"""Evidence loading, status recording and context-preserving task submission."""
import json
import logging
from contextvars import copy_context

from tradingagents.agent_stats import record_data_outcome

logger = logging.getLogger(__name__)


def submit_context(pool, fn, *args, **kwargs):
    """Submit with an independent copy of the run context for each task."""
    return pool.submit(copy_context().run, fn, *args, **kwargs)


def fetch_evidence(tool, **args):
    try:
        return tool.func(**args) or "No data returned."
    except Exception as exc:
        logger.warning("News Analyst could not fetch %s (%s)", tool.name, type(exc).__name__)
        return "Data could not be retrieved."


def format_evidence(label, data):
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    lowered = data.lower()
    lines = lowered.splitlines()
    if lowered.startswith("skipped:") or "transactions are withheld" in lowered:
        status, description = "skipped", "Skipped"
    elif any(line.startswith(("data_unavailable:", "data could not be retrieved",
                              "sales could not be verified")) for line in lines) or (
        lowered.startswith("<") and " unavailable" in lowered
    ):
        status, description = "unavailable", "Unavailable"
    elif any(line.startswith(("no ", "no_data_available:", "fred series '", "fred: ")) for line in lines):
        status, description = "empty", "No usable records"
    else:
        status, description = "received", "Data received"
    record_data_outcome(label, status)
    return f"### {label}\nStatus: {description}\n{data}"
