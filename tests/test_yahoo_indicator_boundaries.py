from types import SimpleNamespace

import pandas as pd
import pytest

from finbotagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from finbotagents.dataflows.vendors.yahoo import market


def test_indicator_window_includes_missing_calendar_days(monkeypatch):
    monkeypatch.setattr(market, "_get_stock_stats_bulk", lambda *args: {"2026-01-05": "42"})
    result = market.get_stock_stats_indicators_window("ABC", "rsi", "2026-01-06", 1)
    assert "2026-01-06: N/A: Not a trading day" in result
    assert "2026-01-05: 42" in result


def test_indicator_window_rejects_unsupported_indicator():
    with pytest.raises(ValueError, match="not supported"):
        market.get_stock_stats_indicators_window("ABC", "imaginary", "2026-01-06", 1)


def test_indicator_window_falls_back_to_each_day_on_calculation_error(monkeypatch):
    def fail(*args):
        raise RuntimeError("bad data")

    calls = []
    monkeypatch.setattr(market, "_get_stock_stats_bulk", fail)
    monkeypatch.setattr(market, "get_stockstats_indicator", lambda symbol, indicator, day: calls.append(day) or "ok")
    result = market.get_stock_stats_indicators_window("ABC", "rsi", "2026-01-06", 1)
    assert calls == ["2026-01-06", "2026-01-05"]
    assert "2026-01-06: ok" in result and "2026-01-05: ok" in result


def test_indicator_window_preserves_vendor_error_for_router_fallback(monkeypatch):
    def fail(*args):
        raise VendorUnavailableError("offline")

    monkeypatch.setattr(market, "_get_stock_stats_bulk", fail)
    with pytest.raises(VendorUnavailableError):
        market.get_stock_stats_indicators_window("ABC", "rsi", "2026-01-06", 1)


@pytest.mark.parametrize("available", [True, False])
def test_get_closes_handles_missing_close_column(monkeypatch, available):
    frame = pd.DataFrame({"Close": [12.0]}) if available else pd.DataFrame({"Open": [10.0]})
    monkeypatch.setattr(market.yf, "Ticker", lambda _: SimpleNamespace(history=lambda **kwargs: frame))
    result = market.get_closes("ABC", "2026-01-01", "2026-01-03")
    assert result.tolist() == ([12.0] if available else [])


def test_individual_indicator_converts_unexpected_error(monkeypatch):
    def fail(*args):
        raise RuntimeError("bad data")

    monkeypatch.setattr(market, "get_stock_stats", fail)
    with pytest.raises(NoMarketDataError, match="could not be read"):
        market.get_stockstats_indicator("ABC", "rsi", "2026-01-06")


def test_individual_indicator_preserves_vendor_errors(monkeypatch):
    def fail(*args):
        raise VendorUnavailableError("offline")

    monkeypatch.setattr(market, "get_stock_stats", fail)
    with pytest.raises(VendorUnavailableError):
        market.get_stockstats_indicator("ABC", "rsi", "2026-01-06")


def test_stockstats_returns_value_for_trading_day_and_na_for_other_day(monkeypatch):
    frame = pd.DataFrame({"Date": pd.to_datetime(["2026-01-05"]), "close": [42.0]})
    monkeypatch.setattr(market, "load_ohlcv", lambda *args: frame.copy())
    assert market.get_stock_stats("ABC", "close", "2026-01-05") == 42.0
    assert "Not a trading day" in market.get_stock_stats("ABC", "close", "2026-01-06")
