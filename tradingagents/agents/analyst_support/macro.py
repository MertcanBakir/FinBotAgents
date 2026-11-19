"""Horizon-aware macro evidence preparation, with bounded model input."""
import contextlib
import json
import re
from datetime import date

from dateutil.relativedelta import relativedelta

from tradingagents.horizons import parse_analysis_horizon


def macro_windows(trade_date, horizon):
    horizon = parse_analysis_horizon(horizon)
    inflation, monthly, daily = {
        'short_1m': (15, 6, 3),
        'medium_3m': (15, 12, 6),
        'long_12m': (24, 24, 12),
    }[horizon]
    end = date.fromisoformat(trade_date)
    def days(months):
        return (end - (end - relativedelta(months=months))).days
    # Daily observations may end before the requested date (weekends/holidays).
    # Keep a small boundary buffer so a 12-month comparison is still reachable.
    return (("cpi", days(inflation)), ("core_pce", days(inflation)),
            ("fed_funds_rate", days(monthly)), ("10y_treasury", days(daily) + 10),
            ("unemployment", days(monthly)), ("yield_curve", days(daily) + 10))


def compact_macro(data, indicator):
    if not isinstance(data, str) or not data.startswith("## FRED:"):
        return data
    header, _, table = data.partition("\n| Date | Value |")
    if not table:
        return data
    points = {}
    if metadata := re.search(r'^- Reference observations: (.*)$', header, re.M):
        with contextlib.suppress(ValueError, TypeError, AttributeError):
            points.update({date.fromisoformat(k): float(v) for k, v in json.loads(metadata[1]).items()})
    points.update({date.fromisoformat(day): float(value)
                   for day, value in re.findall(r"^\| (\d{4}-\d{2}-\d{2}) \| (-?[\d.]+) \|$", table, re.M)})
    if not points:
        return data
    # Do not forward raw window-relative percentages or reference JSON as prose:
    # inflation needs calendar YoY; rate changes need basis/percentage points.
    metadata_lines = [line for line in header.splitlines()
                      if line.startswith(('## FRED:', '- Units:', '- Frequency:', '- Window:', '- Vintage:'))]
    latest = max(points)
    details = [f"Latest observation: {latest}: {points[latest]:.2f}"]
    for months in ((1, 12) if indicator in {'cpi', 'core_pce'} else (1, 3, 12)):
        target = latest - relativedelta(months=months)
        if indicator in {'cpi', 'core_pce', 'fed_funds_rate', 'unemployment'}:
            reference = target if target in points else None
        else:
            candidates = [day for day in points if day <= target and (target-day).days <= 7]
            reference = max(candidates) if candidates else None
        label = ('Month over month' if months == 1 else 'Year over year') if indicator in {'cpi', 'core_pce'} else f'{months}-month change'
        if reference is None:
            details.append(f'{label}: unavailable (reference observation missing).')
            continue
        delta = points[latest] - points[reference]
        if indicator in {'cpi', 'core_pce'}:
            value = f'{100 * delta / points[reference]:+.2f}%' if points[reference] else 'unavailable (zero base)'
        elif indicator == 'unemployment':
            value = f'{delta:+.2f} percentage points'
        else:
            value = f'{delta * 100:+.2f} basis points'
        details.append(f'{label} ({reference} to {latest}): {value}')
    recent = "\n".join(f"{day}: {points[day]:.2f}" for day in sorted(points)[-5:])
    return "\n".join([*metadata_lines, *details, "Most recent observations:", recent])


def macro_news_only(data):
    """Keep headlines with explicit economy/policy signals, not generic company news."""
    if not isinstance(data, str) or '\n### ' not in data:
        return data
    prefix, *articles = data.split('\n### ')
    signal = re.compile(r'\b(fed|federal reserve|central bank|interest rates?|inflation|cpi|pce|gdp|'
                        r'unemployment|jobs report|payrolls|recession|treasury|bond yields?|'
                        r'tariffs?|monetary|fiscal|oil prices?|economic growth)\b', re.I)
    kept = [article for article in articles if signal.search(article.splitlines()[0])]
    return (prefix + '\n### ' + '\n### '.join(kept) if kept
            else 'NO_DATA_AVAILABLE: No relevant macro headlines in the returned search sample.')
