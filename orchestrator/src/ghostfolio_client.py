"""Ghostfolio REST API client with 2-step auth (access token -> JWT)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import structlog

logger = structlog.get_logger()

DEFAULT_TIMEOUT = 30.0


class GhostfolioClient:
    """Client for Ghostfolio REST API.

    Authentication flow:
      1. Use access token to get a short-lived JWT via POST /api/v1/auth/anonymous
      2. Use JWT as Bearer token for all subsequent requests
    """

    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
    ):
        self.base_url = (base_url or os.environ["GHOSTFOLIO_URL"]).rstrip("/")
        self.access_token = access_token or os.environ["GHOSTFOLIO_ACCESS_TOKEN"]
        self._jwt: str | None = None
        self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)

    def _authenticate(self) -> None:
        """Exchange access token for JWT."""
        resp = self._client.post(
            f"{self.base_url}/api/v1/auth/anonymous",
            json={"accessToken": self.access_token},
        )
        resp.raise_for_status()
        self._jwt = resp.json()["authToken"]
        logger.info("ghostfolio_authenticated", base_url=self.base_url)

    def _headers(self) -> dict[str, str]:
        if not self._jwt:
            self._authenticate()
        return {"Authorization": f"Bearer {self._jwt}"}

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make authenticated request with auto-retry on 401."""
        url = f"{self.base_url}{path}"
        resp = self._client.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            logger.info("ghostfolio_jwt_expired, re-authenticating")
            self._authenticate()
            resp = self._client.request(method, url, headers=self._headers(), **kwargs)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    # --- Account operations ---

    def list_accounts(self) -> list[dict]:
        return self._request("GET", "/api/v1/account")

    def get_account(self, account_id: str) -> dict:
        return self._request("GET", f"/api/v1/account/{account_id}")

    def create_account(
        self,
        name: str,
        balance: float,
        currency: str = "USD",
        platform_id: str | None = None,
    ) -> dict:
        payload: dict = {
            "balance": balance,
            "currency": currency,
            "isExcluded": False,
            "name": name,
        }
        if platform_id:
            payload["platformId"] = platform_id
        result = self._request("POST", "/api/v1/account", json=payload)
        logger.info("ghostfolio_account_created", name=name, account_id=result.get("id"))
        return result

    # --- Order (activity) operations ---

    def create_order(
        self,
        account_id: str,
        symbol: str,
        order_type: str,
        quantity: float,
        unit_price: float,
        currency: str = "USD",
        date: datetime | None = None,
        fee: float = 0,
        data_source: str = "YAHOO",
    ) -> dict:
        if date is None:
            date = datetime.now(timezone.utc)
        payload = {
            "accountId": account_id,
            "currency": currency,
            "dataSource": data_source,
            "date": date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "fee": fee,
            "quantity": quantity,
            "symbol": symbol,
            "type": order_type,
            "unitPrice": unit_price,
        }
        result = self._request("POST", "/api/v1/order", json=payload)
        logger.info(
            "ghostfolio_order_created",
            account_id=account_id,
            symbol=symbol,
            type=order_type,
            quantity=quantity,
            unit_price=unit_price,
        )
        return result

    def list_orders(self) -> list[dict]:
        resp = self._request("GET", "/api/v1/order")
        return resp.get("activities", resp) if isinstance(resp, dict) else resp

    def delete_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/api/v1/order/{order_id}")

    # --- Portfolio operations ---

    def get_portfolio_details(self) -> dict:
        return self._request("GET", "/api/v1/portfolio/details")

    def get_portfolio_holdings(self) -> dict:
        return self._request("GET", "/api/v1/portfolio/holdings")

    def get_portfolio_performance(self, range_: str = "max") -> dict:
        return self._request("GET", f"/api/v1/portfolio/performance?range={range_}")

    def get_portfolio_summary(self) -> dict:
        return self._request("GET", "/api/v1/portfolio/summary")

    # --- Info ---

    def get_info(self) -> dict:
        """Health check / system info (no auth needed)."""
        resp = self._client.get(f"{self.base_url}/api/v1/info")
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
