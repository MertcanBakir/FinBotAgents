import pytest

from finbotagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from finbotagents.dataflows.vendors.alpha_vantage import indicator as av


@pytest.mark.parametrize(("name", "function", "column"), [
    ("close_50_sma", "SMA", "SMA"), ("close_200_sma", "SMA", "SMA"),
    ("close_10_ema", "EMA", "EMA"), ("macd", "MACD", "MACD"),
    ("macds", "MACD", "MACD_Signal"), ("macdh", "MACD", "MACD_Hist"),
    ("rsi", "RSI", "RSI"), ("boll", "BBANDS", "Real Middle Band"),
    ("boll_ub", "BBANDS", "Real Upper Band"), ("boll_lb", "BBANDS", "Real Lower Band"),
    ("atr", "ATR", "ATR"),
])
def test_indicator_selects_endpoint_and_filters_dates(monkeypatch, name, function, column):
    calls = []

    def request(endpoint, params):
        calls.append((endpoint, params))
        return f"time,{column}\n2026-01-07,99\n2026-01-05,42\n2025-12-01,4\ninvalid,1\n"

    monkeypatch.setattr(av, "_make_api_request", request)
    result = av.get_indicator("ABC", name, "2026-01-06", 7)
    assert calls[0][0] == function
    assert calls[0][1]["symbol"] == "ABC"
    assert "2026-01-05: 42" in result
    assert "2026-01-07: 99" not in result
    assert "2025-12-01: 4" not in result


@pytest.mark.parametrize(("response", "message"), [
    ("time,RSI", "No data returned"),
    ("date,RSI\n2026-01-05,42", "'time' column not found"),
    ("time,SMA\n2026-01-05,42", "Column 'RSI' not found"),
    ("time,RSI\n2025-01-01,42", "No data available for the specified date range"),
])
def test_indicator_reports_missing_or_invalid_data(monkeypatch, response, message):
    monkeypatch.setattr(av, "_make_api_request", lambda *args: response)
    assert message in av.get_indicator("ABC", "rsi", "2026-01-06", 7)


def test_indicator_propagates_unavailable_vendor_for_fallback(monkeypatch):
    def unavailable(*args):
        raise VendorUnavailableError("unavailable")

    monkeypatch.setattr(av, "_make_api_request", unavailable)
    with pytest.raises(VendorUnavailableError):
        av.get_indicator("ABC", "rsi", "2026-01-06", 7)


def test_unsupported_indicator_reports_no_market_data():
    with pytest.raises(NoMarketDataError):
        av.get_indicator("ABC", "vwma", "2026-01-06", 7)
