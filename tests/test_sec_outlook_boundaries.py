from types import SimpleNamespace

import pytest

from finbotagents.dataflows.errors import VendorUnavailableError
from finbotagents.dataflows.vendors import sec_edgar, sec_outlook


def test_filings_include_only_supported_forms_dates_items_and_accessions():
    payload = {
        "form": ["8-K", "8-K/A", "10-K", "8-K", "8-K", "8-K"],
        "filingDate": ["2026-01-01", "2026-01-02", "2026-01-01", "2026-02-01", "2026-01-01", "2026-01-01"],
        "accessionNumber": ["0000001234-26-000001"] * 4 + ["bad", "0000001234-26-000002"],
        "items": ["2.02", "7.01", "2.02", "2.02", "2.02", "1.01"],
    }
    rows = list(sec_outlook._filings(payload, "2026-01-31"))
    assert len(rows) == 2
    assert rows[0][2] == {"2.02"} and rows[1][2] == {"7.01"}


def test_exhibit_urls_remain_in_expected_sec_archive(monkeypatch):
    html = """<table>
    <tr><td>1</td><td>release</td><td><a href="valid.htm">release</a></td><td>EX-99.1</td></tr>
    <tr><td>2</td><td>external</td><td><a href="https://attacker.invalid/evil.htm">x</a></td><td>EX-99.1</td></tr>
    <tr><td>3</td><td>outside</td><td><a href="../other.htm">x</a></td><td>EX-99.1</td></tr>
    <tr><td>4</td><td>script</td><td><a href="report.js">x</a></td><td>EX-99.1</td></tr>
    <tr><td>5</td><td>wrong</td><td><a href="report.htm">x</a></td><td>EX-10</td></tr>
    <tr><td>6</td><td>no link</td><td>missing</td><td>EX-99</td></tr>
    <tr><td>7</td></tr></table>"""
    monkeypatch.setattr(sec_edgar, "_request", lambda url: SimpleNamespace(text=html))
    urls = list(sec_outlook._exhibits("0000001234", "0000001234-26-000001"))
    assert urls == ["https://www.sec.gov/Archives/edgar/data/1234/000000123426000001/valid.htm"]


@pytest.mark.parametrize(("text", "metric", "expected"), [
    ("Revenue is expected to be $10 billion plus or minus 10%", "revenue", {"low": 9e9, "high": 11e9}),
    ("Sales between $12 million and $15 million", "revenue", {"low": 12e6, "high": 15e6}),
    ("Diluted EPS expected to be $2.50 to $3.00", "eps", {"low": 2.5, "high": 3.0}),
    ("Revenue expected to be $10", "revenue", None),
    ("EPS expected to be $10 million", "eps", None),
    ("Revenue between $15 million and $12 million", "revenue", None),
    ("Revenue grew by $100 billion last quarter", "revenue", None),
])
def test_guidance_requires_unambiguous_forward_looking_values(text, metric, expected):
    assert sec_outlook._bounds(text, metric) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("First quarter fiscal 2027", (2027, 1)), ("Q4 FY2026", (2026, 4)),
    ("fiscal year 2028", (2028, None)), ("second quarter", None),
])
def test_fiscal_period_requires_a_year(text, expected):
    assert sec_outlook._period(text) == expected


@pytest.mark.parametrize("statement", ["We withdraw our guidance.", "We withdrew our guidance."])
def test_guidance_withdrawal_does_not_reuse_older_estimates(monkeypatch, statement):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda _: "0000001234")
    monkeypatch.setattr(sec_outlook, "_releases", lambda *args: [("2026-01-01", "accession", {"2.02"})])
    monkeypatch.setattr(sec_outlook, "_exhibits", lambda *args: ["https://example.invalid/release"])
    monkeypatch.setattr(sec_edgar, "_request", lambda _: SimpleNamespace(text=f"<p>{statement}</p>"))
    result = sec_outlook.get_company_guidance("ABC", "2026-01-02")
    assert result["guidance"] == []
    assert "withdrew" in result["guidance_unavailable"]


