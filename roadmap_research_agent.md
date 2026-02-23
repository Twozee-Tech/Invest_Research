# Research Agent — Implementation Roadmap

Codzienny agent analityczny (14:00 CET, przed otwarciem NYSE o 15:30) który zbiera
pełne artykuły finansowe + geopolityczne, screeners i dane makro, wrzuca wszystko
do 100k kontekstu i produkuje `data/daily_research.json` czytany przez wszystkie
konta tradingowe przy ich własnym cyklu.

---

## Architektura przepływu

```
14:00 CET — run_research_cycle()
  │
  ├── NewsFetcher.fetch_full_articles()   ← ~40 pełnych artykułów (financial + geopolitical)
  ├── WatchlistManager.get_screener_symbols()  ← 6 screenerów yfinance
  ├── market_data.get_market_overview()   ← indeksy, sektory, VIX, TNX
  │
  └── LLM (Miro_Thinker, max_tokens=8192, 100k kontekst)
        └── Zapisuje data/daily_research.json
              │
              ▼
  15:00+ — run_intraday_cycle() / run_cycle()
    ├── ResearchAgent.load_today()  → research_brief dict
    ├── WatchlistManager dodaje top_symbols do universe
    └── build_pass1_messages(research_brief=...) → sekcja == DAILY RESEARCH BRIEF ==
```

---

## Pliki do zmiany (w kolejności implementacji)

### 1. `orchestrator/pyproject.toml`

Dodać jedną zależność:

```toml
[tool.poetry.dependencies]
beautifulsoup4 = "^4.12"
```

---

### 2. `orchestrator/src/news_fetcher.py`

#### 2a. Rozszerzyć `RSS_FEEDS` o geopolitykę

```python
RSS_FEEDS = {
    # Financial (obecne)
    "yahoo_finance":    "https://finance.yahoo.com/news/rssindex",
    "cnbc_top":         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "cnbc_market":      "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "marketwatch":      "http://feeds.marketwatch.com/marketwatch/topstories/",
    # Financial (nowe)
    "wsj_markets":      "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "investing_com":    "https://www.investing.com/rss/news.rss",
    # Geopolitical (nowe)
    "reuters_world":    "https://feeds.reuters.com/reuters/worldNews",
    "bbc_business":     "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bbc_world":        "https://feeds.bbci.co.uk/news/world/rss.xml",
    "ap_top":           "https://feeds.apnews.com/rss/apf-topnews",
    "aljazeera":        "https://www.aljazeera.com/xml/rss/all.xml",
}
```

#### 2b. Dodać metodę `fetch_full_article(url, max_chars=3000) -> str`

```python
def fetch_full_article(self, url: str, max_chars: int = 3000) -> str:
    """Fetch and extract plain text from article URL.

    Returns empty string on any error (graceful degradation).
    """
    # Check article cache (articles don't change)
    cached = self._article_cache.get(url)
    if cached:
        return cached

    try:
        from bs4 import BeautifulSoup
        resp = httpx.get(
            url,
            timeout=8.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"},
            follow_redirects=True,
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        # Strip boilerplate
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        text = " ".join(text.split())          # normalize whitespace
        result = text[:max_chars]
        self._article_cache[url] = result
        return result
    except Exception as e:
        logger.debug("article_fetch_failed", url=url[:60], error=str(e))
        return ""
```

Dodać `self._article_cache: dict[str, str] = {}` do `__init__`.

#### 2c. Dodać metodę `fetch_news_with_articles(max_items, max_article_chars) -> list[NewsItem]`

```python
def fetch_news_with_articles(
    self,
    max_items: int = 40,
    max_article_chars: int = 3000,
) -> list[NewsItem]:
    """Fetch news from all feeds (financial + geopolitical) with full article text.

    Used by ResearchAgent — broader and richer than fetch_relevant_news().
    """
    all_items = self.fetch_news(max_items=max_items * 2)  # fetch extra before filtering

    # Enrich with full article text (parallel would be faster but sequential is safe)
    for item in all_items[:max_items]:
        if item.link and not item.summary or len(item.summary) < 200:
            full = self.fetch_full_article(item.link, max_chars=max_article_chars)
            if full:
                item.summary = full   # replace truncated RSS summary with full text

    return all_items[:max_items]
```

---

### 3. `orchestrator/src/research_agent.py` — NOWY PLIK

