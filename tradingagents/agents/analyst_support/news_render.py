"""Render numeric news evidence without asking a model to reproduce tables."""

import html
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


def _cell(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')


def _number(value, tr, decimals=0):
    if value is None:
        return '—'
    try:
        result = f'{Decimal(str(value)):,.{decimals}f}'
    except InvalidOperation:
        return '—'
    return result.translate(str.maketrans(',.', '.,')) if tr else result


def insider_table(evidence, trade_date, tr=False):
    title = 'Son Bir Yıldaki İçeriden Kişi Satışları' if tr else 'Insider Sales in the Last Year'
    lines = [f'### {title}']
    try:
        data = json.loads(evidence.split('\n', 1)[1])
        groups = data['by_person_role_currency']
    except (ValueError, IndexError, KeyError, TypeError):
        if 'No sales found' in evidence or evidence.startswith(('NO_DATA_AVAILABLE:', 'No insider')):
            lines.append('Sağlayıcının mevcut kayıtlarında satış bulunmuyor.' if tr
                         else 'No sales found in available provider records.')
        else:
            lines.append('Satış verisi alınamadı veya doğrulanamadı.' if tr
                         else 'Sales data unavailable or could not be verified.')
        return '\n\n'.join(lines)

    headings = (['Kişi', 'Görev', 'İşlem sayısı', 'Toplam pay', 'Hesaplanan Ortalama Fiyat']
                if tr else ['Person', 'Role', 'Transactions', 'Total shares', 'Calculated Average Price'])
    lines += ['| ' + ' | '.join(headings) + ' |', '|---|---|---:|---:|---:|']
    partial = False
    for group in groups:
        currency = group['currency']
        average = _number(group.get('calculated_average_price'), tr, 2)
        incomplete = (Decimal(str(group.get('priced_shares', 0))) != Decimal(str(group['known_shares']))
                      or group.get('missing_shares', 0) or group.get('missing_value', 0))
        if incomplete:
            average += '*'
            partial = True
        lines.append('| ' + ' | '.join([
            _cell(group['person']), _cell(group['role']), str(group['transactions']),
            _number(group['known_shares'], tr), f'{average} ({_cell(currency)})',
        ]) + ' |')
    recent = len(data.get('last_30_days', []))
    start = (date.fromisoformat(trade_date) - timedelta(days=30)).isoformat()
    lines.append(f'Son 30 gün ({start}–{trade_date}): {recent} satış kaydı.' if tr
                  else f'Last 30 days ({start}–{trade_date}): {recent} sales records.')
    if latest := data.get('latest_transaction'):
        lines.append(f"En son kayıt: {_cell(latest['person'])}, {latest['date']}, "
                      f"{_number(latest['shares'], tr)} pay." if tr else
                      f"Latest record: {_cell(latest['person'])}, {latest['date']}, "
                      f"{_number(latest['shares'], tr)} shares.")
    defaulted = any(group.get('currency_defaulted') for group in groups)
    note = ('Not: Ortalamalar pay ağırlıklıdır; kayıt kapsamı eksik olabilir'
            if tr else 'Note: Averages are share-weighted; record coverage may be incomplete')
    if defaulted:
        note += (' ve para birimi belirtilmeyen işlemler USD kabul edilir' if tr
                 else '; transactions without a currency default to USD')
    note += '.'
    if partial:
        note = note.rstrip('.') + (' (* yalnızca fiyatı hesaplanabilen kayıtlar).' if tr
                                  else ' (* only records with computable prices).')
    lines.append(note)
    return '\n\n'.join([lines[0], '\n'.join(lines[1:3 + len(groups)]), *lines[3 + len(groups):]])


def social_sample_line(evidence, tr=False):
    counts = re.search(r'Bullish: (\d+) .*?Bearish: (\d+)', evidence)
    total = re.search(r'Total: (\d+)', evidence)
    span = re.search(r'Sample timestamps: (\S+) to (\S+); span: ([\d.]+) seconds', evidence)
    if not (counts and total and span):
        return ('Örneklem: Kullanılabilir etiketli sosyal veri yok.' if tr
                else 'Sample: No usable labeled social data.')
    up, down, n = int(counts[1]), int(counts[2]), int(total[1])
    upper = 100 * up / n if n else 0
    lower = 100 * down / n if n else 0
    if tr:
        return (f'Örneklem: {n} etiketli mesaj; {up} yükseliş (%{upper:.1f}), '
                f'{down} düşüş (%{lower:.1f}); {span[1]}–{span[2]} '
                f'({_number(span[3], tr)} saniye) — yalnızca etiketli örneklemdir, aylık eğilim değildir.')
    return (f'Sample: {n} labeled posts; {up} bullish ({upper:.1f}%), {down} bearish ({lower:.1f}%); '
            f'{span[1]}–{span[2]} ({span[3]} seconds) — labeled sample only, not a monthly trend.')


def compose_news_report(text, table, sample, tr=False):
    """Insert deterministic facts into fixed section markers; no second model call."""
    headings = {
        'NEWS': 'Enstrüman ve Sektör Haberleri' if tr else 'Instrument and Sector News',
        'REACTION': 'Yatırımcı Tepkisi' if tr else 'Investor Reaction',
        'CATALYSTS': 'İzlenecek Gelişmeler' if tr else 'Developments to Watch',
    }
    if '### REACTION' not in text:
        # Preserve usable text from a provider that ignored formatting instructions.
        return text + '\n\n' + table + f"\n\n### {headings['REACTION']}\n\n" + sample
    text = text.replace('### REACTION', table + f"\n\n### {headings['REACTION']}\n\n" + sample, 1)
    for marker in ('NEWS', 'CATALYSTS'):
        text = text.replace(f'### {marker}', f'### {headings[marker]}')
    return text
