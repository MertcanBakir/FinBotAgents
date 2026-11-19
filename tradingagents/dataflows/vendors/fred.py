"""FRED (Federal Reserve Economic Data) macro vendor.

Fetches macroeconomic time series — policy rates, Treasury yields, inflation,
labor, growth — from the St. Louis Fed's free API. Used by the news analyst to
ground macro commentary in actual numbers rather than headlines alone.

A free API key (https://fred.stlouisfed.org/docs/api/api_key.html) is read from
``FRED_API_KEY``; if it is unset the vendor raises ``FredNotConfiguredError`` so
the routing layer treats it as "unavailable" rather than a hard crash.
"""
import hashlib
import json
import logging
import os
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz
from dateutil.relativedelta import relativedelta

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError
from tradingagents.dataflows.files import locked, replace_file
from tradingagents.dataflows.net import get_scrubbed

logger = logging.getLogger(__name__)

FRED_API_BASE = "https://api.stlouisfed.org/fred"

# FRED's realtime clock runs on US Central (St. Louis Fed). It rejects a
# realtime date in its own future with a 400, so the vintage pin is clamped to
# this rather than the caller's local date (#1275). pytz (already a dependency)
# bundles its own tz database, so this works where system tzdata is absent.
FRED_TZ = pytz.timezone("America/Chicago")

# Network timeout (seconds) so a stalled request can't hang the agents,
# mirroring the Alpha Vantage client.
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for most monthly/quarterly series.
DEFAULT_LOOKBACK_DAYS = 365

# Rows cap for the rendered table: recent values matter most for a decision, and
# daily series (yields, VIX) over a long window would otherwise flood context.
MAX_ROWS = 40
_CACHE_TTL_SECONDS = 15 * 60

# Curated human-friendly aliases -> FRED series IDs. Anything not listed is used
# verbatim as a raw FRED series ID, so power users are never limited to this set.
MACRO_SERIES = {
    # Policy rate & Treasury yields
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "fed_funds": "FEDFUNDS",
    "2y_treasury": "DGS2",
    "10y_treasury": "DGS10",
    "30y_treasury": "DGS30",
    "10y_2y_spread": "T10Y2Y",
    "yield_curve": "T10Y2Y",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "inflation_expectations": "T10YIE",
    # Growth & output
    "real_gdp": "GDPC1",
    "gdp": "GDP",
    "industrial_production": "INDPRO",
    # Labor
    "unemployment_rate": "UNRATE",
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    # Money & markets
    "m2": "M2SL",
    "money_supply": "M2SL",
    "vix": "VIXCLS",
    "dollar_index": "DTWEXBGS",
    # Sentiment & housing
    "consumer_sentiment": "UMCSENT",
    "housing_starts": "HOUST",
    "retail_sales": "RSAFS",
    # Euro area (for non-US tickers such as Euronext .PA): FRED mirrors the ECB
    # policy rates, Eurostat HICP and GDP, and OECD long-term government yields,
    # so the same vintage pinning keeps euro-area data point-in-time.
    "ecb_deposit_rate": "ECBDFR",
    "ecb_main_refi_rate": "ECBMRRFR",
    "euro_hicp": "CP0000EZ19M086NEST",
    "euro_core_hicp": "00XEFDEZ19M086NEST",
    "euro_real_gdp": "CLVMNACSCAB1GQEA19",
    "germany_10y": "IRLTLT01DEM156N",
    "france_10y": "IRLTLT01FRM156N",
    "eur_usd": "DEXUSEU",
}


class FredNotConfiguredError(VendorNotConfiguredError):
    """Raised when FRED is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


def get_api_key() -> str:
    """Retrieve the FRED API key from the environment."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise FredNotConfiguredError(
            "FRED_API_KEY environment variable is not set. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html."
        )
    return api_key


