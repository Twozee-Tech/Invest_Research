"""Options-specific risk manager: validates open/close decisions and applies auto-rules."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from ..portfolio_state import PortfolioState
from .decision_parser import CloseInstruction, OpenInstruction, OptionsDecision, RollInstruction
from .greeks import PortfolioGreeks
from .positions import OptionsPosition

logger = structlog.get_logger()


@dataclass
class OptionsRiskResult:
    approved_opens: list[OpenInstruction] = field(default_factory=list)
    rejected_opens: list[dict] = field(default_factory=list)    # {instruction, reason}
    approved_closes: list[CloseInstruction] = field(default_factory=list)
    forced_closes: list[CloseInstruction] = field(default_factory=list)   # auto-close rules
    approved_rolls: list[RollInstruction] = field(default_factory=list)
    modifications: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class OptionsRiskManager:
    """Validates options decisions against per-account risk rules."""

    def __init__(self, risk_profile: dict):
        self.max_portfolio_delta_pct = risk_profile.get("max_portfolio_delta_pct", 15)
        self.min_cash_pct = risk_profile.get("min_cash_pct", 40)
        self.max_open_spreads = risk_profile.get("max_open_spreads", 5)
        self.max_allocation_pct = risk_profile.get("max_allocation_per_spread_pct", 10)
        self.min_new_dte = risk_profile.get("min_new_position_dte", 21)
        self.auto_close_dte = risk_profile.get("auto_close_dte", 7)
        self.take_profit_pct = risk_profile.get("take_profit_pct", 75)
        self.stop_loss_pct = risk_profile.get("stop_loss_pct", 50)

    def validate(
        self,
        decision: OptionsDecision,
        active_positions: list[OptionsPosition],
        portfolio: PortfolioState,
        portfolio_greeks: PortfolioGreeks,
    ) -> OptionsRiskResult:
        result = OptionsRiskResult()
        account_value = portfolio.total_value or 10000

        # ── Step 1: Auto-close rules (independent of LLM decision) ──────────
        llm_close_ids = {c.position_id for c in decision.close_positions}
        llm_roll_ids = {r.position_id for r in decision.roll_positions}

        for pos in active_positions:
            if pos.id in llm_close_ids or pos.id in llm_roll_ids:
                continue  # LLM already handling it

            # DTE auto-close
            if pos.dte is not None and pos.dte <= self.auto_close_dte:
                reason = f"DTE={pos.dte} ≤ auto_close threshold ({self.auto_close_dte})"
                result.forced_closes.append(CloseInstruction(pos.id, reason))
                result.modifications.append(f"[AUTO-CLOSE] {pos.symbol} {pos.spread_type}: {reason}")
                logger.info("options_auto_close_dte", pos_id=pos.id, dte=pos.dte)
                continue

            # Take profit
            captured = pos.profit_captured_pct
            if captured is not None and captured >= self.take_profit_pct:
                reason = f"Take profit: {captured:.0f}% of max profit captured"
                result.forced_closes.append(CloseInstruction(pos.id, reason))
                result.modifications.append(f"[TAKE-PROFIT] {pos.symbol} {pos.spread_type}: {reason}")
                logger.info("options_take_profit", pos_id=pos.id, captured_pct=captured)
                continue

            # Stop loss
            if pos.current_pl is not None and pos.max_loss > 0:
                loss_pct = (-pos.current_pl / pos.max_loss * 100) if pos.current_pl < 0 else 0
                if loss_pct >= self.stop_loss_pct:
                    reason = f"Stop loss: {loss_pct:.0f}% of max loss reached"
                    result.forced_closes.append(CloseInstruction(pos.id, reason))
                    result.modifications.append(f"[STOP-LOSS] {pos.symbol} {pos.spread_type}: {reason}")
                    logger.info("options_stop_loss", pos_id=pos.id, loss_pct=loss_pct)
                    continue

        # ── Step 2: Pass through LLM closes ─────────────────────────────────
        forced_close_ids = {c.position_id for c in result.forced_closes}
        active_ids = {p.id for p in active_positions}

        for close in decision.close_positions:
            if close.position_id not in forced_close_ids:
                if close.position_id in active_ids:
                    result.approved_closes.append(close)
                else:
                    result.warnings.append(f"Close request for unknown position ID: {close.position_id}")

        # ── Step 3: Pass through rolls (only for known, non-closing positions) ──
        closing_ids = forced_close_ids | {c.position_id for c in result.approved_closes}
        for roll in decision.roll_positions:
            if roll.position_id in active_ids and roll.position_id not in closing_ids:
                result.approved_rolls.append(roll)
            else:
                result.warnings.append(f"Roll request for closed/unknown position: {roll.position_id}")

        # ── Step 4: Validate new opens ───────────────────────────────────────
        cash = portfolio.cash
        open_after_closes = len(active_positions) - len(result.forced_closes) - len(result.approved_closes)

        for inst in decision.open_new:
            # Max open spreads limit
            if open_after_closes >= self.max_open_spreads:
                reason = f"Max open spreads ({self.max_open_spreads}) reached"
                result.rejected_opens.append({"instruction": inst, "reason": reason})
                result.modifications.append(f"[REJECTED] Open {inst.symbol} {inst.spread_type}: {reason}")
                continue

            # Cash reserve check (estimate: max_loss = 10% of account per spread)
            estimated_max_loss = account_value * self.max_allocation_pct / 100
            if cash < estimated_max_loss:
                reason = f"Insufficient cash (${cash:.0f}) for new spread (est. max loss ${estimated_max_loss:.0f})"
                result.rejected_opens.append({"instruction": inst, "reason": reason})
                result.modifications.append(f"[REJECTED] Open {inst.symbol}: {reason}")
                continue

            # Min cash pct check
            cash_after = cash - estimated_max_loss
            cash_after_pct = cash_after / account_value * 100
            if cash_after_pct < self.min_cash_pct:
                reason = f"Would breach min cash reserve (would leave {cash_after_pct:.1f}% < {self.min_cash_pct}%)"
                result.rejected_opens.append({"instruction": inst, "reason": reason})
                result.modifications.append(f"[REJECTED] Open {inst.symbol}: {reason}")
                continue

            # Direction must match spread type
            if inst.direction == "bullish" and inst.spread_type == "BEAR_PUT":
                inst.spread_type = "BULL_CALL"
                result.modifications.append(f"[FIX] {inst.symbol}: changed BEAR_PUT → BULL_CALL (bullish direction)")
            elif inst.direction == "bearish" and inst.spread_type == "BULL_CALL":
                inst.spread_type = "BEAR_PUT"
                result.modifications.append(f"[FIX] {inst.symbol}: changed BULL_CALL → BEAR_PUT (bearish direction)")

            result.approved_opens.append(inst)
            open_after_closes += 1
            cash -= estimated_max_loss

        # ── Step 5: Portfolio delta warning ──────────────────────────────────
        if account_value > 0:
            delta_as_pct = abs(portfolio_greeks.total_delta) / account_value * 100
            if delta_as_pct > self.max_portfolio_delta_pct:
                result.warnings.append(
                    f"Portfolio delta ({portfolio_greeks.total_delta:+.2f}) exceeds "
                    f"±{self.max_portfolio_delta_pct}% threshold"
                )

        return result
