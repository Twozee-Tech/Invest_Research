"""Parse LLM response for Wheel Strategy options decisions.

Replaces vertical-spread OpenInstruction/CloseInstruction/RollInstruction
with WheelAction / WheelDecision suited for the cash-secured-put → covered-call wheel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import structlog

logger = structlog.get_logger()

VALID_ACTION_TYPES = {"SELL_CSP", "SELL_CC", "CLOSE", "SKIP"}
VALID_OUTLOOKS = {
    "BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL", "CAUTIOUSLY_BEARISH", "BEARISH"
}


@dataclass
class WheelAction:
    """A single wheel-strategy action decided by the LLM."""
    type: str                    # SELL_CSP | SELL_CC | CLOSE | SKIP
    symbol: str
    strike: float = 0.0
    expiration: str = ""         # YYYY-MM-DD (informational; selector picks exact date)
    contracts: int = 1
    reason: str = ""
    position_id: int | None = None   # for SELL_CC (assigned stock pos) and CLOSE


@dataclass
class WheelDecision:
    """Top-level decision returned from the LLM after parsing."""
    actions: list[WheelAction] = field(default_factory=list)
    outlook: str = "NEUTRAL"
    confidence: float = 0.7
    market_comment: str = ""

    # ── Compatibility shims for main.py ──────────────────────────────────────
    # main.py log/audit code references:
    #   options_decision.open_new, .close_positions, .roll_positions, .portfolio_outlook
    # We expose those as properties so the existing orchestrator keeps working
    # without modification.

    @property
    def open_new(self) -> list[WheelAction]:
        """SELL_CSP + SELL_CC actions map to 'open_new' in orchestrator logging."""
        return [a for a in self.actions if a.type in ("SELL_CSP", "SELL_CC")]

    @property
    def close_positions(self) -> list[WheelAction]:
        """CLOSE actions map to 'close_positions' in orchestrator logging."""
        return [a for a in self.actions if a.type == "CLOSE"]

    @property
    def roll_positions(self) -> list:
        """Wheel strategy has no rolls; return empty list for compatibility."""
        return []

    @property
    def portfolio_outlook(self) -> str:
        """Alias for .outlook used by orchestrator logging."""
        return self.outlook


def parse_options_decision(raw: dict) -> WheelDecision:
    """Parse and normalise LLM wheel-strategy JSON into a WheelDecision.

    Expected JSON shape from the LLM::

        {
          "market_comment": "...",
          "outlook": "NEUTRAL",
          "confidence": 0.72,
          "actions": [
            {"type": "SELL_CSP", "symbol": "AAPL", "contracts": 1, "reason": "..."},
            {"type": "SELL_CC",  "symbol": "MSFT", "contracts": 1,
             "position_id": 42, "reason": "..."},
            {"type": "CLOSE",    "symbol": "SPY",  "position_id": 7, "reason": "..."},
            {"type": "SKIP",     "symbol": "TSLA", "reason": "earnings too close"}
          ]
        }
    """
    if not isinstance(raw, dict):
        logger.warning("wheel_decision_not_dict", type=type(raw).__name__)
        return WheelDecision()

    market_comment = str(raw.get("market_comment", raw.get("reasoning", "")))

    # outlook
    outlook = str(raw.get("outlook", raw.get("portfolio_outlook", "NEUTRAL"))).upper().replace(" ", "_")
    if outlook not in VALID_OUTLOOKS:
        outlook = "NEUTRAL"

    # confidence
    try:
        confidence = float(raw.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.7

    # actions
    actions: list[WheelAction] = []

    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raw_actions = []

    for item in raw_actions:
        if not isinstance(item, dict):
            continue

        action_type = str(item.get("type", "")).upper().strip()
        if action_type not in VALID_ACTION_TYPES:
            logger.warning("wheel_unknown_action_type", raw_type=action_type)
            continue

        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol and action_type != "SKIP":
            logger.warning("wheel_action_missing_symbol", action_type=action_type)
            continue

        # strike (optional — selector will determine it)
        try:
            strike = float(item.get("strike", 0.0) or 0.0)
        except (TypeError, ValueError):
            strike = 0.0

        # expiration (optional hint)
        expiration = str(item.get("expiration", "")).strip()

        # contracts
        try:
            contracts = int(item.get("contracts", 1) or 1)
            contracts = max(1, contracts)
        except (TypeError, ValueError):
            contracts = 1

        reason = str(item.get("reason", ""))

        # position_id (required for CLOSE and SELL_CC to reference existing position)
        position_id: int | None = None
        raw_pid = item.get("position_id")
        if raw_pid is not None:
            try:
                position_id = int(raw_pid)
            except (TypeError, ValueError):
                position_id = None

        if action_type == "CLOSE" and position_id is None:
            logger.warning("wheel_close_missing_position_id", symbol=symbol)
            continue

        actions.append(WheelAction(
            type=action_type,
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            contracts=contracts,
            reason=reason,
            position_id=position_id,
        ))

    logger.info(
        "wheel_decision_parsed",
        total_actions=len(actions),
        sell_csps=sum(1 for a in actions if a.type == "SELL_CSP"),
        sell_ccs=sum(1 for a in actions if a.type == "SELL_CC"),
        closes=sum(1 for a in actions if a.type == "CLOSE"),
        outlook=outlook,
        confidence=confidence,
    )

    return WheelDecision(
        actions=actions,
        outlook=outlook,
        confidence=confidence,
        market_comment=market_comment,
    )
