from io import StringIO

import pytest
from rich.console import Console

from cli import display
from cli.stats_handler import StatsCallbackHandler


@pytest.fixture
def buffer(monkeypatch):
    value = display.MessageBuffer()
    monkeypatch.setattr(display, "message_buffer", value)
    return value


def render(layout):
    stream = StringIO()
    Console(file=stream, width=160, height=80, force_terminal=False).print(layout)
    return stream.getvalue()


def test_live_display_starts_with_waiting_and_no_stats(buffer):
    layout = display.create_layout()
    display.update_display(layout)
    text = render(layout)
    assert "Waiting for analysis report" in text
    assert "Agents: 0/0" in text and "Reports: 0/0" in text


def test_report_counts_only_after_responsible_agent_finishes(buffer):
    buffer.init_for_analysis(["MARKET"])
    buffer.update_report_section("market_report", "Market evidence")
    assert buffer.get_completed_reports_count() == 0
    buffer.update_agent_status("Market Analyst", "completed")
    assert buffer.get_completed_reports_count() == 1
    buffer.update_agent_status("Not an agent", "completed")
    buffer.update_report_section("not_a_report", "ignored")
    assert "not_a_report" not in buffer.report_sections


@pytest.mark.parametrize("status", ["pending", "in_progress", "completed", "error", "unexpected"])
def test_live_display_renders_agent_states(buffer, status):
    buffer.init_for_analysis(["market", "news"])
    buffer.update_agent_status("Market Analyst", status)
    buffer.update_agent_status("News Analyst", status)
    layout = display.create_layout()
    display.update_display(layout, stats_handler=StatsCallbackHandler(), start_time=1)
    text = render(layout)
    assert "Market Analyst" in text and "News Analyst" in text
    assert "Reports: 0/" in text and "Tokens: --" in text


def test_live_display_shows_report_messages_tools_and_tokens(buffer):
    buffer.init_for_analysis(["market"])
    buffer.update_report_section("market_report", "Market evidence")
    buffer.update_agent_status("Market Analyst", "completed")
    buffer.add_message("Info", "message")
    buffer.add_tool_call("lookup", {"ticker": "ABC"})
    stats = StatsCallbackHandler()
    stats.tokens_in, stats.tokens_out = 1200, 20
    layout = display.create_layout()
    display.update_display(layout, stats_handler=stats)
    text = render(layout)
    assert "Market evidence" in text and "message" in text and "lookup" in text
    assert "1.2k" in text and "20" in text


def test_complete_report_shows_each_available_team(monkeypatch):
    stream = StringIO()
    monkeypatch.setattr(display, "console", Console(file=stream, width=100))
    state = {
        "market_report": "market evidence", "macro_report": "macro evidence",
        "news_report": "news evidence", "fundamentals_report": "fundamentals evidence",
        "investment_debate_state": {"bull_history": "bull", "bear_history": "bear"},
        "investment_plan": "research decision", "trader_investment_plan": "trading plan",
        "risk_debate_state": {"aggressive_history": "aggressive", "conservative_history": "conservative",
                              "neutral_history": "neutral"},
        "final_trade_decision": "final decision",
    }
    display.display_complete_report(state)
    text = stream.getvalue()
    for expected in ("Market Analyst", "Macro Analyst", "News Analyst", "Fundamentals Analyst",
                     "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
                     "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager"):
        assert expected in text


@pytest.mark.parametrize("state", [{}, {"investment_debate_state": {"count": 1},
                                      "risk_debate_state": {"count": 1}}])
def test_complete_report_omits_empty_sections(monkeypatch, state):
    stream = StringIO()
    monkeypatch.setattr(display, "console", Console(file=stream, width=100))
    display.display_complete_report(state)
    assert "Complete Analysis Report" in stream.getvalue()
    assert "Research Team Decision" not in stream.getvalue()
    assert "Risk Management Team Decision" not in stream.getvalue()
