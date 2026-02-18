"""Settings page: global config editor."""

import streamlit as st
import yaml
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CONFIG_PATH = Path("config.yaml")


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except (OSError, FileNotFoundError):
        return {}


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


st.title("Settings")

config = load_config()
defaults = config.get("defaults", {})

st.subheader("Connection Settings")

col1, col2 = st.columns(2)
with col1:
    ghostfolio_url = st.text_input(
        "Ghostfolio URL",
        value=os.environ.get("GHOSTFOLIO_URL", defaults.get("ghostfolio_url", "http://192.168.0.12:3333")),
    )
    st.caption("External Ghostfolio instance")

with col2:
    llm_url = st.text_input(
        "LLM Base URL (llama-swap)",
        value=os.environ.get("LLM_BASE_URL", defaults.get("llm_base_url", "http://192.168.0.169:8080/v1")),
    )
    st.caption("llama-swap OpenAI-compatible endpoint")

st.divider()

st.subheader("Default Values")

col1, col2, col3 = st.columns(3)
with col1:
    budget = st.number_input(
        "Initial Budget ($)",
        min_value=100,
        max_value=1000000,
        value=defaults.get("initial_budget", 10000),
    )
with col2:
    currency = st.selectbox(
        "Currency",
        options=["USD", "EUR", "GBP"],
        index=["USD", "EUR", "GBP"].index(defaults.get("currency", "USD")),
    )
with col3:
    data_source = st.selectbox(
        "Data Source",
        options=["YAHOO"],
        index=0,
    )

st.divider()

st.subheader("Cache Settings")
col1, col2 = st.columns(2)
with col1:
    news_ttl = st.number_input(
        "News Cache TTL (minutes)",
        min_value=1,
        max_value=120,
        value=defaults.get("news_cache_ttl_minutes", 15),
    )
with col2:
    quote_ttl = st.number_input(
        "Quote Cache TTL (seconds)",
        min_value=10,
        max_value=600,
        value=defaults.get("quote_cache_ttl_seconds", 60),
    )

if st.button("Save Settings"):
    config["defaults"] = {
        "initial_budget": budget,
        "currency": currency,
        "data_source": data_source,
        "llm_base_url": llm_url,
        "ghostfolio_url": ghostfolio_url,
        "news_cache_ttl_minutes": news_ttl,
        "quote_cache_ttl_seconds": quote_ttl,
    }
    save_config(config)
    st.success("Settings saved!")

st.divider()

# Raw config viewer
st.subheader("Raw Configuration")
with st.expander("View config.yaml"):
    st.code(yaml.dump(config, default_flow_style=False, sort_keys=False), language="yaml")

# Environment variables
st.subheader("Environment Variables")
env_vars = {
    "GHOSTFOLIO_URL": os.environ.get("GHOSTFOLIO_URL", "Not set"),
    "GHOSTFOLIO_ACCESS_TOKEN": "***" if os.environ.get("GHOSTFOLIO_ACCESS_TOKEN") else "Not set",
    "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", "Not set"),
    "LOG_LEVEL": os.environ.get("LOG_LEVEL", "Not set"),
}
for k, v in env_vars.items():
    st.write(f"`{k}` = {v}")
