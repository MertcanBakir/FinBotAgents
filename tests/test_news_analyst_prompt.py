from collections import Counter
from threading import Barrier, Event
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

import tradingagents.agents.analysts.news_analyst as na
from tradingagents.agents.tools import get_news


@pytest.mark.unit
def test_get_news_takes_the_window_not_a_query():
    arg_names = set(get_news.tool_call_schema.model_json_schema()["properties"])
    assert arg_names == {"start_date", "end_date"}


@pytest.mark.unit
def test_news_prefetches_sources_and_writes_one_report(monkeypatch):
    from tradingagents.agents import tools

    calls = []
    first_wave = Barrier(4)
    yahoo_done = Event()
    first_indicators = {"cpi", "core_pce", "fed_funds_rate"}

    def fetch(method, *args, **options):
        calls.append((method, args, options))
        if method == "get_news":
            first_wave.wait(timeout=5)
            raise RuntimeError("provider failed")
        if method == "get_insider_transactions":
            assert sum(name == "get_macro_indicators" for name, _, _ in calls) == 3
            yahoo_done.set()
            return (
                "# Insider Transactions data for NVDA\n"
                "Start Date,Text,Transaction,Insider,Shares\n"
                "2025-01-15,Sale at price 100 per share.,,Boundary Seller,123\n"
                "2025-01-14,Sale at price 100 per share.,,Old Seller,456\n"
                "2026-01-15,Sale at price 100 per share.,,Latest Seller,789\n"
                "2026-01-16,Sale at price 100 per share.,,Future Seller,456\n"
                "2026-01-14,Stock Gift at price 0 per share.,,Donor,456\n"
                "2026-01-14,Purchase at price 100 per share.,,Buyer,456\n"
                "2026-01-14,Stock Award(Grant) at price 0 per share.,,Recipient,456\n"
            )
        if method == "get_macro_indicators":
            if args[0] in first_indicators:
                first_wave.wait(timeout=5)
                assert yahoo_done.wait(timeout=5)
            return (
                "## FRED: Test series\n- Units: Index\n"
                "\n| Date | Value |\n| --- | --- |\n"
                "| 2024-12-01 | 98 |\n| 2025-12-01 | 100 |\n"
            )
        return "Source data"

    def write_report(messages):
        assert Counter(name for name, _, _ in calls) == {
            "get_news": 1, "get_global_news": 1, "get_insider_transactions": 1,
            "get_macro_indicators": 6,
        }
        return AIMessage(content="News report")

    monkeypatch.setattr(tools, "route_to_vendor", fetch)
    llm = MagicMock()
    llm.invoke.side_effect = write_report

    result = na.create_news_analyst(llm)({
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "instrument_context": "Company: NVIDIA; Business classification: Technology / Semiconductors",
    })

    llm.invoke.assert_called_once()
    llm.bind_tools.assert_not_called()
    system, data = llm.invoke.call_args.args[0]
    assert result["news_report"] == "News report"
    yahoo_calls = [call for call in calls if call[0] != "get_macro_indicators"]
    assert [name for name, _, _ in yahoo_calls] == ["get_news", "get_global_news", "get_insider_transactions"]
    assert yahoo_calls[0] == ("get_news", ("NVDA", "2025-12-16", "2026-01-15"), {})
    assert yahoo_calls[1][2] == {"sector_query": "Semiconductors industry news outlook"}
    assert "Status: Unavailable" in data.content
    assert "Year over year (2024-12-01 to 2025-12-01): +2.04%" in data.content
    assert "four level-three headings" in system.content
    assert "Insider Sales in the Last Year" in system.content
    assert "Reported insider sales, 2025-01-15 to 2026-01-15 (inclusive)" in data.content
    assert "Boundary Seller" in data.content and "Latest Seller" in data.content
    assert not any(name in data.content for name in ("Old Seller", "Future Seller", "Donor", "Buyer", "Recipient"))
