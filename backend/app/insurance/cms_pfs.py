"""Dated CMS physician-fee estimates for supported office and nerve services.

The CMS amount is an Original Medicare benchmark, not a payer-confirmed MA quote.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zipfile import ZipFile

from app.insurance import cpt_rules


SOURCE_URL = 'https://www.cms.gov/files/zip/rvu26d-updated-08-26-2026.zip'
SOURCE_SHA256 = '521ff0f4ecbf13b5d99dc1dc04d9f82e5361e55c6b4b6b3033043598bf8406e6'
DEFAULT_CACHE = Path(__file__).with_name('data') / 'cms_pfs_2026_oct_tx.json.gz'
SUPPORTED_CODES = {str(code) for code in (*range(99202, 99206), *range(99212, 99216))} | cpt_rules.NCS_CODES | cpt_rules.EMG_ADD_ONS
EFFECTIVE_FROM = '2026-10-01'
EFFECTIVE_TO = '2026-12-31'
_RVU_SCHEMA = {0: ('', 'HCPCS'), 1: ('', 'MOD'), 3: ('STATUS', 'CODE'),
               5: ('WORK', 'RVU'), 6: ('NON-FAC', 'PE RVU'),
               7: ('NA', 'INDICATOR'), 8: ('FACILITY', 'PE RVU'),
               9: ('NA', 'INDICATOR'), 10: ('MP', 'RVU'),
               13: ('PCTC', 'IND'), 18: ('MULT', 'PROC'),
               19: ('BILAT', 'SURG'), 25: ('CONV', 'FACTOR'),
               27: ('CALCULATION', 'FLAG')}


def _rows(archive: ZipFile, name: str) -> list[list[str]]:
    return list(csv.reader(io.TextIOWrapper(archive.open(name), encoding='utf-8-sig')))


def import_official_zip(source: Path, destination: Path) -> dict:
    """Cache only supported numeric fields; omit AMA-owned descriptions."""
    names = {'non_qp': 'PPRRVU2026_Oct_nonQPP.csv', 'qp': 'PPRRVU2026_Oct_QPP.csv'}
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError('CMS RVU26D ZIP does not match the verified October 2026 release SHA-256')
    with ZipFile(source) as archive:
        required = {*names.values(), 'GPCI2026.csv', '26LOCCO.csv', 'RVU26D.pdf'}
        if not required.issubset(archive.namelist()):
            raise ValueError('CMS RVU26D ZIP is missing required release members')
        gpci_rows = _rows(archive, 'GPCI2026.csv')
        if gpci_rows[2][1:7] != ['State', 'Locality Number', 'Locality Name',
                                '2026 PW GPCI (with 1.0 Floor)***', '2026 PE GPCI', '2026 MP GPCI']:
            raise ValueError('CMS GPCI column schema changed')
        gpci = {row[2]: row[4:7] for row in gpci_rows
                if len(row) >= 7 and row[1] == 'TX' and row[2].isdigit()}
        if not gpci:
            raise ValueError('CMS archive has no Texas 2026 localities')
        rates = {}
        for status, name in names.items():
            rows = _rows(archive, name)
            if (len(rows) < 11 or 'October Release' not in rows[0][2]
                    or rows[4][2] != 'RELEASED 08/26/2026'
                    or any((rows[8][index], rows[9][index]) != pair
                           for index, pair in _RVU_SCHEMA.items())):
                raise ValueError('CMS archive is not the October 2026 PFS release')
            rates[status] = {}
            for row in rows[10:]:
                if len(row) < 29 or row[0] not in SUPPORTED_CODES or row[3] != 'A':
                    continue
                modifier = row[1].strip().upper()
                if modifier not in {'', '26', 'TC'}:
                    continue
                rates[status][row[0] + ':' + modifier] = {
                    'work': row[5], 'nonfacility_pe': row[6], 'facility_pe': row[8],
                    'mp': row[10], 'nonfacility_na': row[7], 'facility_na': row[9],
                    'pctc': row[13], 'multiple': row[18], 'bilateral': row[19],
                    'diagnostic_imaging': row[27], 'conversion_factor': row[25],
                }
        if not all(rates.values()):
            raise ValueError('CMS archive has no supported payable office or nerve rows')
    cache = {'version': 'RVU26D', 'effective_from': EFFECTIVE_FROM, 'effective_to': EFFECTIVE_TO,
             'source_url': SOURCE_URL, 'source_sha256': digest,
             'gpci_tx': gpci, 'rates': rates}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destination, 'wt', encoding='utf-8') as stream:
        json.dump(cache, stream, sort_keys=True, separators=(',', ':'))
    return cache


def estimate_rate(code: str, modifiers: list[str], units: int, *, service_date: str,
                  locality: str, qp_status: str, place_of_service: str,
                  provider_npi: str, patient_binding: str,
                  cache_path: Path = DEFAULT_CACHE) -> dict | None:
    """Price a simple professional line only when every CMS payment scope is known."""
    if (code not in SUPPORTED_CODES or qp_status not in {'qp', 'non_qp'}
            or place_of_service not in {'11', '19', '21', '22'}
            or type(units) is not int or units < 1
            or (units != 1 and code not in cpt_rules.EMG_ADD_ONS)
            or units > 4 or not provider_npi or not patient_binding):
        return None
    modifier = ','.join(value.upper() for value in modifiers)
    if modifier not in {'', '26', 'TC'} or not cache_path.is_file():
        return None
    with gzip.open(cache_path, 'rt', encoding='utf-8') as stream:
        cache = json.load(stream)
    if (cache.get('version') != 'RVU26D' or not cache['effective_from'] <= service_date <= cache['effective_to']
            or locality not in cache.get('gpci_tx', {})):
        return None
    row = cache.get('rates', {}).get(qp_status, {}).get(code + ':' + modifier)
    if not row or row['multiple'] not in {'0', '9'} or row['bilateral'] not in {'0', '3'} or row['diagnostic_imaging'] != '0':
        return None
    facility = place_of_service != '11'
    if row['facility_na' if facility else 'nonfacility_na'].strip():
        return None
    try:
        work_gp, pe_gp, mp_gp = (Decimal(value) for value in cache['gpci_tx'][locality])
        work = Decimal(row['work']) * work_gp
        pe = Decimal(row['facility_pe' if facility else 'nonfacility_pe']) * pe_gp
        mp = Decimal(row['mp']) * mp_gp
        cents = int(((work + pe + mp) * Decimal(row['conversion_factor']) * 100)
                    .quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (ArithmeticError, ValueError, TypeError):
        return None
    if cents <= 0:
        return None
    return {'id': f"cms:{cache['version']}:{qp_status}:TX{locality}:{code}:{modifier}:{place_of_service}",
            'code': code, 'modifier': modifier, 'allowed_cents': cents,
            'basis': 'cms_original_medicare_estimate',
            'source_reference': f"CMS {cache['version']} Texas locality {locality} {qp_status} PFS",
            'source_sha256': cache['source_sha256'], 'source_url': cache['source_url'],
            'effective_from': cache['effective_from'], 'effective_to': cache['effective_to'],
            'service_date': service_date, 'provider_npi': provider_npi,
            'place_of_service': place_of_service, 'patient_binding': patient_binding,
            'cms_locality': locality, 'cms_qp_status': qp_status,
            'cms_setting': 'facility' if facility else 'nonfacility'}
