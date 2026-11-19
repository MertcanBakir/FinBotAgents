"""Filter and aggregate reported insider sales without inferring missing units."""
import csv
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

from dateutil.relativedelta import relativedelta

_SALE = re.compile(r"^(?:sale|sell|sold)\b", re.IGNORECASE)
_TRANSACTION_FIELDS = ("transaction_type", "transaction_code", "transaction_description")


def prepare_insider_sales(data, trade_date):
    """Return explicitly identified sales within the analysis date's trailing year."""
    end = date.fromisoformat(trade_date)
    start = end - relativedelta(years=1)
    scope = f"Reported insider sales, {start} to {end} (inclusive)"

    def in_scope(row, date_field, type_fields):
        try:
            transaction_date = date.fromisoformat(row.get(date_field, ""))
        except (TypeError, ValueError):
            return False
        return start <= transaction_date <= end and any(
            _SALE.match(str(row.get(field) or "").strip()) for field in type_fields
        )

    if isinstance(data, str):
        if data.startswith(("DATA_UNAVAILABLE:", "NO_DATA_AVAILABLE:", "No insider transactions reported",
                            "Data could not be retrieved.", "No data returned.")) or (
            data.startswith("# Insider Transactions for ") and "transactions are withheld" in data
        ):
            return f"{scope}\n{data}"
        header, _, body = data.partition("\n")
        if header.startswith("# Insider Transactions data for "):
            reader = csv.DictReader(StringIO(body))
            fields = reader.fieldnames or []
            if "Start Date" in fields and {"Text", "Transaction"}.intersection(fields):
                sales = [row for row in reader if in_scope(row, "Start Date", ("Text", "Transaction"))]
                if not sales:
                    return f"{scope}\nNo sales found in the provider's available records."
                return f"{scope}\n{_summarize_sales(sales, trade_date, csv_format=True)}"
        try:
            data = json.loads(data)
        except ValueError:
            data = None

    if isinstance(data, dict) and isinstance(rows := data.get("data"), list):
        sales = [row for row in rows if isinstance(row, dict) and in_scope(
            row, "transaction_date", _TRANSACTION_FIELDS,
        )]
        if sales:
            return f"{scope}\n{_summarize_sales(sales, trade_date)}"
        if not rows or any(isinstance(row, dict) and any(
            row.get(field) for field in _TRANSACTION_FIELDS
        ) for row in rows):
            return f"{scope}\nNo sales found in the provider's available records."
    return f"{scope}\nSales could not be verified from the provider's available data."


def _summarize_sales(rows, trade_date, csv_format=False):
    """Aggregate reported amounts, defaulting missing currency to USD."""
    def number(value):
        try:
            result = Decimal(str(value).replace(',', ''))
            return result if result.is_finite() and result >= 0 else None
        except InvalidOperation:
            return None

    end = date.fromisoformat(trade_date)
    window_start = end - relativedelta(days=30)
    groups, recent = {}, []
    latest = None
    seen = set()
    for row in rows:
        # Only exact duplicate records are collapsed; similar trades may be distinct.
        fingerprint = json.dumps(row, sort_keys=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        day = row['Start Date' if csv_format else 'transaction_date']
        person = row.get('Insider' if csv_format else 'executive') or 'Unknown'
        role = row.get('Position' if csv_format else 'executive_title') or 'Unknown'
        raw_currency = str(row.get('Currency' if csv_format else 'currency') or '').strip()
        currency = raw_currency.upper() or 'USD'
        shares = number(row.get('Shares' if csv_format else 'shares'))
        value = number(row.get('Value' if csv_format else 'transaction_value'))
        # A unit price is not an explicitly reported total transaction value.
        group = groups.setdefault((person, role, currency), {
            'person': person, 'role': role, 'currency': currency, 'transactions': 0,
            'known_shares': Decimal(0), 'known_value': Decimal(0),
            'missing_shares': 0, 'missing_value': 0, 'currency_defaulted': False,
            'priced_shares': Decimal(0), 'priced_value': Decimal(0),
        })
        group['transactions'] += 1
        group['currency_defaulted'] |= not bool(raw_currency)
        if shares is not None and shares > 0 and value is not None:
            group['priced_shares'] += shares
            group['priced_value'] += value
        for field, amount in [('shares', shares), ('value', value)]:
            if amount is None:
                group[f'missing_{field}'] += 1
            else:
                group[f'known_{field}'] += amount
        record = {'person': person, 'date': day, 'shares': shares,
                  'reported_value': value, 'currency': currency}
        if latest is None or day > latest['date']:
            latest = record
        if window_start <= date.fromisoformat(day) <= end:
            recent.append(record)
    for group in groups.values():
        group['calculated_average_price'] = (
            (group['priced_value'] / group['priced_shares']).quantize(Decimal('0.01'))
            if group['priced_shares'] else None
        )
        group['average_price_note'] = (
            'Share-weighted value/shares for matched records only. '
            + ('Missing currency defaulted to USD.' if group['currency_defaulted']
               else f"Currency: {group['currency']}.")
        )
        del group['priced_value']
    return json.dumps({
        'unique_records': len(seen), 'exact_duplicates_removed': len(rows) - len(seen),
        'by_person_role_currency': list(groups.values()),
        'latest_transaction': latest,
        'last_30_days': recent,
        'limitations': 'Provider records may be incomplete. Missing currency defaults to USD; '
                        'known totals exclude missing fields. No cross-currency comparison. '
                        'No reported value is derived from a unit price.',
    }, default=str, ensure_ascii=False)
