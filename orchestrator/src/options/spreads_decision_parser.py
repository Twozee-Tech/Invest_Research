"""Parse LLM response for multi-leg spread decisions.

Handles: iron condors, bull/bear call/put spreads, butterflies.
Action types: OPEN_SPREAD, CLOSE, SKIP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import structlog

logger = structlog.get_logger()

VALID_ACTION_TYPES = {"OPEN_SPREAD", "CLOSE", "SKIP"}
VALID_SPREAD_TYPES = {
    "iron_condor", "bull_call", "bear_put",
    "bull_put", "bear_call", "butterfly",
}
VALID_OUTLOOKS = {
    "BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL", "CAUTIOUSLY_BEARISH", "BEARISH"
}


@dataclass
class SpreadAction:
    """A single spread action decided by the LLM."""
    type: str               # OPEN_SPREAD | CLOSE | SKIP
    symbol: str
    spread_type: str = ""   # iron_condor | bull_call | bear_put | bull_put | bear_call | butterfly
    contracts: int = 1
    reason: str = ""
    position_id: int | None = None  # for CLOSE


@dataclass
class SpreadDecision:
    """Top-level decision returned from the LLM after parsing."""
    actions: list[SpreadAction] = field(default_factory=list)
    outlook: str = "NEUTRAL"
    confidence: float = 0.7
    market_comment: str = ""

    @property
    def open_new(self) -> list[SpreadAction]:
        return [a for a in self.actions if a.type == "OPEN_SPREAD"]

    @property
    def close_positions(self) -> list[SpreadAction]:
        return [a for a in self.actions if a.type == "CLOSE"]

    @property
    def roll_positions(self) -> list:
        return []

    @property
    def portfolio_outlook(self) -> str:
        return self.outlook


def parse_spreads_decision(raw: dict) -> SpreadDecision:
    """Parse and normalise LLM spread-strategy JSON into a SpreadDecision.

    Expected JSON shape::

        {
          "market_comment": "...",
          "outlook": "NEUTRAL",
          "confidence": 0.72,
          "actions": [
            {"type": "OPEN_SPREAD", "symbol": "AAPL", "spread_type": "iron_condor",
             "contracts": 1, "reason": "..."},
            {"type": "CLOSE", "symbol": "SPY", "position_id": 7, "reason": "..."},
            {"type": "SKIP", "symbol": "TSLA", "reason": "earnings too close"}
          ]
        }
    """
    if not isinstance(raw, dict):
        logger.warning("spreads_decision_not_dict", type=type(raw).__name__)
        return SpreadDecision()

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
    actions: list[SpreadAction] = []

    raw_actions = raw.get("actions", [])
    if not isinstance(raw_actions, list):
        raw_actions = []

    for item in raw_actions:
        if not isinstance(item, dict):
            continue

        action_type = str(item.get("type", "")).upper().strip()
        if action_type not in VALID_ACTION_TYPES:
            logger.warning("spreads_unknown_action_type", raw_type=action_type)
            continue

        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol and action_type != "SKIP":
            logger.warning("spreads_action_missing_symbol", action_type=action_type)
            continue

        # spread_type (required for OPEN_SPREAD)
        spread_type = str(item.get("spread_type", "")).lower().strip()
        if action_type == "OPEN_SPREAD" and spread_type not in VALID_SPREAD_TYPES:
            logger.warning("spreads_invalid_spread_type", raw_type=spread_type, symbol=symbol)
            continue

        # contracts
        try:
            contracts = int(item.get("contracts", 1) or 1)
            contracts = max(1, contracts)
        except (TypeError, ValueError):
            contracts = 1

        reason = str(item.get("reason", ""))

        # position_id (required for CLOSE)
        position_id: int | None = None
        raw_pid = item.get("position_id")
        if raw_pid is not None:
            try:
                position_id = int(raw_pid)
            except (TypeError, ValueError):
                position_id = None

        if action_type == "CLOSE" and position_id is None:
            logger.warning("spreads_close_missing_position_id", symbol=symbol)
            continue

        actions.append(SpreadAction(
            type=action_type,
            symbol=symbol,
            spread_type=spread_type,
            contracts=contracts,
            reason=reason,
            position_id=position_id,
        ))

    logger.info(
        "spreads_decision_parsed",
        total_actions=len(actions),
        opens=sum(1 for a in actions if a.type == "OPEN_SPREAD"),
        closes=sum(1 for a in actions if a.type == "CLOSE"),
        outlook=outlook,
        confidence=confidence,
    )

    return SpreadDecision(
        actions=actions,
        outlook=outlook,
        confidence=confidence,
        market_comment=market_comment,
    )
