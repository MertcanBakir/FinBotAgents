import json
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from finbotagents.agents.analyst_support.insider_sales import prepare_insider_sales
from finbotagents.agents.analyst_support.news_render import (
    compose_news_report,
    insider_table,
    social_sample_line,
)
from finbotagents.agents.analysts import news_analyst


def _sales():
    return prepare_insider_sales({'data': [{
        'transaction_date': '2026-01-14', 'transaction_type': 'Sale',
        'executive': 'Person | <script>', 'executive_title': 'Director',
        'shares': '10', 'transaction_value': '1234', 'currency': 'USD',
    }]}, '2026-01-15')


def test_table_is_rendered_with_average_and_no_coverage_or_important_sales_section():
    report = insider_table(_sales(), '2026-01-15', tr=True)
    assert '123,40 (USD)' in report
    assert 'Person &#124; &lt;script&gt;' in report
    assert 'Son 30 gün' in report and '1 satış kaydı' in report
    assert 'Önemli satışlar' not in report and 'eşleşen pay' not in report
    assert 'Yöntem' not in report
    assert report.count('Not:') == 1
    assert len(report.split('Not:')[1].splitlines()) == 1
    assert '|---|---|---:|---:|---:|' in report


def test_partial_price_is_marked_without_an_extra_table_column():
    source = _sales().split('\n', 1)
    data = json.loads(source[1])
    data['by_person_role_currency'][0]['known_shares'] = '20'
    report = insider_table(source[0] + '\n' + json.dumps(data), '2026-01-15')
    assert '123.40* (USD)' in report
    assert 'only records with computable prices' in report
    assert 'coverage |' not in report


def test_source_failure_is_not_presented_as_no_sales():
    assert 'unavailable' in insider_table('DATA_UNAVAILABLE: down', '2026-01-15')
    assert 'No sales found' in insider_table(
        'Reported insider sales\nNo sales found in the provider records.', '2026-01-15')


def test_missing_currency_renders_usd_with_a_brief_default_note():
    evidence = prepare_insider_sales({'data': [{
        'transaction_date': '2026-01-14', 'transaction_type': 'Sale',
        'executive': 'A', 'shares': '10', 'transaction_value': '100', 'currency': ' ',
    }]}, '2026-01-15')
    report = insider_table(evidence, '2026-01-15', tr=True)
    assert '10,00 (USD)' in report
    assert 'USD kabul edilir' in report
    assert 'belirtilmemiş)' not in report


def test_social_counts_are_composed_once_in_the_correct_section():
    evidence = ('Bullish: 17 (57%) · Bearish: 13 (43%) · Total: 30\n'
                'Sample timestamps: 2026-01-15T10:00:00+00:00 to '
                '2026-01-15T11:00:00+00:00; span: 3600 seconds')
    sample = social_sample_line(evidence, tr=True)
    assert '%56.7' in sample and '3.600 saniye' in sample
    report = compose_news_report('### NEWS\nEvents\n### REACTION\nThemes\n### CATALYSTS\nNext',
                                 'TABLE', sample, tr=True)
    assert report.count(sample) == 1
    assert report.index('Events') < report.index('TABLE') < report.index('Örneklem:') < report.index('Themes')
    assert '### REACTION' not in report and '### NEWS' not in report
    assert '### İzlenecek Gelişmeler' in report


def test_news_model_does_not_receive_or_reproduce_the_sales_table(monkeypatch):
    def fetch(tool, **kwargs):
        if tool.name == 'get_insider_transactions':
            return {'data': [{'transaction_date': '2026-01-14', 'transaction_type': 'Sale',
                             'executive': 'Secret executive', 'shares': '10',
                             'transaction_value': '100', 'currency': 'USD'}]}
        return 'news evidence'

    monkeypatch.setattr(news_analyst, 'fetch_evidence', fetch)
    monkeypatch.setattr(news_analyst, 'fetch_stocktwits_messages', lambda *a, **k: 'NO_DATA_AVAILABLE: no labels')
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content='### NEWS\nEvents\n### REACTION\nViews\n### CATALYSTS\nNext')
    result = news_analyst.create_news_analyst(llm)({
        'company_of_interest': 'NVDA', 'trade_date': '2026-01-15', 'instrument_context': 'NVDA',
    })
    llm.invoke.assert_called_once()
    assert 'Secret executive' not in llm.invoke.call_args.args[0][1].content
    assert 'Secret executive' in result['news_report']
    assert '10.00 (USD)' in result['news_report']
    assert 'No usable labeled social data' in result['news_report']
    assert result['messages'][0].content == result['news_report']
