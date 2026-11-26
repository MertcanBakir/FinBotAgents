"""Market data loading and validated indicator-group selection."""
import json

from finbotagents.agent_stats import track_data
from finbotagents.dataflows.vendors.yahoo.snapshot import load_verified_market_rows

MAX_GROUPS = 8


@track_data("market_price_history")
def load_prices(symbol, date, *, history_rows, interval):
    return load_verified_market_rows(symbol, date, history_rows=history_rows, interval=interval)


def select_indicator_groups(text, optional, required):
    selected = list(required)
    notes = []
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.partition("\n")[2][:-3].strip()
    try:
        items = json.loads(text)["groups"]
    except (ValueError, KeyError, TypeError):
        return selected, ["No readable optional indicator group selection."]
    if not isinstance(items, list):
        return selected, ["No readable optional indicator group selection."]
    invalid_count = 0
    overflow = False
    for item in items:
        if not isinstance(item, str) or item not in optional:
            invalid_count += 1
        elif item not in selected:
            if len(selected) < MAX_GROUPS:
                selected.append(item)
            else:
                overflow = True
    if invalid_count:
        notes.append(f"{invalid_count} requested indicator groups are outside the allowed list.")
    if overflow:
        notes.append("Additional indicator groups omitted because the limit is eight.")
    if not any(group not in required for group in selected):
        notes.append("No optional indicator groups selected.")
    return selected, notes
