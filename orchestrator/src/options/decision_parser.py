"""Parse LLM response for options trading decisions."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

VALID_SPREAD_TYPES = {"BULL_CALL", "BEAR_PUT"}
VALID_DIRECTIONS = {"bullish", "bearish", "neutral"}
VALID_SIZES = {"small", "medium", "large"}
SIZE_TO_CONTRACTS = {"small": 1, "medium": 2, "large": 3}


@dataclass
class CloseInstruction:
    position_id: int
    reason: str = ""


@dataclass
class RollInstruction:
    position_id: int
    direction: str = "neutral"
    spread_type: str = ""


@dataclass
class OpenInstruction:
    symbol: str
    direction: str              # bullish | bearish | neutral
    spread_type: str            # BULL_CALL | BEAR_PUT
    contracts: int = 1          # resolved from size
    thesis: str = ""


@dataclass
class OptionsDecision:
    reasoning: str = ""
    hold_positions: list[int] = field(default_factory=list)
    close_positions: list[CloseInstruction] = field(default_factory=list)
    roll_positions: list[RollInstruction] = field(default_factory=list)
    open_new: list[OpenInstruction] = field(default_factory=list)
    portfolio_outlook: str = "NEUTRAL"
    confidence: float = 0.5


def parse_options_decision(raw: dict) -> OptionsDecision:
    """Parse and normalize LLM options decision JSON."""
    if not isinstance(raw, dict):
        logger.warning("options_decision_not_dict", type=type(raw).__name__)
        return OptionsDecision()

    reasoning = str(raw.get("reasoning", ""))

    # hold_positions: list of ints
    holds_raw = raw.get("hold_positions", [])
    holds = []
    if isinstance(holds_raw, list):
        for item in holds_raw:
            try:
                holds.append(int(item))
            except (TypeError, ValueError):
                pass

    # close_positions
    closes = []
    for item in (raw.get("close_positions") or []):
        if isinstance(item, dict):
            try:
                pos_id = int(item.get("id", 0))
                if pos_id > 0:
                    closes.append(CloseInstruction(
                        position_id=pos_id,
                        reason=str(item.get("reason", "")),
                    ))
            except (TypeError, ValueError):
                pass
        elif isinstance(item, (int, float)):
            closes.append(CloseInstruction(position_id=int(item)))

    # roll_positions
    rolls = []
    for item in (raw.get("roll_positions") or []):
        if isinstance(item, dict):
            try:
                pos_id = int(item.get("id", 0))
                if pos_id > 0:
                    direction = str(item.get("direction", "neutral")).lower()
                    spread_type = str(item.get("spread_type", "")).upper()
                    if direction not in VALID_DIRECTIONS:
                        direction = "neutral"
                    if spread_type not in VALID_SPREAD_TYPES:
                        spread_type = _direction_to_default_spread(direction)
                    rolls.append(RollInstruction(
                        position_id=pos_id,
                        direction=direction,
                        spread_type=spread_type,
                    ))
            except (TypeError, ValueError):
                pass

    # open_new
    opens = []
    for item in (raw.get("open_new") or []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        direction = str(item.get("direction", "neutral")).lower()
        spread_type = str(item.get("spread_type", "")).upper()
        size = str(item.get("size", "small")).lower()

        if direction not in VALID_DIRECTIONS:
            direction = "neutral"
        if spread_type not in VALID_SPREAD_TYPES:
            spread_type = _direction_to_default_spread(direction)
        if size not in VALID_SIZES:
            size = "small"

        contracts = SIZE_TO_CONTRACTS[size]
        opens.append(OpenInstruction(
            symbol=symbol,
            direction=direction,
            spread_type=spread_type,
            contracts=contracts,
            thesis=str(item.get("thesis", "")),
        ))

    # portfolio_outlook
    outlook = str(raw.get("portfolio_outlook", "NEUTRAL")).upper().replace(" ", "_")
    valid_outlooks = {"BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL", "CAUTIOUSLY_BEARISH", "BEARISH"}
    if outlook not in valid_outlooks:
        outlook = "NEUTRAL"

    # confidence
    try:
        confidence = float(raw.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    return OptionsDecision(
        reasoning=reasoning,
        hold_positions=holds,
        close_positions=closes,
        roll_positions=rolls,
        open_new=opens,
        portfolio_outlook=outlook,
        confidence=confidence,
    )


def _direction_to_default_spread(direction: str) -> str:
    """Map direction to a sensible default spread type."""
    if direction == "bullish":
        return "BULL_CALL"
    elif direction == "bearish":
        return "BEAR_PUT"
    return "BEAR_PUT"  # neutral → default to BEAR_PUT (premium selling)
