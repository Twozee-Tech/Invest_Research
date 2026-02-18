"""Main entry point: scheduler + full decision cycle orchestration."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import structlog
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .account_manager import AccountManager
from .audit_logger import AuditLogger
from .decision_parser import parse_analysis, parse_decision
from .ghostfolio_client import GhostfolioClient
from .llm_client import LLMClient
from .market_data import MarketDataProvider
from .news_fetcher import NewsFetcher
from .portfolio_state import get_portfolio_state
from .prompt_builder import build_pass1_messages, build_pass2_messages, format_decision_history
from .risk_manager import RiskManager, RiskManagerResult
from .technical_indicators import compute_indicators
from .trade_executor import TradeExecutor

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


class Orchestrator:
    """Main orchestrator that runs the full decision cycle for an account."""

    def __init__(self, config_path: str = "config.yaml", dry_run: bool = False):
        self.config_path = config_path
        self.dry_run = dry_run
        self._load_config()

        self.ghostfolio = GhostfolioClient()
        self.llm = LLMClient()
        self.market_data = MarketDataProvider()
        self.news = NewsFetcher()
        self.audit = AuditLogger()
        self.account_mgr = AccountManager(config_path=config_path, client=self.ghostfolio)

    def _load_config(self) -> None:
        with open(self.config_path) as f:
            self.config = yaml.safe_load(f)

    def run_cycle(self, account_key: str) -> None:
        """Run a full decision cycle for one account.

        Phases:
          1. Gather context (portfolio, market data, news, indicators)
          2. LLM Pass 1: Market analysis
          3. LLM Pass 2: Trading decisions
          4. Risk validation
          5. Trade execution
          6. Audit logging
        """
        self._load_config()  # Reload in case config changed
        acct = self.config.get("accounts", {}).get(account_key)
        if not acct:
            logger.error("account_not_found", key=account_key)
            return

        account_name = acct.get("name", account_key)
        account_id = acct.get("ghostfolio_account_id", "")
        model = acct.get("model", "Nemotron")
        fallback = acct.get("fallback_model")
        risk_profile = acct.get("risk_profile", {})
        watchlist = acct.get("watchlist", [])

        logger.info("cycle_start", account=account_name, model=model, dry_run=self.dry_run)
        error_msg = None

        try:
            # ===== PHASE 1: GATHER CONTEXT =====
            logger.info("phase1_gathering_context", account=account_name)

            portfolio = get_portfolio_state(self.ghostfolio, account_id, account_name)
            portfolio_before = {
                "total_value": portfolio.total_value,
                "cash": portfolio.cash,
                "positions": portfolio.position_count,
                "total_pl_pct": portfolio.total_pl_pct,
            }

            # Market data for watchlist
            quotes = self.market_data.get_quotes_batch(watchlist)
            market_data = {}
            for sym, q in quotes.items():
                market_data[sym] = {
                    "price": q.price,
                    "change_pct": q.change_pct,
                    "pe": q.pe_ratio,
                    "div_yield": q.dividend_yield,
                    "52w_high": q.week52_high,
                    "52w_low": q.week52_low,
                    "sector": q.sector,
                }

            # Technical indicators
            tech_signals = {}
            for sym in watchlist:
                try:
                    df = self.market_data.get_history(sym, period="6mo")
                    if not df.empty:
                        tech_signals[sym] = compute_indicators(df, sym)
                except Exception as e:
                    logger.warning("indicators_failed", symbol=sym, error=str(e))

            # News
            news_items = self.news.fetch_relevant_news(watchlist, max_items=10)
            news_text = self.news.format_for_prompt(news_items)

            # Decision history
            history = self.audit.get_decision_history(account_key, limit=4)
            history_text = format_decision_history(history)

            # ===== PHASE 2: LLM PASS 1 - ANALYSIS =====
            logger.info("phase2_llm_analysis", account=account_name, model=model)

            pass1_messages = build_pass1_messages(
                portfolio=portfolio,
                market_data=market_data,
                technical_signals=tech_signals,
                news_text=news_text,
                decision_history=history_text,
                strategy_config=acct,
            )

            analysis_raw = self.llm.chat_json(
                messages=pass1_messages,
                model=model,
                fallback_model=fallback,
                temperature=0.7,
            )
            analysis = parse_analysis(analysis_raw)
            logger.info(
                "analysis_complete",
                regime=analysis.market_regime,
                opportunities=len(analysis.opportunities),
            )

            # ===== PHASE 3: LLM PASS 2 - DECISIONS =====
            logger.info("phase3_llm_decisions", account=account_name, model=model)

            pass2_messages = build_pass2_messages(
                analysis_json=analysis_raw,
                portfolio=portfolio,
                strategy_config=acct,
                risk_profile=risk_profile,
            )

            decision_raw = self.llm.chat_json(
                messages=pass2_messages,
                model=model,
                fallback_model=fallback,
                temperature=0.5,
            )
            decision = parse_decision(decision_raw)
            logger.info(
                "decision_complete",
                actions=len(decision.actions),
                outlook=decision.portfolio_outlook,
                confidence=decision.confidence,
            )

            # ===== PHASE 4: RISK VALIDATION =====
            logger.info("phase4_risk_validation", account=account_name)

            risk_mgr = RiskManager(risk_profile)
            risk_result: RiskManagerResult = risk_mgr.validate(
                decision=decision,
                portfolio=portfolio,
                quotes={s: q for s, q in quotes.items()},
            )

            for w in risk_result.warnings:
                logger.warning("risk_warning", account=account_name, warning=w)
            for m in risk_result.modifications:
                logger.info("risk_modification", account=account_name, mod=m)

            # ===== PHASE 5: TRADE EXECUTION =====
            all_actions = risk_result.forced_actions + risk_result.approved_actions
            executed_trades = []

            if all_actions:
                logger.info(
                    "phase5_executing_trades",
                    account=account_name,
                    count=len(all_actions),
                    dry_run=self.dry_run,
                )
                executor = TradeExecutor(
                    self.ghostfolio, self.market_data, dry_run=self.dry_run,
                )
                results = executor.execute_trades(all_actions, account_id)

                for r in results:
                    executed_trades.append({
                        "type": r.action.type,
                        "symbol": r.action.symbol,
                        "quantity": r.quantity,
                        "price": r.unit_price,
                        "total": r.total_cost,
                        "success": r.success,
                        "error": r.error,
                        "order_id": r.ghostfolio_order_id,
                    })
                    if r.success:
                        logger.info(
                            "trade_ok",
                            symbol=r.action.symbol,
                            type=r.action.type,
                            qty=round(r.quantity, 4),
                            price=r.unit_price,
                        )
                    else:
                        logger.error("trade_failed", symbol=r.action.symbol, error=r.error)

                # Verify orders
                verify_warnings = executor.verify_orders(results)
                for vw in verify_warnings:
                    logger.warning("order_verification", warning=vw)
            else:
                logger.info("phase5_no_trades", account=account_name, reason="No actions to execute")

            # Get portfolio state after trades
            portfolio_after_state = get_portfolio_state(self.ghostfolio, account_id, account_name)
            portfolio_after = {
                "total_value": portfolio_after_state.total_value,
                "cash": portfolio_after_state.cash,
                "positions": portfolio_after_state.position_count,
                "total_pl_pct": portfolio_after_state.total_pl_pct,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error("cycle_failed", account=account_name, error=error_msg, exc_info=True)
            analysis_raw = {}
            decision_raw = {}
            pass1_messages = []
            pass2_messages = []
            risk_result = RiskManagerResult()
            executed_trades = []
            portfolio_before = {}
            portfolio_after = {}

        # ===== PHASE 6: AUDIT LOGGING =====
        log_file = self.audit.log_cycle(
            account_key=account_key,
            account_name=account_name,
            model=model,
            pass1_messages=pass1_messages,
            pass1_response=analysis_raw,
            pass2_messages=pass2_messages,
            pass2_response=decision_raw,
            risk_modifications=risk_result.modifications,
            risk_warnings=risk_result.warnings,
            forced_actions=[
                {"type": a.type, "symbol": a.symbol, "amount": a.amount_usd, "thesis": a.thesis}
                for a in risk_result.forced_actions
            ],
            rejected_actions=[
                {"symbol": r.action.symbol, "reason": r.rejection_reason}
                for r in risk_result.rejected_actions
            ],
            executed_trades=executed_trades,
            portfolio_before=portfolio_before,
            portfolio_after=portfolio_after,
            error=error_msg,
        )

        status = "ERROR" if error_msg else "OK"
        logger.info("cycle_complete", account=account_name, status=status, log=log_file)


def parse_cron(cron_str: str) -> dict:
    """Parse cron string into APScheduler trigger kwargs."""
    parts = cron_str.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron: {cron_str}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def main():
    parser = argparse.ArgumentParser(description="AI Investment Orchestrator")
    parser.add_argument("--once", type=str, help="Run single cycle for account key, then exit")
    parser.add_argument("--all", action="store_true", help="Run all accounts once, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Don't execute real trades")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    orch = Orchestrator(config_path=args.config, dry_run=args.dry_run)

    # Ensure all accounts exist in Ghostfolio
    logger.info("ensuring_accounts_exist")
    orch.account_mgr.ensure_accounts_exist()

    if args.once:
        logger.info("running_single_cycle", account=args.once)
        orch.run_cycle(args.once)
        return

    if args.all:
        logger.info("running_all_accounts")
        for key in orch.config.get("accounts", {}):
            orch.run_cycle(key)
        return

    # Scheduled mode: set up cron jobs for each account
    scheduler = BlockingScheduler()

    for key, acct in orch.config.get("accounts", {}).items():
        cron_str = acct.get("cron", "0 20 * * 0")
        try:
            cron_kwargs = parse_cron(cron_str)
            trigger = CronTrigger(**cron_kwargs)
            scheduler.add_job(
                orch.run_cycle,
                trigger=trigger,
                args=[key],
                id=f"cycle_{key}",
                name=f"Decision cycle: {acct.get('name', key)}",
                misfire_grace_time=3600,
            )
            logger.info(
                "scheduler_job_added",
                account=acct.get("name", key),
                cron=cron_str,
            )
        except Exception as e:
            logger.error("scheduler_setup_failed", account=key, error=str(e))

    # Graceful shutdown
    def shutdown(signum, frame):
        logger.info("shutting_down")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("scheduler_starting", jobs=len(scheduler.get_jobs()))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("scheduler_stopped")


if __name__ == "__main__":
    main()