def test_missing_sec_filer_is_reported(monkeypatch):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda _: None)
    assert "No SEC filer" in sec_outlook.get_company_guidance("ABC", "2026-01-02")["unavailable"]


@pytest.mark.parametrize("currency", ["USD", "CAD", None])
def test_inferred_reporting_currency_is_used_only_if_verified(monkeypatch, currency):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda _: "0000001234")
    monkeypatch.setattr(sec_outlook, "_releases", lambda *args: [("2026-01-01", "a", {"2.02"})])
    monkeypatch.setattr(sec_outlook, "_exhibits", lambda *args: ["https://example.invalid/release"])
    html = "<h2>Outlook</h2><p>FY 2027</p><p>Revenue expected to be $10 million</p>"
    monkeypatch.setattr(sec_edgar, "_request", lambda _: SimpleNamespace(text=html))

    def reporting_currency(*args):
        if currency is None:
            raise VendorUnavailableError("unavailable")
        return currency

    monkeypatch.setattr(sec_outlook, "_reporting_currency", reporting_currency)
    result = sec_outlook.get_company_guidance("ABC", "2026-01-02")
    assert result["guidance"][0]["currency"] == currency
    assert "uses_dollar_sign" not in result["guidance"][0]


@pytest.mark.parametrize(("context", "period", "document", "expected"), [
    ("Quarter ends on January 31, 2027", (2027, 1), "", ["2027-01-31"]),
    ("FY 2027", (2027, None), "first quarter ended January 31, 2027", []),
    ("FY 2027 Q2", (2027, 2), "no prior period", []),
    ("FY 2027 Q2", (2027, 2), "Q1 FY 2027 first quarter ended January 31, 2027", ["2027-04-30"]),
    ("FY 2027 Q2", (2027, 2), "Q1 FY 2027 first quarter ended January 30, 2027", ["2027-05-01", "2027-05-08"]),
    ("FY 2027 Q2", (2027, 2), "Q3 FY 2027 third quarter ended January 31, 2027", []),
])
def test_period_end_requires_consistent_previous_quarter(context, period, document, expected):
    assert sec_outlook._period_ends(context, period, document) == expected


@pytest.mark.parametrize(("line", "basis"), [
    ("GAAP EPS expected to be $2.00", "gaap"),
    ("Adjusted EPS expected to be $2.00", "normalized"),
    ("EPS expected to be $2.00", None),
])
def test_eps_guidance_marks_reported_basis(line, basis):
    blocks = ["Outlook", "FY 2027", line]
    records = sec_outlook._guidance(blocks, "https://example.invalid", "2026-01-01")
    assert len(records) == 1
    assert records[0]["basis"] == basis


def test_guidance_does_not_read_past_forward_looking_disclaimer():
    blocks = ["Outlook", "FY 2027", "Forward-looking statements", "Revenue expected to be $10 billion"]
    assert sec_outlook._guidance(blocks, "https://example.invalid", "2026-01-01") == []


def test_non_guidance_and_long_headings_are_ignored():
    blocks = ["Other information", "FY 2027", "Revenue expected to be $10 billion",
              "Outlook " + "x" * 300]
    assert sec_outlook._guidance(blocks, "https://example.invalid", "2026-01-01") == []


@pytest.mark.parametrize(("text", "as_of", "expected"), [
    ("Company will report earnings on November 18, 2026", "2026-11-01", "2026-11-18"),
    ("Company will report earnings on November 18, 2026", "2026-11-19", None),
    ("Company reported earnings on November 18, 2026", "2026-11-01", None),
])
def test_confirmed_earnings_date_must_be_future_and_explicit(text, as_of, expected):
    assert sec_outlook._confirmed_date([text], as_of) == expected