```python
"""Daily market research agent.

Runs once a day (14:00 CET), gathers broad financial + geopolitical data,
sends to LLM for synthesis, saves result to data/daily_research.json.
All trading accounts read this file at the start of their own cycle.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import structlog

logger = structlog.get_logger()

_OUTPUT_FILE = Path("data/daily_research.json")

# yfinance screener names to run (beyond the 3 used in WatchlistManager)
RESEARCH_SCREENERS = [
    "day_gainers",
    "day_losers",
    "most_actives",
    "growth_technology_stocks",    # tech high-growth
    "undervalued_large_caps",      # value opportunities
    "most_shorted_stocks",         # short squeeze candidates
]

RESEARCH_SYSTEM_PROMPT = """You are a senior market research analyst preparing a
pre-market intelligence brief for a team of autonomous investment agents.

Your job:
1. Read ALL provided news articles (financial + geopolitical) and market data.
2. Identify 10-15 stocks/ETFs most worth watching today, with clear investment thesis.
3. Assess macro regime and key themes driving markets.
4. Flag geopolitical risks that could move specific sectors.
5. Note any important events (earnings, Fed speakers, economic data releases).

Be concrete and data-driven. Every symbol recommendation must have a specific
catalyst or thesis, not just "momentum" or "looks interesting".

Respond with valid JSON:
{
  "date": "YYYY-MM-DD",
  "market_regime": "BULL_TREND|BEAR_TREND|SIDEWAYS|HIGH_VOLATILITY",
  "key_themes": ["theme1", "theme2"],
  "top_symbols": [
    {
      "symbol": "NVDA",
      "thesis": "Blackwell demand ahead of GTC conference; analysts raised targets",
      "catalyst": "GTC 2026 keynote March 18",
      "sector": "Technology",
      "conviction": "HIGH|MEDIUM|LOW",
      "direction": "BULLISH|BEARISH|NEUTRAL"
    }
  ],
  "sectors": [
    {"name": "Energy", "bias": "OVERWEIGHT|NEUTRAL|UNDERWEIGHT", "reason": "..."}
  ],
  "geopolitical_risks": [
    {
      "event": "Russia-Ukraine energy supply disruption",
      "affected_sectors": ["Energy", "Materials"],
      "market_impact": "Bullish natgas, XOM, CVX; bearish EUR/USD"
    }
  ],
  "macro_events_today": "CPI at 14:30 ET, Powell speech 16:00 ET",
  "avoid_today": ["any sectors or symbols to avoid and why"]
}
"""


class ResearchAgent:
    def __init__(self, llm, news, market_data, config: dict):
        self.llm = llm
        self.news = news
        self.market_data = market_data
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """Gather data, call LLM, save and return research brief."""
        research_config = self.config.get("accounts", {}).get("research", {})
        model = research_config.get("model", "Nemotron")
        fallback = research_config.get("fallback_model", "Qwen3-Next")
        max_news = research_config.get("max_news_articles", 40)
        max_article_chars = research_config.get("max_article_chars", 3000)
        max_screener = research_config.get("max_screener_per_source", 15)

        logger.info("research_agent_start", model=model)

        # --- Gather all data ---
        news_text = self._gather_news(max_news, max_article_chars)
        screener_text = self._gather_screeners(max_screener)
        market_text = self._gather_market_overview()

        # --- Build prompt ---
        from datetime import datetime
        today = datetime.now().strftime("%A %Y-%m-%d %H:%M CET")
        user_content = "\n\n".join([
            f"== TODAY: {today} ==",
            market_text,
            screener_text,
            news_text,
            "Analyze all the above and respond with the research brief JSON.",
        ])

        messages = [
            {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

        # --- Call LLM ---
        result = self.llm.chat_json(
            messages=messages,
            model=model,
            fallback_model=fallback,
            temperature=0.6,
            max_tokens=8192,
        )

        # Ensure date is set
        result["date"] = date.today().isoformat()

        self._save(result)
        logger.info(
            "research_agent_complete",
            themes=result.get("key_themes", []),
            symbols=[s["symbol"] for s in result.get("top_symbols", [])],
        )
        return result

    @staticmethod
    def load_today() -> dict | None:
        """Load today's research brief if it exists. Returns None if stale or missing."""
        if not _OUTPUT_FILE.exists():
            return None
        try:
            with open(_OUTPUT_FILE) as f:
                data = json.load(f)
            if data.get("date") == date.today().isoformat():
                return data
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _gather_news(self, max_items: int, max_article_chars: int) -> str:
        """Fetch full articles from financial + geopolitical RSS feeds."""
        items = self.news.fetch_news_with_articles(
            max_items=max_items,
            max_article_chars=max_article_chars,
        )
        lines = [f"== NEWS ({len(items)} articles) =="]
        for i, item in enumerate(items, 1):
            lines.append(f"\n--- [{i}] {item.source.upper()} | {item.published} ---")
            lines.append(f"TITLE: {item.title}")
            if item.summary:
                lines.append(item.summary)
        return "\n".join(lines)

    def _gather_screeners(self, max_per_source: int) -> str:
        """Run multiple yfinance screeners and return formatted results."""
        import yfinance as yf
        lines = ["== MARKET SCREENERS =="]
        all_symbols: list[str] = []

        for screener_name in RESEARCH_SCREENERS:
            try:
                result = yf.screen(screener_name)
                quotes = result.get("quotes", [])[:max_per_source]
                symbols = [q.get("symbol", "") for q in quotes if q.get("symbol")]
                all_symbols.extend(symbols)
                lines.append(f"\n{screener_name.upper()} ({len(symbols)} symbols):")
                for q in quotes:
                    sym = q.get("symbol", "")
                    price = q.get("regularMarketPrice", "?")
                    chg = q.get("regularMarketChangePercent", 0)
                    name = q.get("shortName", "")
                    lines.append(f"  {sym} ({name}): ${price} {chg:+.2f}%")
            except Exception as e:
                logger.warning("screener_failed", name=screener_name, error=str(e))

        return "\n".join(lines)

    def _gather_market_overview(self) -> str:
        """Fetch indices, sectors, VIX, TNX."""
        try:
            overview = self.market_data.get_market_overview()
            lines = ["== MARKET OVERVIEW =="]
            for sym, data in overview.items():
                price = data.get("price", "?")
                chg = data.get("change_pct", 0)
                label = data.get("label", "")
                label_str = f" [{label}]" if label else ""
                lines.append(f"{sym}{label_str}: {price} ({chg:+.2f}%)")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("market_overview_failed", error=str(e))
            return "== MARKET OVERVIEW ==\n(unavailable)"

    @staticmethod
    def _save(data: dict) -> None:
        _OUTPUT_FILE.parent.mkdir(exist_ok=True)
        with open(_OUTPUT_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("research_saved", path=str(_OUTPUT_FILE))
```

