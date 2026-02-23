"""Multi-account lifecycle management: create, check, sync with Ghostfolio."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
import structlog

from .ghostfolio_client import GhostfolioClient

logger = structlog.get_logger()


class AccountManager:
    """Manages account lifecycle between config.yaml and Ghostfolio."""

    def __init__(self, config_path: str = "data/config.yaml", client: GhostfolioClient | None = None):
        self.config_path = Path(config_path)
        self.client = client or GhostfolioClient()
        self._config: dict | None = None

    def load_config(self) -> dict:
        """Load config.yaml."""
        with open(self.config_path) as f:
            self._config = yaml.safe_load(f)
        return self._config

    def save_config(self, config: dict | None = None) -> None:
        """Save config.yaml."""
        cfg = config or self._config
        if cfg is None:
            raise ValueError("No config to save")
        with open(self.config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.info("config_saved", path=str(self.config_path))

    @property
    def config(self) -> dict:
        if self._config is None:
            self.load_config()
        return self._config

    def get_accounts(self) -> dict:
        """Get all account configs."""
        return self.config.get("accounts", {})

    def get_account(self, account_key: str) -> dict | None:
        """Get a single account config by key."""
        return self.get_accounts().get(account_key)

    def ensure_accounts_exist(self) -> dict[str, str]:
        """Ensure all accounts exist in Ghostfolio, creating missing ones.

        Returns mapping of account_key -> ghostfolio_account_id.
        """
        accounts = self.get_accounts()
        initial_budget = self.config.get("defaults", {}).get("initial_budget", 10000)
        result: dict[str, str] = {}
        config_changed = False

        # Cycle types that don't trade and need no Ghostfolio account
        _NON_TRADING = {"research"}

        # Build a map of all existing Ghostfolio accounts once (avoids per-account GET calls)
        gf_accounts_by_id: dict[str, dict] = {}
        try:
            all_accts = self.client.list_accounts()
            if isinstance(all_accts, dict):
                raw_list = all_accts.get("accounts", []) or []
            else:
                raw_list = all_accts if isinstance(all_accts, list) else []
            for a in raw_list:
                if isinstance(a, dict) and a.get("id"):
                    gf_accounts_by_id[a["id"]] = a
        except Exception as e:
            logger.warning("ensure_accounts_list_failed", error=str(e))

        for key, acct in accounts.items():
            if acct.get("cycle_type") in _NON_TRADING:
                continue  # research agent has no portfolio

            gf_id = acct.get("ghostfolio_account_id", "TBD")
            name = acct.get("name", key)
            currency = self.config.get("defaults", {}).get("currency", "USD")

            if gf_id and gf_id != "TBD":
                existing = gf_accounts_by_id.get(gf_id)
                if existing:
                    result[key] = gf_id
                    # Rename if name differs
                    existing_name = existing.get("name", "")
                    if existing_name and existing_name != name:
                        try:
                            self.client.update_account(gf_id, name=name)
                            logger.info("account_renamed", key=key, old=existing_name, new=name)
                        except Exception as re:
                            logger.warning("account_rename_failed", key=key, error=str(re))
                    else:
                        logger.info("account_verified", key=key, ghostfolio_id=gf_id)
                    continue
                else:
                    logger.warning("account_not_found_in_ghostfolio", key=key, id=gf_id)

            # Create new account
            try:
                new_account = self.client.create_account(
                    name=name,
                    balance=initial_budget,
                    currency=currency,
                )
                new_id = new_account["id"]
                accounts[key]["ghostfolio_account_id"] = new_id
                result[key] = new_id
                config_changed = True
                logger.info("account_created", key=key, ghostfolio_id=new_id, balance=initial_budget)
            except Exception as e:
                logger.error("account_creation_failed", key=key, error=str(e))

        if config_changed:
            self.save_config()

        return result

    def add_account(
        self,
        key: str,
        name: str,
        model: str,
        cron: str,
        strategy: str,
        risk_profile: dict,
        watchlist: list[str],
        fallback_model: str = "Qwen3-Next",
        strategy_description: str = "",
        prompt_style: str = "",
        preferred_metrics: list[str] | None = None,
        horizon: str = "weeks to months",
    ) -> str:
        """Add a new account to config and create in Ghostfolio.

        Returns the Ghostfolio account ID.
        """
        initial_budget = self.config.get("defaults", {}).get("initial_budget", 10000)

        # Create in Ghostfolio
        new_account = self.client.create_account(
            name=name,
            balance=initial_budget,
            currency=self.config.get("defaults", {}).get("currency", "USD"),
        )
        gf_id = new_account["id"]

        # Add to config
        self.config.setdefault("accounts", {})[key] = {
            "name": name,
            "ghostfolio_account_id": gf_id,
            "model": model,
            "fallback_model": fallback_model,
            "cron": cron,
            "strategy": strategy,
            "strategy_description": strategy_description,
            "prompt_style": prompt_style,
            "preferred_metrics": preferred_metrics or [],
            "horizon": horizon,
            "risk_profile": risk_profile,
            "watchlist": watchlist,
        }
        self.save_config()
        logger.info("account_added", key=key, ghostfolio_id=gf_id)
        return gf_id

    def remove_account(self, key: str) -> bool:
        """Remove account from config (does not delete from Ghostfolio)."""
        accounts = self.config.get("accounts", {})
        if key in accounts:
            del accounts[key]
            self.save_config()
            logger.info("account_removed", key=key)
            return True
        return False

    def update_account(self, key: str, updates: dict) -> None:
        """Update account config fields."""
        acct = self.config.get("accounts", {}).get(key)
        if acct is None:
            raise ValueError(f"Account '{key}' not found")
        acct.update(updates)
        self.save_config()
        logger.info("account_updated", key=key, fields=list(updates.keys()))

    def list_account_summaries(self) -> list[dict]:
        """Get summary of all accounts for display."""
        summaries = []
        for key, acct in self.get_accounts().items():
            summaries.append({
                "key": key,
                "name": acct.get("name", key),
                "ghostfolio_id": acct.get("ghostfolio_account_id", "TBD"),
                "model": acct.get("model", "Unknown"),
                "cron": acct.get("cron", ""),
                "strategy": acct.get("strategy", ""),
                "watchlist_count": len(acct.get("watchlist", [])),
            })
        return summaries
