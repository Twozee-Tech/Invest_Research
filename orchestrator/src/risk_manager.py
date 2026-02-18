"""Risk manager: validates and modifies trade decisions against hard rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import structlog

from .decision_parser import TradeAction, DecisionResult
from .portfolio_state import PortfolioState
from .market_data import StockQuote

logger = structlog.get_logger()

MIN_PRICE = 5.0
MIN_AVG_DAILY_VOLUME_USD = 100_000
MAX_PORTFOLIO_DRAWDOWN_PCT = -20.0


@dataclass
class RiskCheckResult:
    """Result of risk validation for a single action."""
    action: TradeAction
    approved: bool = True
    modified: bool = False
    original_amount: float = 0.0
    rejection_reason: str = ""
    modification_reason: str = ""


@dataclass
class RiskManagerResult:
    """Full result of risk validation pass."""
    approved_actions: list[TradeAction] = field(default_factory=list)
    rejected_actions: list[RiskCheckResult] = field(default_factory=list)
    forced_actions: list[TradeAction] = field(default_factory=list)
    modifications: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class RiskManager:
    """Validates trade decisions against account-specific risk rules."""

    def __init__(self, risk_profile: dict):
        self.max_position_pct = risk_profile.get("max_position_pct", 20)
        self.min_cash_pct = risk_profile.get("min_cash_pct", 10)
        self.max_trades_per_cycle = risk_profile.get("max_trades_per_cycle", 5)
        self.stop_loss_pct = risk_profile.get("stop_loss_pct", -15)
        self.min_holding_days = risk_profile.get("min_holding_days", 14)
        self.max_sector_exposure_pct = risk_profile.get("max_sector_exposure_pct", 40)

    def validate(
        self,
        decision: DecisionResult,
        portfolio: PortfolioState,
        quotes: dict[str, StockQuote],
        order_history: list[dict] | None = None,
    ) -> RiskManagerResult:
        """Run all risk checks on the decision.

        Order of operations:
          1. Check for forced stop-loss sells
          2. Check portfolio-level drawdown
          3. Validate each action against rules
          4. Trim to max trades (drop lowest urgency first)
        """
        result = RiskManagerResult()

        # 1. Check stop-losses BEFORE model actions
        forced_sells = self._check_stop_losses(portfolio)
        result.forced_actions.extend(forced_sells)
        if forced_sells:
            symbols = [a.symbol for a in forced_sells]
            result.warnings.append(f"STOP-LOSS triggered for: {', '.join(symbols)}")

        # 2. Check portfolio drawdown
        if portfolio.total_pl_pct <= MAX_PORTFOLIO_DRAWDOWN_PCT:
            result.warnings.append(
                f"CRITICAL: Portfolio drawdown {portfolio.total_pl_pct:.1f}% exceeds "
                f"{MAX_PORTFOLIO_DRAWDOWN_PCT}% threshold. Forcing 50% exposure reduction."
            )
            forced_reduce = self._force_reduce_exposure(portfolio, 0.5)
            result.forced_actions.extend(forced_reduce)

        # 3. Validate each model action
        validated = []
        for action in decision.actions:
            check = self._validate_action(action, portfolio, quotes, order_history)
            if check.approved:
                validated.append(check)
            else:
                result.rejected_actions.append(check)
                result.modifications.append(
                    f"REJECTED {action.type} {action.symbol} ${action.amount_usd:.0f}: "
                    f"{check.rejection_reason}"
                )

        # 4. Trim to max trades per cycle (keep highest urgency)
        urgency_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        validated.sort(key=lambda c: urgency_order.get(c.action.urgency, 1))

        for check in validated[:self.max_trades_per_cycle]:
            result.approved_actions.append(check.action)
            if check.modified:
                result.modifications.append(
                    f"MODIFIED {check.action.type} {check.action.symbol}: "
                    f"${check.original_amount:.0f} -> ${check.action.amount_usd:.0f} "
                    f"({check.modification_reason})"
                )

        for check in validated[self.max_trades_per_cycle:]:
            check.approved = False
            check.rejection_reason = f"Exceeds max {self.max_trades_per_cycle} trades/cycle"
            result.rejected_actions.append(check)
            result.modifications.append(
                f"REJECTED {check.action.type} {check.action.symbol}: max trades exceeded"
            )

        logger.info(
            "risk_validation_complete",
            approved=len(result.approved_actions),
            rejected=len(result.rejected_actions),
            forced=len(result.forced_actions),
            warnings=len(result.warnings),
        )
        return result

    def _validate_action(
        self,
        action: TradeAction,
        portfolio: PortfolioState,
        quotes: dict[str, StockQuote],
        order_history: list[dict] | None,
    ) -> RiskCheckResult:
        """Validate a single action against all rules."""
        check = RiskCheckResult(action=action, original_amount=action.amount_usd)
        quote = quotes.get(action.symbol)

        # Rule: No penny stocks
        if quote and quote.price < MIN_PRICE:
            check.approved = False
            check.rejection_reason = f"Price ${quote.price:.2f} below ${MIN_PRICE} minimum"
            return check

        # Rule: Minimum liquidity
        if quote and action.type == "BUY":
            avg_vol_usd = quote.avg_volume_10d * quote.price
            if avg_vol_usd < MIN_AVG_DAILY_VOLUME_USD:
                check.approved = False
                check.rejection_reason = (
                    f"Avg daily volume ${avg_vol_usd:,.0f} below "
                    f"${MIN_AVG_DAILY_VOLUME_USD:,.0f} minimum"
                )
                return check

        if action.type == "BUY":
            return self._validate_buy(action, check, portfolio, quote)
        elif action.type == "SELL":
            return self._validate_sell(action, check, portfolio, order_history)

        return check

    def _validate_buy(
        self,
        action: TradeAction,
        check: RiskCheckResult,
        portfolio: PortfolioState,
        quote: StockQuote | None,
    ) -> RiskCheckResult:
        """Validate a BUY action."""
        min_cash = portfolio.total_value * self.min_cash_pct / 100
        max_investable = max(0, portfolio.cash - min_cash)

        # Rule: Cash after BUY >= min_cash_pct
        if action.amount_usd > max_investable:
            if max_investable <= 0:
                check.approved = False
                check.rejection_reason = (
                    f"Insufficient cash. Available: ${portfolio.cash:,.2f}, "
                    f"min reserve: ${min_cash:,.2f}"
                )
                return check
            # Trim amount
            check.action = TradeAction(
                type=action.type,
                symbol=action.symbol,
                amount_usd=max_investable,
                urgency=action.urgency,
                thesis=action.thesis,
                exit_condition=action.exit_condition,
            )
            check.modified = True
            check.modification_reason = f"Trimmed to respect {self.min_cash_pct}% cash reserve"

        # Rule: Position size <= max_position_pct
        existing_position = portfolio.get_position(action.symbol)
        existing_value = existing_position.market_value if existing_position else 0
        new_total = existing_value + check.action.amount_usd
        max_position_value = portfolio.total_value * self.max_position_pct / 100

        if new_total > max_position_value:
            allowed = max(0, max_position_value - existing_value)
            if allowed <= 0:
                check.approved = False
                check.rejection_reason = (
                    f"Position already at {existing_value / portfolio.total_value * 100:.1f}% "
                    f"(max {self.max_position_pct}%)"
                )
                return check
            check.action = TradeAction(
                type=action.type,
                symbol=action.symbol,
                amount_usd=allowed,
                urgency=action.urgency,
                thesis=action.thesis,
                exit_condition=action.exit_condition,
            )
            check.modified = True
            check.modification_reason = f"Trimmed to respect {self.max_position_pct}% max position"

        return check

    def _validate_sell(
        self,
        action: TradeAction,
        check: RiskCheckResult,
        portfolio: PortfolioState,
        order_history: list[dict] | None,
    ) -> RiskCheckResult:
        """Validate a SELL action."""
        position = portfolio.get_position(action.symbol)

        # Rule: Must hold the position
        if not position or position.quantity <= 0:
            check.approved = False
            check.rejection_reason = f"No position in {action.symbol} to sell"
            return check

        # Rule: Can't sell more than we have
        if action.amount_usd > position.market_value:
            check.action = TradeAction(
                type=action.type,
                symbol=action.symbol,
                amount_usd=position.market_value,
                urgency=action.urgency,
                thesis=action.thesis,
                exit_condition=action.exit_condition,
            )
            check.modified = True
            check.modification_reason = "Trimmed to actual position value"

        # Rule: Minimum holding period
        if position.first_buy_date and self.min_holding_days > 0:
            try:
                buy_date = datetime.fromisoformat(position.first_buy_date.replace("Z", "+00:00"))
                days_held = (datetime.now(buy_date.tzinfo) - buy_date).days
                if days_held < self.min_holding_days:
                    check.approved = False
                    check.rejection_reason = (
                        f"Held {days_held} days, minimum is {self.min_holding_days} days"
                    )
                    return check
            except (ValueError, TypeError):
                pass  # Can't parse date, skip this check

        return check

    def _check_stop_losses(self, portfolio: PortfolioState) -> list[TradeAction]:
        """Check all positions for stop-loss triggers."""
        forced = []
        for position in portfolio.positions:
            if position.unrealized_pl_pct <= self.stop_loss_pct:
                forced.append(TradeAction(
                    type="SELL",
                    symbol=position.symbol,
                    amount_usd=position.market_value,
                    urgency="HIGH",
                    thesis=f"STOP-LOSS: Position at {position.unrealized_pl_pct:+.1f}% "
                           f"(threshold: {self.stop_loss_pct}%)",
                    exit_condition="Immediate stop-loss execution",
                ))
                logger.warning(
                    "stop_loss_triggered",
                    symbol=position.symbol,
                    pl_pct=position.unrealized_pl_pct,
                    threshold=self.stop_loss_pct,
                )
        return forced

    def _force_reduce_exposure(
        self,
        portfolio: PortfolioState,
        reduction_factor: float,
    ) -> list[TradeAction]:
        """Force sell positions to reduce exposure."""
        forced = []
        for position in sorted(portfolio.positions, key=lambda p: p.unrealized_pl_pct):
            sell_amount = position.market_value * reduction_factor
            if sell_amount > 10:
                forced.append(TradeAction(
                    type="SELL",
                    symbol=position.symbol,
                    amount_usd=sell_amount,
                    urgency="HIGH",
                    thesis=f"FORCED REDUCTION: Portfolio drawdown exceeds "
                           f"{MAX_PORTFOLIO_DRAWDOWN_PCT}% threshold",
                    exit_condition="Emergency risk reduction",
                ))
        return forced