---

### 4. `orchestrator/src/watchlist_manager.py`

W metodzie `get_full_watchlist()` po liniach ze screenerami dodać blok:

```python
# Research agent suggestions (today's brief)
try:
    from .research_agent import ResearchAgent
    brief = ResearchAgent.load_today()
    if brief:
        research_syms = [s["symbol"] for s in brief.get("top_symbols", [])]
        logger.debug("watchlist_research_symbols", count=len(research_syms))
        all_symbols.extend(research_syms)
except Exception as e:
    logger.warning("watchlist_research_load_failed", error=str(e))
```

Dodać **przed** krokiem deduplication (`seen = set()` itd.), żeby research symbole
też przeszły przez filtr `_TICKER_RE`.

---

### 5. `orchestrator/src/prompt_builder.py`

#### 5a. Nowa funkcja pomocnicza

Dodać na końcu pliku:

```python
def format_research_brief(brief: dict) -> str:
    """Format daily research brief for LLM prompt injection."""
    if not brief:
        return ""
    lines = ["== DAILY RESEARCH BRIEF (pre-market analysis) =="]

    regime = brief.get("market_regime", "")
    themes = brief.get("key_themes", [])
    macro = brief.get("macro_events_today", "")

    if regime:
        lines.append(f"Market regime: {regime}")
    if themes:
        lines.append(f"Key themes: {', '.join(themes)}")
    if macro:
        lines.append(f"Macro events today: {macro}")

    symbols = brief.get("top_symbols", [])
    if symbols:
        lines.append("\nTop research picks:")
        for s in symbols:
            conviction = s.get("conviction", "")
            direction = s.get("direction", "")
            tag = f"[{conviction}/{direction}]" if conviction else ""
            lines.append(
                f"  {s['symbol']} {tag}: {s.get('thesis', '')} "
                f"| catalyst: {s.get('catalyst', 'N/A')}"
            )

    geo_risks = brief.get("geopolitical_risks", [])
    if geo_risks:
        lines.append("\nGeopolitical risks:")
        for r in geo_risks:
            lines.append(
                f"  {r.get('event', '')}: {r.get('market_impact', '')} "
                f"(sectors: {', '.join(r.get('affected_sectors', []))})"
            )

    avoid = brief.get("avoid_today", [])
    if avoid:
        lines.append(f"\nAvoid today: {', '.join(str(a) for a in avoid)}")

    return "\n".join(lines)
```

#### 5b. Zmienić sygnaturę `build_pass1_messages()`

```python
def build_pass1_messages(
    portfolio: PortfolioState,
    market_data: dict[str, dict],
    technical_signals: dict[str, TechnicalSignals],
    news_text: str,
    decision_history: str,
    strategy_config: dict,
    earnings_text: str = "",
    fundamentals_text: str = "",
    research_brief: dict | None = None,   # ← NOWY parametr
) -> list[dict[str, str]]:
```

#### 5c. Dodać do `user_parts` (po `fundamentals_text`, przed `earnings_text`)

```python
if research_brief:
    brief_text = format_research_brief(research_brief)
    if brief_text:
        user_parts += ["", brief_text]
```