def _resolve_series_id(indicator: str) -> str:
    """Map a friendly alias to a FRED series ID, or pass a raw ID through.

    Raises ``ValueError`` when the input is neither a known alias nor a plausible
    series ID — typically a descriptive phrase the LLM passed instead (e.g.
    "bank of japan rate"). FRED IDs are short and alphanumeric, so this rejects
    it up front with guidance rather than letting it 400 the API.
    """
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    candidate = indicator.strip().upper()
    # FRED series IDs never contain whitespace and are short; reject anything
    # else (a descriptive phrase the LLM passed) rather than 400ing the API.
    if not candidate or len(candidate) > 30 or any(c.isspace() for c in candidate):
        raise ValueError(
            f"'{indicator}' is not a known macro alias or a valid FRED series ID. "
            f"Use an alias (e.g. 'cpi', 'unemployment', '10y_treasury') or a raw "
            f"FRED series ID (e.g. 'CPIAUCSL')."
        )
    return candidate


def _fred_today() -> str:
    """FRED's current calendar date (US Central) as ``yyyy-mm-dd``.

    The vintage pin is clamped to this: FRED rejects a ``realtime_start`` after
    its own today with a 400, and ``as_of_date`` on a live run comes from the
    caller's local clock, which can already be tomorrow in Chicago.
    """
    return datetime.now(FRED_TZ).strftime("%Y-%m-%d")


def _request(path: str, params: dict) -> dict:
    """GET a FRED endpoint, surfacing FRED's JSON error body on a bad request."""
    api_key = get_api_key()
    response = get_scrubbed(
        f"{FRED_API_BASE}/{path}",
        params={**params, "api_key": api_key, "file_type": "json"},
        timeout=REQUEST_TIMEOUT,
        secret=api_key,
        passthrough=(400,),
    )
    # FRED returns 400 with a JSON {"error_message": ...} for unknown series IDs
    # or malformed params; turn that into a clear, actionable error.
    if response.status_code == 400:
        try:
            message = response.json().get("error_message", response.text)
        except ValueError:
            message = response.text
        raise ValueError(f"FRED request failed: {message}")
    return response.json()


def get_macro_data(
    indicator: str,
    as_of_date: str,
    look_back_days: int | None = None,
) -> str:
    """Fetch a FRED macroeconomic series as a formatted markdown report.

    Args:
        indicator: A friendly alias (e.g. "cpi", "unemployment", "10y_treasury")
            or a raw FRED series ID (e.g. "CPIAUCSL", "DGS10").
        as_of_date: The as-of date (yyyy-mm-dd). It bounds the observation window
            AND pins the data vintage: FRED is queried with the realtime bounds
            set to ``as_of_date`` (clamped to FRED's own today) so a historical
            run sees the values that were actually published by that date, not
            later revisions. Without this, revision-prone series (CPI, GDP, ...)
            would leak future information into a backtest (#1275).
        look_back_days: Trailing window length; ``None`` uses DEFAULT_LOOKBACK_DAYS.

    Returns:
        A markdown report with the series title, units, frequency, the latest
        value, the change over the window, and a recent observation table.
    """
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS

    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=look_back_days)).strftime("%Y-%m-%d")

    # Pin the data vintage. FRED defaults both realtime bounds to today, serving
    # the LATEST revision of every observation; a single-day realtime interval
    # asks for the values known as of the pin instead, on both the metadata and
    # observations requests (#1275). Clamp to FRED's today: on a live run
    # as_of_date is the caller's local date, which can be a day ahead of Chicago,
    # and a realtime date in FRED's future 400s -> the routing layer would then
    # drop macro data silently. A past as_of_date is unaffected, so historical
    # point-in-time behaviour is preserved.
    pit = min(as_of_date, _fred_today())

    # Invalid LLM-supplied indicator: return guidance rather than raising, so a
    # bad argument doesn't abort the run (the routing layer also degrades macro
    # data, but a specific message is more useful to the analyst).
    try:
        series_id = _resolve_series_id(indicator)
    except ValueError as e:
        return f"FRED: {e}"

    key = (series_id, as_of_date, start_date, pit)
    config = get_config()
    cache_root = config.get("fred_cache_dir") or config["data_cache_dir"]
    digest = hashlib.sha256(json.dumps(("macro-references-v2", *key)).encode()).hexdigest()
    path = Path(cache_root) / "fred" / f"{digest}.json"

    with ExitStack() as stack:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            stack.enter_context(locked(path))
        except OSError:
            return _fetch_report(*key)[0]

        cached = _read_cached_report(path)
        if cached is not None:
            return cached

        report, has_observations = _fetch_report(*key)
        if has_observations:
            try:
                replace_file(path, lambda temp: Path(temp).write_text(json.dumps(report), encoding="utf-8"))
            except OSError:
                logger.debug("Could not write FRED cache for %s", series_id)
        return report


