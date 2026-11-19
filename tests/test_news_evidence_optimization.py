import json
import random
from unittest.mock import patch

from tradingagents.agents.analyst_support.insider_sales import prepare_insider_sales
from tradingagents.agents.analyst_support.news import deduplicate_news_blocks
from tradingagents.dataflows.vendors import stocktwits


def test_random_sampling_uses_filtered_pool_and_removes_usernames():
    messages = [{
        'created_at': f'2026-01-09T15:{i:02d}:00Z',
        'body': f'message-{i} hello @private_handle',
        'user': {'username': 'secret_author'},
        'entities': {'sentiment': {'basic': 'Bullish' if i % 2 else 'Bearish'}},
    } for i in range(50)]
    messages.append({'created_at': '2025-01-01T00:00:00Z', 'body': 'OUTSIDE WINDOW'})

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({'messages': messages}).encode()

    with patch.object(stocktwits, 'urlopen', return_value=Response()):
        result = stocktwits.fetch_stocktwits_messages('NVDA', start_date='2026-01-01',
            end_date='2026-01-09', rng=random.Random(42))
    assert 'Total: 30' in result and 'In-window pool: 50' in result
    assert 'OUTSIDE WINDOW' not in result
    assert 'secret_author' not in result and 'private_handle' not in result
    assert result.count('hello [user]') == 30
    selected = [line for line in result.splitlines() if line.startswith('[')]
    assert len(selected) == 30
    assert any(int(line.split('message-')[1].split()[0]) >= 30 for line in selected)
    bullish = sum(' · Bullish]' in line for line in selected)
    assert f'Bullish: {bullish} ' in result
    assert f'bullish {100 * bullish / 30:.1f}%' in result
    assert 'span:' in result


def test_insider_summary_separates_currencies_and_missing_values():
    rows = [
        {'transaction_date': '2026-01-14', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '10', 'transaction_value': '100', 'currency': 'USD'},
        {'transaction_date': '2026-01-13', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '20', 'transaction_value': '300', 'currency': 'USD'},
        {'transaction_date': '2025-03-01', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '5', 'transaction_value': '50', 'currency': 'EUR'},
        {'transaction_date': '2026-01-12', 'transaction_type': 'Sale', 'executive': 'B',
         'share_price': '90', 'shares': '2'},
        {'transaction_date': '2026-01-11', 'transaction_type': 'Purchase', 'executive': 'C'},
        {'transaction_date': '2024-01-01', 'transaction_type': 'Sale', 'executive': 'Old'},
    ]
    result = prepare_insider_sales({'data': rows + [rows[0]]}, '2026-01-15')
    data = json.loads(result.split('\n', 1)[1])
    groups = {(r['person'], r['currency']): r for r in data['by_person_role_currency']}
    assert data['unique_records'] == 4 and data['exact_duplicates_removed'] == 1
    assert groups['A', 'USD']['known_value'] == '400'
    assert groups['A', 'USD']['calculated_average_price'] == '13.33'
    assert groups['A', 'EUR']['known_value'] == '50'
    assert groups['B', 'USD']['known_value'] == '0'
    assert groups['B', 'USD']['missing_value'] == 1
    assert groups['B', 'USD']['currency_defaulted'] is True
    assert len(data['last_30_days']) == 3
    assert data['latest_transaction']['date'] == '2026-01-14'


def test_insider_csv_summary_defaults_missing_currency_to_usd():
    source = ('# Insider Transactions data for NVDA\n'
              'Start Date,Text,Insider,Shares,Value\n'
              '2026-01-14,Sale,A,10,100\n2026-01-13,Sale,A,20,300\n')
    data = json.loads(prepare_insider_sales(source, '2026-01-15').split('\n', 1)[1])
    group, = data['by_person_role_currency']
    assert group['known_shares'] == '30'
    assert group['known_value'] == '400'
    assert group['currency'] == 'USD'
    assert group['currency_defaulted'] is True
    assert group['calculated_average_price'] == '13.33'
    assert 'defaulted to USD' in group['average_price_note']


def test_time_bins_keep_sparse_periods_and_exclude_unlabeled_posts():
    messages = [{
        'id': i, 'created_at': f'2026-01-{day:02d}T12:00:00Z',
        'body': str(i), 'entities': {'sentiment': {'basic': 'Bullish'}},
    } for i, day in enumerate([1] * 50 + [6, 12, 18, 24, 30])]
    sample = stocktwits._time_sample(messages, 10, random.Random(42))
    assert len(sample) == 10
    assert {m['created_at'][8:10] for m in sample} == {'01', '06', '12', '18', '24', '30'}


def test_pagination_collects_labels_and_deduplicates_boundary_posts():
    def message(i, label):
        return {'id': i, 'created_at': f'2026-01-09T15:{i:02d}:00Z',
                'body': f'body-{i}', 'entities': {'sentiment': {'basic': label}}}

    payloads = [
        {'messages': [message(4, None), message(3, 'Bullish')],
         'cursor': {'more': True, 'max': 3}},
        {'messages': [message(3, 'Bullish'), message(2, 'Bearish')],
         'cursor': {'more': False}},
    ]
    urls = []

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    def fetch(req, timeout):
        urls.append(req.full_url)
        return Response(payloads[len(urls) - 1])

    with patch.object(stocktwits, 'urlopen', side_effect=fetch):
        result = stocktwits.fetch_stocktwits_messages('NVDA', rng=random.Random(0))
    assert urls[-1].endswith('?max=3')
    assert 'Total: 2' in result and 'labeled pool: 2' in result
    assert 'body-4' not in result and result.count('body-3') == 1
    assert 'pages fetched: 2' in result


def test_deduplication_keeps_richest_copy_across_sources_and_distinct_reporting():
    blocks = {
        'company': '## News\n### Event (source: A, published 2026-01-01)\nshort\n'
                   'Link: https://example.com/story?utm_source=x\n',
        'sector': '## Sector\n### Event (source: A, published 2026-01-01)\n'
                  'A longer summary with additional evidence\nLink: https://example.com/story\n'
                  '\n### Event (source: B, published 2026-01-01)\nIndependent view\n'
                  'Link: https://other.com/story\n',
    }
    result = deduplicate_news_blocks(blocks)
    all_text = '\n'.join(result.values())
    assert all_text.count('source: A') == 1
    assert 'longer summary' in all_text and 'Independent view' in all_text
    assert 'All articles duplicate' in result['company']


def test_average_uses_only_matching_value_and_positive_share_records():
    rows = [
        {'transaction_date': '2026-01-14', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '10', 'transaction_value': '100', 'currency': 'USD'},
        {'transaction_date': '2026-01-13', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '90', 'currency': 'USD'},
        {'transaction_date': '2026-01-12', 'transaction_type': 'Sale', 'executive': 'A',
         'shares': '0', 'transaction_value': '999', 'currency': 'USD'},
    ]
    data = json.loads(prepare_insider_sales({'data': rows}, '2026-01-15').split('\n', 1)[1])
    group, = data['by_person_role_currency']
    assert group['calculated_average_price'] == '10.00'
    assert group['priced_shares'] == '10'


def test_unlabeled_feed_is_not_presented_as_neutral_or_invented_labels():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({'messages': [{
                'id': 1, 'created_at': '2026-01-09T12:00:00Z', 'body': 'NO LABEL',
            }]}).encode()

    with patch.object(stocktwits, 'urlopen', return_value=Response()):
        result = stocktwits.fetch_stocktwits_messages('NVDA')
    assert result.startswith('NO_DATA_AVAILABLE:')
    assert 'not neutral sentiment' in result
