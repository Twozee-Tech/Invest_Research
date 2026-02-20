"""Financial news fetcher using RSS feeds with caching and relevance filtering."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import feedparser
import structlog

logger = structlog.get_logger()

NEWS_CACHE_TTL = 900  # 15 minutes

RSS_FEEDS = {
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
    "cnbc_top": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "cnbc_market": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "reuters_business": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
    "marketwatch": "http://feeds.marketwatch.com/marketwatch/topstories/",
}


@dataclass
class NewsItem:
    title: str
    summary: str
    source: str
    published: str
    link: str
    relevance_score: float = 0.0


class NewsFetcher:
    """Fetches and filters financial news from RSS feeds."""

    def __init__(self, cache_ttl: int = NEWS_CACHE_TTL):
        self._cache: dict[str, tuple[list[NewsItem], float]] = {}
        self._cache_ttl = cache_ttl

    def fetch_news(self, max_items: int = 20) -> list[NewsItem]:
        """Fetch latest financial news from all RSS feeds."""
        # Check cache
        cache_key = "all_news"
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached[1]) < self._cache_ttl:
            return cached[0][:max_items]

        all_items: list[NewsItem] = []
        for source_name, feed_url in RSS_FEEDS.items():
            try:
                items = self._parse_feed(feed_url, source_name)
                all_items.extend(items)
            except Exception as e:
                logger.warning("news_feed_failed", source=source_name, error=str(e))

        # Deduplicate by title similarity
        seen_titles: set[str] = set()
        unique_items = []
        for item in all_items:
            normalized = item.title.lower().strip()
            if normalized not in seen_titles:
                seen_titles.add(normalized)
                unique_items.append(item)

        # Sort by relevance (financial keywords)
        unique_items.sort(key=lambda x: x.relevance_score, reverse=True)

        self._cache[cache_key] = (unique_items, time.time())
        logger.info("news_fetched", total_items=len(unique_items))
        return unique_items[:max_items]

    def fetch_relevant_news(
        self,
        watchlist: list[str],
        max_items: int = 10,
    ) -> list[NewsItem]:
        """Fetch news filtered for relevance to a watchlist.

        Returns watchlist-specific news first, filled with general financial
        news if needed. Avoids injecting totally unrelated stories.
        """
        all_news = self.fetch_news(max_items=50)

        symbol_to_name = {
            "AAPL": "APPLE", "MSFT": "MICROSOFT", "GOOGL": "GOOGLE",
            "AMZN": "AMAZON", "NVDA": "NVIDIA", "META": "META",
            "TSLA": "TESLA", "JPM": "JPMORGAN", "V": "VISA",
            "JNJ": "JOHNSON", "UNH": "UNITEDHEALTH", "WMT": "WALMART",
            "PG": "PROCTER", "KO": "COCA-COLA", "HD": "HOME DEPOT",
            "AMD": "AMD", "COIN": "COINBASE", "PLTR": "PALANTIR",
            "SOFI": "SOFI", "SPY": "S&P 500", "QQQ": "NASDAQ",
            "SCHD": "SCHWAB", "VTI": "VANGUARD", "VOO": "VANGUARD",
            "IWM": "RUSSELL", "MARA": "MARATHON",
        }
        watchlist_upper = {s.upper() for s in watchlist}

        watchlist_items: list[NewsItem] = []
        general_items: list[NewsItem] = []

        for item in all_news:
            text = f"{item.title} {item.summary}".upper()
            boost = 0.0

            for sym in watchlist_upper:
                if sym in text:
                    boost += 2.0
            for sym in watchlist:
                name = symbol_to_name.get(sym, "")
                if name and name in text:
                    boost += 1.5

            if boost > 0:
                item.relevance_score += boost
                watchlist_items.append(item)
            else:
                general_items.append(item)

        # Watchlist-specific news first, fill remainder with general financial news
        result = sorted(watchlist_items, key=lambda x: x.relevance_score, reverse=True)[:max_items]
        if len(result) < max_items:
            general_sorted = sorted(general_items, key=lambda x: x.relevance_score, reverse=True)
            result.extend(general_sorted[:max_items - len(result)])

        logger.debug(
            "news_relevant_filtered",
            watchlist_count=len(watchlist_items),
            general_count=len(general_items),
            returned=len(result),
        )
        return result

    def _parse_feed(self, url: str, source: str) -> list[NewsItem]:
        """Parse a single RSS feed."""
        feed = feedparser.parse(url)
        items = []
        for entry in feed.entries[:15]:
            title = entry.get("title", "")
            summary = entry.get("summary", entry.get("description", ""))
            # Strip HTML tags from summary
            summary = re.sub(r"<[^>]+>", "", summary)
            if len(summary) > 300:
                summary = summary[:300] + "..."

            published = entry.get("published", entry.get("updated", ""))

            item = NewsItem(
                title=title,
                summary=summary,
                source=source,
                published=published,
                link=entry.get("link", ""),
                relevance_score=self._base_relevance(title, summary),
            )
            items.append(item)
        return items

    @staticmethod
    def _base_relevance(title: str, summary: str) -> float:
        """Score base relevance of a news item based on financial keywords."""
        text = f"{title} {summary}".lower()
        keywords = {
            "fed": 1.5, "interest rate": 1.5, "inflation": 1.3,
            "earnings": 1.2, "revenue": 1.0, "profit": 1.0,
            "market": 0.8, "stock": 0.8, "trade": 0.7,
            "gdp": 1.0, "jobs": 0.9, "unemployment": 0.9,
            "tariff": 1.2, "recession": 1.3, "rally": 0.8,
            "crash": 1.5, "bull": 0.7, "bear": 0.7,
            "etf": 0.6, "bond": 0.6, "yield": 0.7,
            "s&p": 1.0, "nasdaq": 1.0, "dow": 0.9,
        }
        score = 0.0
        for kw, weight in keywords.items():
            if kw in text:
                score += weight
        return score

    def format_for_prompt(self, news: list[NewsItem]) -> str:
        """Format news items for LLM prompt injection."""
        if not news:
            return "== RECENT NEWS ==\nNo significant financial news available."

        lines = ["== RECENT NEWS =="]
        for i, item in enumerate(news, 1):
            lines.append(f"{i}. [{item.source}] {item.title}")
            if item.summary:
                lines.append(f"   {item.summary[:200]}")
        return "\n".join(lines)
