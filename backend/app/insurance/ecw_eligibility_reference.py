"""Read out-of-pocket figures from an eCW eligibility printout as reference evidence."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import fitz

from app.insurance import identity


def _words(page: fitz.Page) -> list[tuple]:
    text = page.get_text()
    textpage = None if text.strip() else page.get_textpage_ocr(dpi=200)
    return page.get_text('words', textpage=textpage)


def parse_ecw_eligibility_pdf(content: bytes, *, first: str, last: str,
                              service_date: str) -> dict:
    """Return only dated, patient-matched reference limits; never calculate liability."""
    if len(content) > 8_000_000 or not content.startswith(b'%PDF-'):
        raise ValueError('Upload an eCW eligibility PDF under 8 MB.')
    try:
        with fitz.open(stream=content, filetype='pdf') as document:
            if not 4 <= len(document) <= 10 or any(
                    page.rect.width > 1000 or page.rect.height > 1200 for page in document):
                raise ValueError('The eCW eligibility printout is incomplete or unsupported.')
            first_page = document[0]
            header = first_page.get_text()
            if not header.strip():
                header = first_page.get_text(textpage=first_page.get_textpage_ocr(dpi=200))
            match = re.search(r'Eligibility\s+Response\s+for\s+([A-Z-]+)\s*,\s*([A-Z-]+)\s+'
                              r'(\d{1,2}/\d{1,2}/\d{4})', header, re.I)
            if not match:
                raise ValueError('The eCW eligibility report header could not be read.')
            if (identity.normalized(match.group(1)) != identity.normalized(last)
                    or identity.normalized(match.group(2)) != identity.normalized(first)):
                raise ValueError('The eligibility report patient differs from this visit.')
            report_date = datetime.strptime(match.group(3), '%m/%d/%Y').date()
            visit_date = date.fromisoformat(service_date)
            if not report_date <= visit_date <= report_date + timedelta(days=7):
                raise ValueError('The eligibility report is not current for this visit.')

            found: dict[tuple[str, str], set[int]] = {}
            # shortcut: This reads the eCW office table layout; add another parser for a different printout layout.
            for page in list(document)[1:4]:
                words = _words(page)
                offices = [(x, y) for x, y, _, _, word, *_ in words
                           if 110 <= x <= 195 and re.fullmatch(r'Office[,.:]?', word, re.I)]
                for x, y, _, _, word, *_ in words:
                    level = ('individual' if re.search(r'Individual', word, re.I) else
                             'family' if re.search(r'Family', word, re.I) else None)
                    if not level or not 70 <= x <= 125 or not any(
                            -12 <= y - office_y <= 36 for _, office_y in offices):
                        continue
                    right = ' '.join(part for x0, y0, _, _, part, *_ in words
                                     if 280 <= x0 <= 410 and abs(y0 - y) <= 12)
                    period = 'remaining' if re.search(r'Remaining', right, re.I) else 'annual'
                    for amount in re.finditer(r'(?<![\d.])(\d{1,5})(?:\.(\d{2}))?(?![\d.])', right):
                        cents = int(Decimal(f'{amount.group(1)}.{amount.group(2) or "00"}') * 100)
                        if period == 'annual' and cents == 0:
                            continue
                        found.setdefault((level, period), set()).add(cents)
            keys = [(level, period) for level in ('individual', 'family')
                    for period in ('annual', 'remaining')]
            if any(len(found.get(key, set())) != 1 for key in keys):
                raise ValueError('The office out-of-pocket figures are missing or conflicting.')
            values = {f'{level}_oop_{period}_cents': next(iter(found[(level, period)]))
                      for level, period in keys}
            if any(values[f'{level}_oop_remaining_cents'] > values[f'{level}_oop_annual_cents']
                   for level in ('individual', 'family')):
                raise ValueError('The reported out-of-pocket figures conflict.')
    except (fitz.FileDataError, fitz.EmptyFileError) as exc:
        raise ValueError('The PDF could not be read.') from exc
    return {'source': 'eCW eligibility PDF', 'report_date': report_date.isoformat(),
            'service_date': service_date, **values}
