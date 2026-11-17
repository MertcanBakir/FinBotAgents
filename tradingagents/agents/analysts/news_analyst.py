"""Collect the news evidence before asking the model for a single report."""

import csv
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date, timedelta
from io import StringIO

from dateutil.relativedelta import relativedelta
from langchain_core.messages import HumanMessage, SystemMessage

from tradingagents.agent_stats import record_data_outcome
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.agents.structured import NO_EXTERNAL_TOOLS
from tradingagents.agents.tools import (
    get_global_news,
    get_insider_transactions,
    get_macro_indicators,
    get_news,
)

logger = logging.getLogger(__name__)
_SALE = re.compile(r"^(?:sale|sell|sold)\b", re.IGNORECASE)
_TRANSACTION_FIELDS = ("transaction_type", "transaction_code", "transaction_description")
_MACRO_WINDOWS = (
    ("cpi", 400),
    ("core_pce", 450),
    ("fed_funds_rate", 90),
    ("10y_treasury", 30),
    ("unemployment", 90),
    ("yield_curve", 30),
)


def _insider_sales(data, trade_date):
    """Return explicitly identified sales within the analysis date's trailing year."""
    end = date.fromisoformat(trade_date)
    start = end - relativedelta(years=1)
    scope = f"Reported insider sales, {start} to {end} (inclusive)"

    def in_scope(row, date_field, type_fields):
        try:
            transaction_date = date.fromisoformat(row.get(date_field, ""))
        except (TypeError, ValueError):
            return False
        return start <= transaction_date <= end and any(
            _SALE.match(str(row.get(field) or "").strip()) for field in type_fields
        )

    if isinstance(data, str):
        if data.startswith(("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:", "No insider transactions reported",
                            "Data could not be retrieved.", "No data returned.")) or (
            data.startswith("# Insider Transactions for ") and "transactions are withheld" in data
        ):
            return f"{scope}\n{data}"
        header, _, body = data.partition("\n")
        if header.startswith("# Insider Transactions data for "):
            reader = csv.DictReader(StringIO(body))
            fields = reader.fieldnames or []
            if "Start Date" in fields and {"Text", "Transaction"}.intersection(fields):
                sales = [row for row in reader if in_scope(row, "Start Date", ("Text", "Transaction"))]
                if not sales:
                    return f"{scope}\nNo sales found in the provider's available records."
                output = StringIO()
                fields = [field for field in fields if field and any(row.get(field) for row in sales)]
                writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(sales)
                return f"{scope}\n{output.getvalue()}"
        try:
            data = json.loads(data)
        except ValueError:
            data = None

    if isinstance(data, dict) and isinstance(rows := data.get("data"), list):
        sales = [row for row in rows if isinstance(row, dict) and in_scope(
            row, "transaction_date", _TRANSACTION_FIELDS,
        )]
        if sales:
            return f"{scope}\n{json.dumps(sales, ensure_ascii=False)}"
        if not rows or any(isinstance(row, dict) and any(
            row.get(field) for field in _TRANSACTION_FIELDS
        ) for row in rows):
            return f"{scope}\nNo sales found in the provider's available records."
    return f"{scope}\nSales could not be verified from the provider's available data."


def _compact_macro(data, indicator):
    if not isinstance(data, str) or not data.startswith("## FRED:"):
        return data
    header, _, table = data.partition("\n| Date | Value |")
    if not table:
        return data
    points = {}
    for day, value in re.findall(r"^\| (\d{4}-\d{2}-\d{2}) \| (-?[\d.]+) \|$", table, re.M):
        points[date.fromisoformat(day)] = float(value)
    if not points:
        return data

    latest = max(points)
    details = []
    if indicator in {"cpi", "core_pce"}:
        for label, reference in (
            ("Month over month", latest - relativedelta(months=1)),
            ("Year over year", latest - relativedelta(years=1)),
        ):
            if points.get(reference):
                change = (points[latest] / points[reference] - 1) * 100
                details.append(f"{label} ({reference} to {latest}): {change:+.2f}%")
    recent = "\n".join(f"{day}: {points[day]:g}" for day in sorted(points)[-5:])
    return "\n".join([header.strip(), *details, "Most recent observations:", recent])


def _fetch(tool, **args):
    try:
        return tool.func(**args) or "No data returned."
    except Exception as exc:
        logger.warning("News Analyst could not fetch %s (%s)", tool.name, type(exc).__name__)
        return "Data could not be retrieved."


def _evidence(label, data):
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


def create_news_analyst(llm):
    def news_analyst_node(state):
        ticker = state["company_of_interest"]
        trade_date = state["trade_date"]
        start_date = (date.fromisoformat(trade_date) - timedelta(days=30)).isoformat()
        is_stock = state.get("asset_type", "stock") == "stock"
        instrument_context = get_instrument_context_from_state(state)
        classification = re.search(
            r"(?:Business classification|Industry|Sector): (.*?)(?=;|\. Do not substitute|$)",
            instrument_context,
        )
        sector = classification[1].split(" / ")[-1].rstrip(".") if classification else ticker
        with ThreadPoolExecutor(max_workers=3) as fred_pool:
            fred_jobs = {
                indicator: fred_pool.submit(
                    copy_context().run, _fetch, get_macro_indicators,
                    indicator=indicator, curr_date=trade_date,
                    look_back_days=days, trade_date=trade_date,
                )
                for indicator, days in _MACRO_WINDOWS
            }

            company = _fetch(get_news, ticker=ticker, start_date=start_date,
                             end_date=trade_date, trade_date=trade_date)
            global_news = _fetch(get_global_news, curr_date=trade_date, trade_date=trade_date,
                                 sector_query=f"{sector} industry news outlook")
            sales = (
                _insider_sales(_fetch(get_insider_transactions, ticker=ticker, trade_date=trade_date), trade_date)
                if is_stock else "Skipped: insider sales apply to stocks."
            )
            macro = [
                _evidence(f"FRED {indicator}", _compact_macro(job.result(), indicator))
                for indicator, job in fred_jobs.items()
            ]
        evidence = "\n\n".join([
            _evidence("Instrument news", company),
            _evidence("Insider sales", sales),
            _evidence("Global and sector news", global_news),
            *macro,
        ])
        system_message = (
            "You are the News Analyst. Write a concise Markdown report for the research team using "
            "the supplied evidence. Use these four level-three headings in order: Instrument News, "
            "Insider Sales in the Last Year, Macro and Sector Developments, and Data Coverage. "
            "Translate the headings into the report language. Cite the supplied date, publisher and link "
            "for material news when available. Flag high-impact claims supported by only one headline as unverified. "
            "Prioritize developments relevant to the instrument and omit unrelated headlines. "
            "For stocks, summarize material reported insider sales and identify any in the 30-day news window. "
            "Distinguish missing coverage from no sales in available records. For FRED, state dates, units and "
            "supplied changes. Use the calculated year-over-year figures for annual inflation and distinguish "
            "observation dates from release dates. Summarize source gaps without repeating the analysis. "
            "Omit ratings, transaction proposals, and trading recommendations. "
            + NO_EXTERNAL_TOOLS
            + get_language_instruction(preserve_decision_labels=False)
        )
        result = llm.invoke([
            SystemMessage(content=system_message),
            HumanMessage(content=(
                f"Analysis date: {trade_date}\nNews window: {start_date} to {trade_date}\n"
                f"{instrument_context}\n\n{evidence}"
            )),
        ])
        return {"messages": [result], "news_report": result.text}

    return news_analyst_node
