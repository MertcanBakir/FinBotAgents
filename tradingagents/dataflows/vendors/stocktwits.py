"""StockTwits public symbol-stream fetcher.

StockTwits exposes a per-symbol message stream at
``api.stocktwits.com/api/2/streams/symbol/{ticker}.json`` that requires no
API key, no OAuth, and no registration. Each message includes a
user-labeled sentiment field (``Bullish``/``Bearish``/null), the message
body, timestamp, and posting user.

The function is deliberately self-contained: short timeout, graceful
degradation on any HTTP or parse failure, and a string return type so
the calling agent gets a uniform interface regardless of whether the
network call succeeded.
"""

from __future__ import annotations

import contextlib
import html
import http.client
import json
import logging
import random
import re
from datetime import datetime
from time import monotonic
from urllib.request import Request, urlopen

from tradingagents.agent_stats import track_data
from tradingagents.dataflows.date_window import coverage_gap, in_window, to_utc
from tradingagents.dataflows.symbols import crypto_base

logger = logging.getLogger(__name__)

_API = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_UA = "tradingagents/0.2 (+https://github.com/TauricResearch/TradingAgents)"


def _created_at(message) -> datetime | None:
    """Parse a message's ISO 8601 ``created_at``; None when missing or malformed."""
    raw = message.get("created_at")
    if not raw:
        return None
    with contextlib.suppress(ValueError, TypeError):
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    return None


def _within_window(messages, start_date, end_date):
    """Keep only messages published in [start_date, end_date] (look-ahead safe).

    No window (both None) leaves the list untouched for live callers. A message
    whose ``created_at`` (ISO 8601) is unparseable is dropped in a historical
    window, since we can't prove it isn't from after the as-of date (#1220).
    """
    if not (start_date and end_date):
        return messages
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    return [m for m in messages if in_window(_created_at(m), start_dt, end_dt)]


def _body(message) -> str:
    """The message text; the API serves it HTML-escaped (``&amp;``, ``&#39;``)."""
    return html.unescape(message.get("body") or "")


def _stocktwits_symbol(ticker: str) -> str:
    """Map a crypto pair to StockTwits' ``<BASE>.X`` convention.

    StockTwits lists crypto as ``BTC.X`` (Yahoo's ``BTC-USD`` form 404s), so any
    crypto symbol resolves to its base plus ``.X``; other symbols pass through
    upper-cased.
    """
    base = crypto_base(ticker)
    return f"{base}.X" if base else ticker.strip().upper()


