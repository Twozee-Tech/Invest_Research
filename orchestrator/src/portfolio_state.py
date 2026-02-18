"""Aggregate portfolio state from Ghostfolio for a specific account."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import structlog

from .ghostfolio_client import GhostfolioClient

logger = structlog.get_logger()


@dataclass
class Position:
    symbol: str
    name: str
    quantity: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    sector: str
    first_buy_date: str | None = None
    weight_pct: float = 0.0


@dataclass
class PortfolioState:
    account_id: str
    account_name: str
    total_value: float
    cash: float
    invested: float
    positions: list[Position] = field(default_factory=list)
    total_pl: float = 0.0
    total_pl_pct: float = 0.0
    sector_weights: dict[str, float] = field(default_factory=dict)
    timestamp: str = ""

    @property
    def cash_pct(self) -> float:
        if self.total_value <= 0:
            return 100.0
        return (self.cash / self.total_value) * 100

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def get_position(self, symbol: str) -> Position | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None

    def to_prompt_text(self) -> str:
        """Format portfolio state for LLM prompt."""
        lines = [
            f"== PORTFOLIO STATE ({self.account_name}) ==",
            f"Total Value: ${self.total_value:,.2f}",
            f"Cash: ${self.cash:,.2f} ({self.cash_pct:.1f}%)",
            f"Invested: ${self.invested:,.2f}",
            f"Total P/L: ${self.total_pl:,.2f} ({self.total_pl_pct:+.2f}%)",
            f"Positions: {self.position_count}",
            "",
        ]
        if self.positions:
            lines.append("Holdings:")
            for p in sorted(self.positions, key=lambda x: x.market_value, reverse=True):
                lines.append(
                    f"  {p.symbol}: {p.quantity:.4f} shares @ avg ${p.avg_cost:.2f} "
                    f"| now ${p.current_price:.2f} | value ${p.market_value:,.2f} "
                    f"| P/L {p.unrealized_pl_pct:+.1f}% | weight {p.weight_pct:.1f}% "
                    f"| sector: {p.sector}"
                )
        else:
            lines.append("Holdings: (none - cash only)")

        if self.sector_weights:
            lines.append("")
            lines.append("Sector breakdown:")
            for sector, weight in sorted(self.sector_weights.items(), key=lambda x: -x[1]):
                lines.append(f"  {sector}: {weight:.1f}%")

        return "\n".join(lines)


def get_portfolio_state(
    client: GhostfolioClient,
    account_id: str,
    account_name: str,
) -> PortfolioState:
    """Build a PortfolioState from Ghostfolio API data."""
    _empty = PortfolioState(
        account_id=account_id,
        account_name=account_name,
        total_value=0,
        cash=0,
        invested=0,
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        holdings_data = client.get_portfolio_holdings()
        accounts_raw = client.list_accounts()
    except Exception as e:
        logger.error("portfolio_state_fetch_failed", account_id=account_id, error=str(e))
        return _empty

    try:
        # list_accounts() returns a list in most Ghostfolio versions,
        # but some versions wrap it: {"accounts": [...]}
        if isinstance(accounts_raw, list):
            accounts = accounts_raw
        elif isinstance(accounts_raw, dict):
            accounts = accounts_raw.get("accounts", []) or []
        else:
            accounts = []

        # Find account balance (cash)
        account_info = None
        for acc in accounts:
            if isinstance(acc, dict) and acc.get("id") == account_id:
                account_info = acc
                break

        cash = account_info.get("balance", 0) if isinstance(account_info, dict) else 0

        # Ghostfolio wraps holdings: {"holdings": {...}} in some versions
        if isinstance(holdings_data, dict) and "holdings" in holdings_data:
            raw_holdings = holdings_data["holdings"]
        else:
            raw_holdings = holdings_data

        # Build positions from holdings that belong to this account
        positions = []
        sector_totals: dict[str, float] = {}
        total_invested = 0.0

        holdings = raw_holdings if isinstance(raw_holdings, dict) else {}
        for symbol, holding in holdings.items():
            # Filter holdings for this account
            if not isinstance(holding, dict):
                continue

            accounts_in_holding = holding.get("accounts", {})
            if account_id not in accounts_in_holding and accounts_in_holding:
                continue

            quantity = holding.get("quantity", 0)
            if quantity <= 0:
                continue

            avg_cost = holding.get("averagePrice", 0)
            current_price = holding.get("marketPrice", 0)
            market_value = holding.get("marketValue", quantity * current_price)
            investment = holding.get("investment", quantity * avg_cost)
            unrealized_pl = market_value - investment
            unrealized_pl_pct = (unrealized_pl / investment * 100) if investment > 0 else 0

            # sectors may be a list of dicts {"name": "Tech"} or plain strings
            sectors_raw = holding.get("sectors") or []
            if sectors_raw:
                first = sectors_raw[0]
                sector = first.get("name", "Unknown") if isinstance(first, dict) else str(first)
            else:
                sector = "Unknown"

            position = Position(
                symbol=symbol,
                name=holding.get("name", symbol),
                quantity=quantity,
                avg_cost=avg_cost,
                current_price=current_price,
                market_value=market_value,
                unrealized_pl=unrealized_pl,
                unrealized_pl_pct=unrealized_pl_pct,
                sector=sector,
                first_buy_date=holding.get("firstBuyDate"),
            )
            positions.append(position)
            total_invested += investment
            sector_totals[sector] = sector_totals.get(sector, 0) + market_value

        total_market = sum(p.market_value for p in positions)
        total_value = total_market + cash

        # Compute weights
        for p in positions:
            p.weight_pct = (p.market_value / total_value * 100) if total_value > 0 else 0

        sector_weights = {}
        for sector, val in sector_totals.items():
            sector_weights[sector] = (val / total_value * 100) if total_value > 0 else 0

        total_pl = total_market - total_invested
        total_pl_pct = (total_pl / total_invested * 100) if total_invested > 0 else 0

        state = PortfolioState(
            account_id=account_id,
            account_name=account_name,
            total_value=total_value,
            cash=cash,
            invested=total_invested,
            positions=positions,
            total_pl=total_pl,
            total_pl_pct=total_pl_pct,
            sector_weights=sector_weights,
            timestamp=datetime.utcnow().isoformat(),
        )

        logger.info(
            "portfolio_state_loaded",
            account=account_name,
            total_value=total_value,
            positions=len(positions),
            cash=cash,
        )
        return state

    except Exception as e:
        logger.error("portfolio_state_build_failed", account_id=account_id, error=str(e))
        return _empty