def _read_cached_report(path: Path) -> str | None:
    try:
        if 0 <= time.time() - path.stat().st_mtime < _CACHE_TTL_SECONDS:
            report = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(report, str) and report.startswith("## FRED:"):
                return report
    except (OSError, ValueError):
        pass
    return None


def _fetch_report(series_id: str, as_of_date: str, start_date: str, pit: str) -> tuple[str, bool]:
    realtime = {"realtime_start": pit, "realtime_end": pit}
    meta = _request("series", {"series_id": series_id, **realtime}).get("seriess") or []
    if not meta:
        return (
            f"FRED series '{series_id}' not found. Pass a known alias "
            f"(e.g. 'cpi', 'unemployment') or a valid FRED series ID."
        ), False
    info = meta[0]
    title = info.get("title", series_id)
    units = info.get("units_short") or info.get("units", "")
    frequency = info.get("frequency", "")
    seasonal = info.get("seasonal_adjustment_short", "")

    observations = _request(
        "series/observations",
        {
            "series_id": series_id,
            "observation_start": start_date,
            "observation_end": as_of_date,
            "sort_order": "asc",
            **realtime,
        },
    ).get("observations", [])

    # FRED encodes a missing observation as ".".
    points = [
        (o["date"], o["value"])
        for o in observations
        if o.get("value") not in (".", None, "")
    ]

    header = (
        f"## FRED: {title} ({series_id})\n"
        f"- Units: {units}\n"
        f"- Frequency: {frequency}"
        f"{f' ({seasonal})' if seasonal else ''}\n"
        f"- Window: {start_date} to {as_of_date}\n"
        f"- Vintage: {pit} (FRED realtime_start = realtime_end; values available in this vintage)\n"
    )

    if not points:
        return header + (
            f"\nNo observations for {series_id} in this window at the {pit} "
            f"vintage. The series may report less frequently than the window "
            f"(try a longer look_back_days), or have no vintage published by "
            f"then (unpublished as of {pit}, or before ALFRED coverage begins)."
        ), False

    first_date, first_val = points[0]
    last_date, last_val = points[-1]
    try:
        delta = float(last_val) - float(first_val)
        base = float(first_val)
        pct = f" ({delta / base * 100:+.2f}%)" if base != 0 else ""
        summary = (
            f"\n**Latest:** {last_val} ({last_date}) | "
            f"**Change from {first_date} to {last_date}:** {delta:+.2f}{pct}, "
            f"from {first_val}\n"
        )
    except ValueError:
        summary = f"\n**Latest:** {last_val} ({last_date})\n"

    # Preserve calendar references before truncating the daily table. The analyst
    # can calculate long-window changes without receiving hundreds of rows.
    latest_day = date.fromisoformat(last_date)
    references = {last_date: last_val}
    for months in (1, 3, 12):
        target = latest_day - relativedelta(months=months)
        candidates = [(day, value) for day, value in points
                      if day <= target.isoformat() and (target - date.fromisoformat(day)).days <= 7]
        if candidates:
            day, value = max(candidates)
            references[day] = value
    header += f"- Reference observations: {json.dumps(references)}\n"
    shown = points
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]
        note = f"\n_(showing the most recent {MAX_ROWS} of {len(points)} observations)_\n"

    table = (
        "\n| Date | Value |\n| --- | --- |\n"
        + "\n".join(f"| {d} | {v} |" for d, v in shown)
        + "\n"
    )

    return header + summary + note + table, True
