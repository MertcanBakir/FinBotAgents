from datetime import date, timedelta

import pytest
from dateutil.relativedelta import relativedelta

from finbotagents.agents.analyst_support.macro import compact_macro, macro_news_only, macro_windows
from finbotagents.dataflows.vendors import fred
from finbotagents.technical_analysis.reporting import render_zones
from finbotagents.technical_analysis.zones import ZoneEvidence


@pytest.mark.parametrize('horizon,inflation,monthly,daily', [
    ('short_1m', 15, 6, 3), ('medium_3m', 15, 12, 6), ('long_12m', 24, 24, 12),
])
def test_windows_follow_horizon_and_cover_annual_inflation(horizon, inflation, monthly, daily):
    end = date(2026, 11, 19)
    windows = dict(macro_windows(str(end), horizon))
    assert windows['cpi'] == (end - (end - relativedelta(months=inflation))).days
    assert windows['core_pce'] == windows['cpi']
    assert windows['unemployment'] == (end - (end - relativedelta(months=monthly))).days
    assert windows['fed_funds_rate'] == windows['unemployment']
    assert windows['10y_treasury'] == (end - (end - relativedelta(months=daily))).days + 10
    assert windows['yield_curve'] == windows['10y_treasury']


def _series(points):
    return ('## FRED: Test\n- Units: Percent\n- Vintage: 2026-11-19\n'
            '**Change from window start:** +25%\n\n| Date | Value |\n| --- | --- |\n'
            + '\n'.join(f'| {day} | {value} |' for day, value in points))


def test_inflation_uses_exact_calendar_references_not_window_change():
    text = compact_macro(_series([('2025-10-01', 100), ('2026-09-01', 103),
                                 ('2026-10-01', 104)]), 'cpi')
    assert 'Year over year (2025-10-01 to 2026-10-01): +4.00%' in text
    assert 'Month over month (2026-09-01 to 2026-10-01): +0.97%' in text
    assert 'Change from window start' not in text
    assert '- Vintage: 2026-11-19' in text
    missing = compact_macro(_series([('2025-11-01', 100), ('2026-10-01', 104)]), 'cpi')
    assert 'Year over year: unavailable' in missing


def test_rates_use_basis_points_and_unemployment_uses_percentage_points():
    points = [('2026-09-01', 4), ('2026-10-01', 4.25)]
    assert '+25.00 basis points' in compact_macro(_series(points), 'fed_funds_rate')
    assert '+0.25 percentage points' in compact_macro(_series(points), 'unemployment')


def test_daily_change_uses_prior_observation_with_bounded_weekend_gap():
    points = [('2026-10-16', 4), ('2026-11-18', 4.25)]
    text = compact_macro(_series(points), '10y_treasury')
    assert '1-month change (2026-10-16 to 2026-11-18): +25.00 basis points' in text
    stale = compact_macro(_series([('2026-10-01', 4), ('2026-11-18', 4.25)]), '10y_treasury')
    assert '1-month change: unavailable' in stale


def test_daily_annual_reference_survives_fred_table_truncation(monkeypatch):
    latest = date(2026, 11, 18)
    points = [{'date': '2025-11-18', 'value': '4.00'}]
    points += [{'date': str(latest - timedelta(days=i)), 'value': '4.25'}
               for i in reversed(range(100))]

    def request(path, params):
        assert params['realtime_start'] == params['realtime_end'] == '2026-11-19'
        return ({'seriess': [{'title': 'Treasury', 'units': 'Percent', 'frequency': 'Daily'}]}
                if path == 'series' else {'observations': points})

    monkeypatch.setattr(fred, '_request', request)
    raw, available = fred._fetch_report('DGS10', '2026-11-19', '2025-11-09', '2026-11-19')
    assert available
    assert '| 2025-11-18 |' not in raw
    text = compact_macro(raw, '10y_treasury')
    assert '12-month change (2025-11-18 to 2026-11-18): +25.00 basis points' in text
    assert len(text.splitlines()) < 20


def test_macro_headlines_exclude_company_only_stories():
    text = '## Global\n### NVIDIA CEO news\ncompany\n### Fed interest rate decision\nmacro'
    filtered = macro_news_only(text)
    assert 'NVIDIA' not in filtered and 'Fed interest rate decision' in filtered
    assert macro_news_only('## Global\n### NVIDIA CEO news\ncompany').startswith('NO_DATA_AVAILABLE:')


def test_zone_display_rounding_does_not_change_calculated_bounds():
    zone = ZoneEvidence(252, 'support', date(2026, 1, 1), (211.5891, 215.4711))
    rendered = render_zones([zone])
    assert '211.59–215.47' in rendered
    assert zone.bounds == (211.5891, 215.4711)
