from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from finbotagents.agents.analysts import macro_analyst, news_analyst


def test_news_collects_social_for_thirty_days_and_calls_model_once(monkeypatch):
    calls = []
    monkeypatch.setattr(news_analyst, 'fetch_evidence', lambda tool, **kw:
                        calls.append((tool.name, kw)) or 'evidence')
    for name in ('fetch_stocktwits_messages',):
        monkeypatch.setattr(news_analyst, name, lambda *a, _name=name, **kw:
                            calls.append((_name, kw)) or '<source unavailable>')
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content='Combined report')
    result = news_analyst.create_news_analyst(llm)({
        'company_of_interest': 'NVDA', 'trade_date': '2026-01-15',
        'instrument_context': 'Company: NVIDIA; Sector: Semiconductors',
    })
    llm.invoke.assert_called_once()
    assert result['news_report'].startswith('Combined report')
    assert result['messages'][0].content == result['news_report']
    assert {name for name, _ in calls} == {
        'get_news', 'get_global_news', 'get_insider_transactions',
        'fetch_stocktwits_messages',
    }
    for name, options in calls:
        if name in {'get_news', 'fetch_stocktwits_messages'}:
            assert options['start_date'] == '2025-12-16'
            assert options['end_date'] == '2026-01-15'
    sector = next(options for name, options in calls if name == 'get_global_news')
    assert sector['sector_only'] is True
    prompt = llm.invoke.call_args.args[0]
    assert 'never present that sample as a monthly trend' in prompt[0].content
    assert 'Status: Unavailable' in prompt[1].content


def test_macro_collects_six_series_and_global_news_once(monkeypatch):
    calls = []
    monkeypatch.setattr(macro_analyst, 'fetch_evidence', lambda tool, **kw:
                        calls.append((tool.name, kw)) or 'macro evidence')
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content='Macro report')
    result = macro_analyst.create_macro_analyst(llm)({
        'company_of_interest': 'NVDA', 'trade_date': '2026-01-15',
        'instrument_context': 'Company: NVIDIA', 'analysis_horizon': 'medium_3m',
    })
    llm.invoke.assert_called_once()
    assert result['macro_report'] == 'Macro report'
    assert sum(name == 'get_macro_indicators' for name, _ in calls) == 6
    assert sum(name == 'get_global_news' for name, _ in calls) == 1
    assert all('sector_query' not in options for _, options in calls)
    assert 'medium_3m' in llm.invoke.call_args.args[0][1].content


@pytest.mark.parametrize('module,factory', [
    (news_analyst, 'create_news_analyst'),
    (macro_analyst, 'create_macro_analyst'),
])
def test_model_failure_does_not_trigger_a_second_call(monkeypatch, module, factory):
    monkeypatch.setattr(module, 'fetch_evidence', lambda *a, **k: 'Data could not be retrieved.')
    if module is news_analyst:
        monkeypatch.setattr(module, 'fetch_stocktwits_messages', lambda *a, **k: '')
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError('provider failed')
    with pytest.raises(RuntimeError, match='provider failed'):
        getattr(module, factory)(llm)({
            'company_of_interest': 'NVDA', 'trade_date': '2026-01-15',
            'instrument_context': 'Company: NVIDIA',
        })
    llm.invoke.assert_called_once()


def test_sector_news_does_not_also_search_global_queries(monkeypatch):
    from finbotagents.dataflows.vendors.yahoo import news
    queries = []

    class Search:
        def __init__(self, query, **kwargs):
            queries.append(query)
            self.news = []

    monkeypatch.setattr(news.yf, 'Search', Search)
    news.get_global_news_yfinance('2026-01-15', sector_query='Semiconductors', sector_only=True)
    assert queries == ['Semiconductors']