---

### 6. `orchestrator/src/main.py`

#### 6a. Import na górze pliku

```python
from .research_agent import ResearchAgent
```

#### 6b. Nowa metoda `run_research_cycle()`

Dodać po `run_intraday_cycle()` (ok. linia ~460):

```python
def run_research_cycle(self) -> None:
    """Daily market research: gather broad news + screeners → LLM synthesis → JSON brief."""
    self._load_config()
    from .research_agent import ResearchAgent
    agent = ResearchAgent(self.llm, self.news, self.market_data, self.config)
    try:
        result = agent.run()
        logger.info(
            "research_cycle_complete",
            themes=result.get("key_themes", []),
            top_symbols=[s["symbol"] for s in result.get("top_symbols", [])],
            regime=result.get("market_regime"),
        )
    except Exception as e:
        logger.error("research_cycle_failed", error=str(e), exc_info=True)
```

#### 6c. W `run_cycle()` — załadować brief i przekazać do Pass 1

Po bloku `fundamentals_text` (ok. linia ~565), dodać:

```python
# Daily research brief (produced by research agent at 14:00 CET)
research_brief: dict | None = None
try:
    research_brief = ResearchAgent.load_today()
    if research_brief:
        logger.debug("research_brief_loaded", date=research_brief.get("date"))
except Exception as e:
    logger.warning("research_brief_load_failed", error=str(e))
```

Zmienić wywołanie `build_pass1_messages()`:

```python
pass1_messages = build_pass1_messages(
    ...
    fundamentals_text=fundamentals_text,
    research_brief=research_brief,      # ← dodać
)
```

#### 6d. To samo w `run_intraday_cycle()`

Analogicznie — załadować `research_brief` i przekazać do `build_pass1_messages()`.

#### 6e. Dispatcher w schedulerze

W bloku `if cycle_type == "intraday":` dodać gałąź:

```python
if cycle_type == "research":
    job_fn = orch.run_research_cycle
    grace = 1800  # 30 min grace (heavy LLM call)
elif cycle_type == "intraday":
    job_fn = orch.run_intraday_cycle
    ...
```

#### 6f. CLI `--once` i `--all`

W blokach obsługi CLI dodać analogicznie:

```python
if cycle_type == "research":
    orch.run_research_cycle()
elif cycle_type == "intraday":
    orch.run_intraday_cycle(key)
else:
    orch.run_cycle(key)
```

---

### 7. `config.yaml`

Dodać jako **pierwszy** account (żeby scheduler odpalił go przed resztą):

```yaml
accounts:
  research:
    name: "Daily Research Agent"
    cycle_type: "research"
    cron: "0 14 * * 0-4"        # Mon-Fri 14:00 CET (1.5h przed NYSE open)
    model: "Nemotron"
    fallback_model: "Qwen3-Next"
    max_news_articles: 40
    max_article_chars: 3000
    max_screener_per_source: 15

  weekly_balanced:
    ...
```

---

## Kolejność wdrożenia

```
1. pyproject.toml          — dodać beautifulsoup4
2. news_fetcher.py         — nowe RSS feeds + fetch_full_article + fetch_news_with_articles
3. research_agent.py       — nowy plik (kopiuj z roadmap)
4. watchlist_manager.py    — dodać blok research symbols
5. prompt_builder.py       — format_research_brief + parametr research_brief
6. main.py                 — run_research_cycle + load_today w run_cycle/run_intraday
7. config.yaml             — dodać account research:
8. Deploy                  — docker compose up --build -d (rebuild bo nowa zależność)
```

---

## Test po wdrożeniu

```bash
# Na serwerze 192.168.0.169:
docker exec invest-orchestrator-1 python3 -m src.main --once research

# Sprawdzić output:
docker exec invest-orchestrator-1 cat data/daily_research.json | python3 -m json.tool | head -80

# Sprawdzić czy daily_momentum czyta brief:
docker exec invest-orchestrator-1 python3 -m src.main --once daily_momentum
# → w logach powinno być: research_brief_loaded date=2026-XX-XX
```

---

## Uwagi

- `data/daily_research.json` jest nadpisywany codziennie. Archiwum logów jest w `logs/` (audit).
- Jeśli research agent nie zdąży lub padnie, konta tradingowe działają normalnie
  (`ResearchAgent.load_today()` zwraca `None` → `research_brief=None` → sekcja pomijana).
- Nemotron może trwać 1-3 min. Grace time schedulera = 30 min.
- BeautifulSoup scraperuje artykuły — niektóre serwisy blokują boty (Reuters, WSJ).
  Wtedy `fetch_full_article()` zwraca `""` i używany jest krótki RSS summary. Graceful.
- 40 artykułów × 3000 znaków = 120k znaków ≈ 30k tokenów (dobrze mieści się w 100k).
