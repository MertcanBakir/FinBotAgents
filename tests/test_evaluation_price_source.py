from datetime import date

import pandas as pd
import pytest
import yfinance

from finbotagents.dataflows.errors import VendorUnavailableError
from finbotagents.dataflows.evaluation_prices import AdjustedCloseSource


def test_adjusted_closes_include_the_final_date(monkeypatch):
    calls = []
    frame = pd.DataFrame({"Close": [10.0, 12.5]}, index=pd.to_datetime(["2026-01-05", "2026-01-06"]))

    def download(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return frame

    monkeypatch.setattr(yfinance, "download", download)
    result = AdjustedCloseSource().closes("ABC", date(2026, 1, 5), date(2026, 1, 6))
    assert result == {date(2026, 1, 5): 10.0, date(2026, 1, 6): 12.5}
    assert calls[0][1]["end"] == "2026-01-07"
    assert calls[0][1]["auto_adjust"] is True


def test_adjusted_closes_handles_multiindex_and_missing_data(monkeypatch):
    frame = pd.DataFrame({("Close", "ABC"): [12.0]}, index=pd.to_datetime(["2026-01-05"]))
    monkeypatch.setattr(yfinance, "download", lambda *args, **kwargs: frame)
    assert AdjustedCloseSource().closes("ABC", date(2026, 1, 5), date(2026, 1, 5)) == {
        date(2026, 1, 5): 12.0
    }
    monkeypatch.setattr(yfinance, "download", lambda *args, **kwargs: pd.DataFrame())
    assert AdjustedCloseSource().closes("ABC", date(2026, 1, 5), date(2026, 1, 5)) == {}


def test_adjusted_close_source_hides_vendor_exception(monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("sensitive vendor response")

    monkeypatch.setattr(yfinance, "download", fail)
    with pytest.raises(VendorUnavailableError, match="Evaluation price source unavailable"):
        AdjustedCloseSource().closes("ABC", date(2026, 1, 5), date(2026, 1, 5))