@track_data("stocktwits")
def fetch_stocktwits_messages(
    ticker: str,
    limit: int = 30,
    timeout: float = 10.0,
    start_date: str | None = None,
    end_date: str | None = None,
    rng=None,
    max_pages: int = 8,
) -> str:
    """Sample recent StockTwits messages for ``ticker`` into an anonymous evidence block.

    Sample labeled posts across six observed time bins after date filtering;
    bounded cursor pagination broadens the pool. ``rng`` accepts a seeded
    Random instance for reproducible tests. Author usernames are discarded and
    @handles in message bodies are redacted. The upstream feed, not this request,
    determines the available historical pool.

    When ``start_date``/``end_date`` (yyyy-mm-dd) are given, messages are trimmed
    to that window, so a historical run never sees today's chatter (#1220). The
    public stream only serves recent messages, so a window it cannot reach is
    reported as unavailable rather than as silence.

    Returns a placeholder string when the endpoint is unreachable, the
    symbol has no messages, or the response shape is unexpected — the
    caller never has to special-case None or exceptions.
    """
    url = _API.format(ticker=_stocktwits_symbol(ticker))
    fetched, seen, cursors = [], set(), set()
    deadline = monotonic() + timeout
    next_url = url
    pages = 0
    fetch_note = ""
    for _ in range(max_pages):
        remaining = deadline - monotonic()
        if remaining <= 0:
            fetch_note = "Fetch time budget reached; partial coverage."
            break
        req = Request(next_url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urlopen(req, timeout=remaining) as resp:
                data = json.loads(resp.read())
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            logger.warning("StockTwits fetch failed for %s: %s", ticker, exc)
            if not fetched:
                return f"<stocktwits unavailable: {type(exc).__name__}>"
            fetch_note = f"Older page unavailable ({type(exc).__name__}); partial coverage."
            break
        pages += 1
        batch = data.get("messages", []) if isinstance(data, dict) else []
        for message in batch:
            key = message.get("id") or json.dumps(message, sort_keys=True)
            if key not in seen:
                seen.add(key)
                fetched.append(message)
        # Stop at the window boundary, but do not stop at 30 labels: collect a
        # broader pool before drawing the time-stratified sample.
        if start_date and any(
            (day := _created_at(m)) and day.date().isoformat() < start_date for m in batch
        ):
            break
        cursor = data.get("cursor", {}) if isinstance(data, dict) else {}
        older = cursor.get("max")
        if not cursor.get("more") or not older or older in cursors or not batch:
            break
        cursors.add(older)
        next_url = f"{url}?max={older}"
    else:
        fetch_note = "Page budget reached; partial coverage."
    messages = _within_window(fetched, start_date, end_date)
    if not messages:
        if start_date and end_date:
            gap = coverage_gap(
                (_created_at(m) for m in fetched), start_date, end_date,
                "StockTwits", f"messages about ${ticker.upper()}",
            )
            return gap or (
                f"<no StockTwits messages for ${ticker.upper()} within "
                f"{start_date}..{end_date}>"
            )
        return f"<no StockTwits messages found for ${ticker.upper()}>"

    pool_size = len(messages)
    messages = [m for m in messages if _label(m) in {"Bullish", "Bearish"} and _created_at(m)]
    labeled_pool_size = len(messages)
    messages = _time_sample(messages, max(0, limit), rng or random)
    if not messages:
        return (f"NO_DATA_AVAILABLE: no labeled StockTwits messages in {pool_size} in-window messages; "
                f"pages fetched: {pages}; not neutral sentiment")
    dates = [to_utc(_created_at(m)) for m in messages]
    dates = [d for d in dates if d is not None]
    coverage = (
        f"Sample timestamps: {min(dates).isoformat()} to {max(dates).isoformat()}; "
        f"span: {(max(dates) - min(dates)).total_seconds():g} seconds"
        if dates else "Sample timestamps unavailable"
    )
    lines = []
    bullish = bearish = unlabeled = 0
    for m in messages[:limit]:
        created = m.get("created_at", "")
        entities = m.get("entities") or {}
        sentiment_obj = entities.get("sentiment") or {}
        sentiment = sentiment_obj.get("basic") if isinstance(sentiment_obj, dict) else None
        body = re.sub(r"(?<!\w)@[A-Za-z0-9_]+", "[user]", _body(m)).replace("\n", " ").strip()
        if len(body) > 280:
            body = body[:280] + "…"

        if sentiment == "Bullish":
            bullish += 1
            tag = "Bullish"
        elif sentiment == "Bearish":
            bearish += 1
            tag = "Bearish"
        else:
            unlabeled += 1
            tag = "no-label"
        lines.append(f"[{created} · {tag}] {body}")

    total = bullish + bearish + unlabeled
    bull_pct = round(100 * bullish / total) if total else 0
    bear_pct = round(100 * bearish / total) if total else 0
    labeled = bullish + bearish
    labeled_ratio = (
        f"Labeled only: {labeled}; bullish {100 * bullish / labeled:.1f}%, "
        f"bearish {100 * bearish / labeled:.1f}%"
        if labeled else "Labeled only: 0; directional ratio unavailable"
    )
    summary = (
        f"Bullish: {bullish} ({bull_pct}%) · "
        f"Bearish: {bearish} ({bear_pct}%) · "
        f"Unlabeled: {unlabeled} · "
        f"Total: {total} time-stratified labeled messages · In-window pool: {pool_size}; "
        f"labeled pool: {labeled_pool_size}; pages fetched: {pages}\n"
        f"{labeled_ratio}\n{coverage}\n"
        "Label-only, time-balanced sample: ratios do not represent all users or the full feed. "
        f"Recent feed, not guaranteed monthly coverage. {fetch_note}"
    )
    return summary + "\n\n" + "\n".join(lines)


def _label(message):
    value = (message.get("entities") or {}).get("sentiment") or {}
    return value.get("basic") if isinstance(value, dict) else None


def _time_sample(messages, limit, rng):
    """Balance six equal-duration bins over the observed pool, then shuffle within bins."""
    if not messages or not limit:
        return []
    stamps = [to_utc(_created_at(m)).timestamp() for m in messages]
    low, high = min(stamps), max(stamps)
    bins = [[] for _ in range(6)]
    for message, stamp in zip(messages, stamps, strict=True):
        index = min(5, int(6 * (stamp - low) / (high - low))) if high > low else 0
        bins[index].append(message)
    for bucket in bins:
        rng.shuffle(bucket)
    selected = []
    while len(selected) < limit and any(bins):
        for bucket in bins:
            if bucket and len(selected) < limit:
                selected.append(bucket.pop())
    return selected
