"""Texas PFS locality from the official October 2026 CMS service ZIP file."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZipFile

SOURCE_URL = 'https://www.cms.gov/files/zip/zip-code-carrier-locality-file-updated-08-13-2026.zip'
SOURCE_SHA256 = '64cd98f5fd6087b9f1732a11749e48883ad26f6416cc1e26cd19af135c5b17b2'
DEFAULT_CACHE = Path(__file__).with_name('data') / 'cms_zip_locality_2026_oct_tx.json.gz'


def import_official_zip(source: Path, destination: Path = DEFAULT_CACHE) -> dict:
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('CMS ZIP-locality source is not the verified October 2026 release')
    with ZipFile(source) as archive:
        required = {'ZIP5_OCT2026.txt', 'ZIP9_OCT2026.txt', 'ZIP5lyout.txt', 'ZIP9lyout.txt'}
        if not required.issubset(archive.namelist()):
            raise ValueError('CMS ZIP-locality archive is missing required members')
        layout = archive.read('ZIP5lyout.txt').decode(errors='replace')
        if not all(label in layout for label in ('Pricing Locality', 'Plus Four Flag', 'Year/Quarter')):
            raise ValueError('CMS ZIP-locality record layout changed')
        zip5 = {}
        for raw in archive.open('ZIP5_OCT2026.txt'):
            row = raw.decode('ascii').rstrip('\r\n')
            if not row.startswith('TX'):
                continue
            if (len(row) < 80 or row[7:12] != '04412' or row[75:80] != '20264'
                    or row[20] not in {'0', '1'} or not row[2:7].isdigit()):
                raise ValueError('Unexpected Texas CMS ZIP5 locality record')
            zip5[row[2:7]] = {'locality': row[12:14], 'plus_four_required': row[20] == '1'}
        if not zip5:
            raise ValueError('CMS ZIP-locality archive has no Texas records')
        zip9 = {}
        ambiguous = {code for code, value in zip5.items() if value['plus_four_required']}
        for raw in archive.open('ZIP9_OCT2026.txt'):
            row = raw.decode('ascii').rstrip('\r\n')
            if not row.startswith('TX') or row[2:7] not in ambiguous:
                continue
            if (len(row) < 80 or row[7:12] != '04412' or row[75:80] != '20264'
                    or not row[21:25].isdigit()):
                raise ValueError('Unexpected Texas CMS ZIP9 locality record')
            zip9[row[2:7] + row[21:25]] = row[12:14]
        if not zip9:
            raise ValueError('CMS ZIP-locality archive has no Texas ZIP+4 extensions')
    cache = {'version': '2026Q4', 'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
             'zip5': zip5, 'zip9': zip9}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destination, 'wt', encoding='utf-8') as output:
        json.dump(cache, output, sort_keys=True, separators=(',', ':'))
    return cache


def resolve(postal_code: str, *, cache_path: Path = DEFAULT_CACHE) -> dict | None:
    digits = re.sub(r'\D', '', str(postal_code or ''))
    if len(digits) not in {5, 9} or not cache_path.is_file():
        return None
    with gzip.open(cache_path, 'rt', encoding='utf-8') as stream:
        cache = json.load(stream)
    if cache.get('version') != '2026Q4' or cache.get('source_sha256') != SOURCE_SHA256:
        return None
    row = cache.get('zip5', {}).get(digits[:5])
    if not row:
        return None
    locality = (cache.get('zip9', {}).get(digits) if row['plus_four_required'] else row['locality'])
    return {'locality': locality, 'source_url': SOURCE_URL, 'source_sha256': SOURCE_SHA256,
            'status': 'resolved' if locality else 'zip_plus_four_required'}


def pricing_locality(postal_code: str | None, configured: str | None) -> tuple[str | None, dict]:
    """Prefer the CMS service ZIP; never silently override a conflicting clinic map."""
    if postal_code:
        evidence = resolve(postal_code)
        if evidence is None:
            return None, {'status': 'unresolved_service_zip'}
        if not evidence['locality']:
            return None, evidence
        if configured and configured != evidence['locality']:
            return None, {**evidence, 'status': 'clinic_location_conflict',
                          'configured_locality': configured}
        return evidence['locality'], evidence
    if configured:
        return configured, {'status': 'clinic_location_configuration', 'locality': configured}
    return None, {'status': 'service_location_missing'}
