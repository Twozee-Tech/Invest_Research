"""Execute options spread trades: SQLite position tracking + Ghostfolio cash flows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import structlog

from ..ghostfolio_client import GhostfolioClient
from ..market_data import MarketDataProvider
from .data import get_option_chain, get_current_option_price
from .decision_parser import CloseInstruction, OpenInstruction, RollInstruction
from .greeks import calculate_spread_greeks
from .positions import OptionsPosition, OptionsPositionTracker
from .selector import SelectedSpread, select_spread

logger = structlog.get_logger()


@dataclass
class OptionsTradeResult:
    action: str              # "OPEN", "CLOSE", "ROLL", "UPDATE"
    symbol: str
    spread_type: str
    position_id: int | None
    success: bool
    realized_pl: float | None = None
    error: str = ""
    ghostfolio_order_id: str | None = None


class OptionsExecutor:
    """Execute open/close/roll decisions for options spreads."""

    def __init__(
        self,
        ghostfolio: GhostfolioClient,
        market_data: MarketDataProvider,
        tracker: OptionsPositionTracker,
        account_id: str,
        risk_profile: dict,
        dry_run: bool = False,
    ):
        self.ghostfolio = ghostfolio
        self.market_data = market_data
        self.tracker = tracker
        self.account_id = account_id
        self.risk_profile = risk_profile
        self.dry_run = dry_run

    # ── Public interface ──────────────────────────────────────────────────────

    def execute_closes(
        self,
        closes: list[CloseInstruction],
        active_positions: list[OptionsPosition],
    ) -> list[OptionsTradeResult]:
        results = []
        pos_map = {p.id: p for p in active_positions}
        for close in closes:
            pos = pos_map.get(close.position_id)
            if pos is None:
                results.append(OptionsTradeResult(
                    action="CLOSE", symbol="?", spread_type="?",
                    position_id=close.position_id, success=False,
                    error=f"Position {close.position_id} not found",
                ))
                continue
            results.append(self._close_position(pos, close.reason))
        return results

    def execute_opens(
        self,
        opens: list[OpenInstruction],
    ) -> list[OptionsTradeResult]:
        results = []
        for inst in opens:
            results.append(self._open_position(inst))
        return results

    def execute_rolls(
        self,
        rolls: list[RollInstruction],
        active_positions: list[OptionsPosition],
    ) -> list[OptionsTradeResult]:
        """Roll = close existing + open new with updated direction."""
        results = []
        pos_map = {p.id: p for p in active_positions}
        for roll in rolls:
            pos = pos_map.get(roll.position_id)
            if pos is None:
                results.append(OptionsTradeResult(
                    action="ROLL", symbol="?", spread_type="?",
                    position_id=roll.position_id, success=False,
                    error=f"Position {roll.position_id} not found for roll",
                ))
                continue
            # Close old leg
            close_result = self._close_position(pos, f"ROLLED → {roll.spread_type}")
            results.append(close_result)
            if not close_result.success:
                continue
            # Open new leg
            new_inst = OpenInstruction(
                symbol=pos.symbol,
                direction=roll.direction,
                spread_type=roll.spread_type,
                contracts=pos.contracts,
                thesis=f"Roll from {pos.spread_type}",
            )
            open_result = self._open_position(new_inst)
            results.append(open_result)
        return results

    def update_active_positions(
        self,
        active_positions: list[OptionsPosition],
    ) -> list[OptionsTradeResult]:
        """Refresh current_value and Greeks for all held positions."""
        results = []
        today = date.today()
        for pos in active_positions:
            result = self._update_position_state(pos, today)
            results.append(result)
        return results

    # ── Internal: open ────────────────────────────────────────────────────────

    def _open_position(self, inst: OpenInstruction) -> OptionsTradeResult:
        try:
            # 1. Select strikes
            spread = select_spread(inst.symbol, inst.spread_type, self.risk_profile)
            if spread is None:
                return OptionsTradeResult(
                    action="OPEN", symbol=inst.symbol, spread_type=inst.spread_type,
                    position_id=None, success=False,
                    error="Strike selection failed (no suitable chain)",
                )

            # 2. Calculate spread Greeks
            greeks = calculate_spread_greeks(
                spread_type=spread.spread_type,
                underlying_price=spread.underlying_price,
                buy_strike=spread.buy_strike,
                sell_strike=spread.sell_strike,
                expiration_date=spread.expiration,
                buy_iv=spread.buy_iv,
                sell_iv=spread.sell_iv,
                buy_premium=spread.buy_premium,
                sell_premium=spread.sell_premium,
                contracts=inst.contracts,
            )

            max_profit = spread.max_profit_per_spread * inst.contracts
            max_loss = spread.max_loss_per_spread * inst.contracts

            # 3. Ghostfolio cash flow (debit paid)
            ghostfolio_order_id = None
            if not self.dry_run:
                ghostfolio_order_id = self._ghostfolio_open(spread, inst.contracts)
            else:
                ghostfolio_order_id = "DRY_RUN"
                logger.info(
                    "options_dry_run_open",
                    symbol=inst.symbol, spread_type=inst.spread_type,
                    buy_strike=spread.buy_strike, sell_strike=spread.sell_strike,
                    net_debit=spread.net_debit, contracts=inst.contracts,
                )

            # 4. SQLite record
            greeks_dict = {}
            if greeks:
                greeks_dict = {
                    "net_delta": greeks.net_delta,
                    "net_gamma": greeks.net_gamma,
                    "net_theta": greeks.net_theta,
                    "net_vega": greeks.net_vega,
                }

            pos_id = self.tracker.open_position(
                account_key=self.account_id,
                symbol=spread.symbol,
                spread_type=spread.spread_type,
                contracts=inst.contracts,
                expiration_date=spread.expiration,
                buy_strike=spread.buy_strike,
                buy_option_type=spread.buy_option_type,
                buy_premium=spread.buy_premium,
                sell_strike=spread.sell_strike,
                sell_option_type=spread.sell_option_type,
                sell_premium=spread.sell_premium,
                max_profit=max_profit,
                max_loss=max_loss,
                entry_debit=spread.net_debit,
                buy_contract_symbol=spread.buy_contract_symbol,
                sell_contract_symbol=spread.sell_contract_symbol,
                ghostfolio_order_id=ghostfolio_order_id,
            )

            # Set initial Greeks state
            if greeks:
                self.tracker.update_position(
                    pos_id,
                    current_value=spread.net_debit,
                    current_pl=0.0,
                    greeks=greeks_dict,
                    dte=spread.dte,
                )

            logger.info(
                "options_opened",
                pos_id=pos_id, symbol=spread.symbol, spread_type=spread.spread_type,
                buy_strike=spread.buy_strike, sell_strike=spread.sell_strike,
                net_debit=spread.net_debit, contracts=inst.contracts,
                max_profit=max_profit, max_loss=max_loss,
            )

            return OptionsTradeResult(
                action="OPEN", symbol=spread.symbol, spread_type=spread.spread_type,
                position_id=pos_id, success=True,
                ghostfolio_order_id=ghostfolio_order_id,
            )

        except Exception as e:
            logger.error("options_open_failed", symbol=inst.symbol, error=str(e), exc_info=True)
            return OptionsTradeResult(
                action="OPEN", symbol=inst.symbol, spread_type=inst.spread_type,
                position_id=None, success=False, error=str(e),
            )

    # ── Internal: close ───────────────────────────────────────────────────────

    def _close_position(self, pos: OptionsPosition, reason: str) -> OptionsTradeResult:
        try:
            # 1. Get current spread value (mid of each leg)
            close_value = self._get_current_spread_value(pos)
            if close_value is None:
                close_value = pos.current_value or pos.entry_debit

            # 2. Ghostfolio cash flow (proceeds received)
            ghostfolio_order_id = None
            if not self.dry_run:
                ghostfolio_order_id = self._ghostfolio_close(pos, close_value)
            else:
                ghostfolio_order_id = "DRY_RUN"
                logger.info(
                    "options_dry_run_close",
                    pos_id=pos.id, symbol=pos.symbol,
                    close_value=close_value, reason=reason,
                )

            # 3. SQLite update
            realized_pl = self.tracker.close_position(
                pos.id, close_value, reason, ghostfolio_order_id,
            )

            return OptionsTradeResult(
                action="CLOSE", symbol=pos.symbol, spread_type=pos.spread_type,
                position_id=pos.id, success=True, realized_pl=realized_pl,
                ghostfolio_order_id=ghostfolio_order_id,
            )

        except Exception as e:
            logger.error("options_close_failed", pos_id=pos.id, error=str(e), exc_info=True)
            return OptionsTradeResult(
                action="CLOSE", symbol=pos.symbol, spread_type=pos.spread_type,
                position_id=pos.id, success=False, error=str(e),
            )

    # ── Internal: update state ────────────────────────────────────────────────

    def _update_position_state(self, pos: OptionsPosition, today: date) -> OptionsTradeResult:
        try:
            from datetime import datetime as dt
            exp_date = dt.strptime(pos.expiration_date, "%Y-%m-%d").date()
            dte = max((exp_date - today).days, 0)

            # Check if expired
            if dte == 0:
                logger.info("options_position_expired", pos_id=pos.id, symbol=pos.symbol)
                self.tracker.expire_position(pos.id)
                return OptionsTradeResult(
                    action="UPDATE", symbol=pos.symbol, spread_type=pos.spread_type,
                    position_id=pos.id, success=True,
                )

            # Current spread value
            current_value = self._get_current_spread_value(pos)
            if current_value is None:
                return OptionsTradeResult(
                    action="UPDATE", symbol=pos.symbol, spread_type=pos.spread_type,
                    position_id=pos.id, success=False, error="Could not fetch current prices",
                )

            current_pl = round((current_value - pos.entry_debit) * pos.contracts * 100, 2)

            # Recalculate Greeks
            underlying_price = self.market_data.get_current_price(pos.symbol)
            greeks_dict = {}
            if underlying_price:
                t = max(dte / 365.0, 0.001)
                from .data import get_current_option_price as _gop
                buy_iv = _get_iv_from_chain(pos.symbol, pos.buy_option_type, pos.buy_strike, pos.expiration_date)
                sell_iv = _get_iv_from_chain(pos.symbol, pos.sell_option_type, pos.sell_strike, pos.expiration_date)

                greeks = calculate_spread_greeks(
                    spread_type=pos.spread_type,
                    underlying_price=underlying_price,
                    buy_strike=pos.buy_strike,
                    sell_strike=pos.sell_strike,
                    expiration_date=pos.expiration_date,
                    buy_iv=buy_iv or 0.25,
                    sell_iv=sell_iv or 0.25,
                    buy_premium=0,
                    sell_premium=0,
                    contracts=pos.contracts,
                )
                if greeks:
                    greeks_dict = {
                        "net_delta": greeks.net_delta,
                        "net_gamma": greeks.net_gamma,
                        "net_theta": greeks.net_theta,
                        "net_vega": greeks.net_vega,
                    }

            self.tracker.update_position(pos.id, current_value, current_pl, greeks_dict, dte)
            return OptionsTradeResult(
                action="UPDATE", symbol=pos.symbol, spread_type=pos.spread_type,
                position_id=pos.id, success=True,
            )

        except Exception as e:
            logger.error("options_update_failed", pos_id=pos.id, error=str(e))
            return OptionsTradeResult(
                action="UPDATE", symbol=pos.symbol, spread_type=pos.spread_type,
                position_id=pos.id, success=False, error=str(e),
            )

    # ── Internal: Ghostfolio ─────────────────────────────────────────────────

    def _ghostfolio_open(self, spread: SelectedSpread, contracts: int) -> str | None:
        """Record cash outflow (debit paid) in Ghostfolio as MANUAL BUY."""
        try:
            symbol = f"OPT-{spread.symbol}-{spread.spread_type}-{spread.expiration.replace('-', '')}"
            result = self.ghostfolio.create_order(
                account_id=self.account_id,
                symbol=symbol,
                order_type="BUY",
                quantity=float(contracts),
                unit_price=spread.net_debit,
                data_source="MANUAL",
            )
            return result.get("id") if isinstance(result, dict) else None
        except Exception as e:
            logger.error("ghostfolio_options_open_failed", error=str(e))
            return None

    def _ghostfolio_close(self, pos: OptionsPosition, close_value: float) -> str | None:
        """Record cash inflow (proceeds) in Ghostfolio as MANUAL SELL."""
        try:
            symbol = f"OPT-{pos.symbol}-{pos.spread_type}-{pos.expiration_date.replace('-', '')}"
            result = self.ghostfolio.create_order(
                account_id=self.account_id,
                symbol=symbol,
                order_type="SELL",
                quantity=float(pos.contracts),
                unit_price=close_value,
                data_source="MANUAL",
            )
            return result.get("id") if isinstance(result, dict) else None
        except Exception as e:
            logger.error("ghostfolio_options_close_failed", error=str(e))
            return None

    # ── Internal: pricing ─────────────────────────────────────────────────────

    def _get_current_spread_value(self, pos: OptionsPosition) -> float | None:
        """Get current mid-price of the spread (buy leg - sell leg)."""
        buy_price = get_current_option_price(
            pos.symbol, pos.buy_option_type, pos.buy_strike, pos.expiration_date,
        )
        sell_price = get_current_option_price(
            pos.symbol, pos.sell_option_type, pos.sell_strike, pos.expiration_date,
        )
        if buy_price is None or sell_price is None:
            return None
        return round(buy_price - sell_price, 2)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_iv_from_chain(
    symbol: str, option_type: str, strike: float, expiration: str
) -> float | None:
    """Fetch current IV for a specific contract from yfinance chain."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        chain = ticker.option_chain(expiration)
        df = chain.calls if option_type == "call" else chain.puts
        row = df[df["strike"] == strike]
        if row.empty:
            return None
        iv = float(row["impliedVolatility"].iloc[0])
        return iv if iv > 0 else None
    except Exception:
        return None
