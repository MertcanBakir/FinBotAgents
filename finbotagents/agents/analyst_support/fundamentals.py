"""Prepare period-limited financial statement evidence."""
import re
from calendar import monthrange
from datetime import date

_SEC_PERIOD_ROW = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: \(\d+ months\))?,")


def _months_before(day, months):
    year, month_index = divmod(day.year * 12 + day.month - 1 - months, 12)
    month = month_index + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def limit_statement_history(data, months):
    """Keep recent SEC period rows and their original headers and units."""
    if not isinstance(data, str) or "\n# SEC EDGAR facts filed on or before " not in data:
        return data
    lines = data.splitlines(keepends=True)
    periods = {
        index: date.fromisoformat(match[1])
        for index, line in enumerate(lines)
        if (match := _SEC_PERIOD_ROW.match(line))
    }
    if not periods:
        return data
    latest = max(periods.values())
    cutoff = _months_before(latest, months)
    return "".join(
        line for index, line in enumerate(lines)
        if index not in periods or periods[index] >= cutoff
    )
