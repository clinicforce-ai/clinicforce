"""Read a dated Availity Essentials eligibility printout for visit planning.

The portal's Patient Cost Estimator error is not an eligibility response. This
parser accepts only the explicit specialist office rows and a matching patient,
date and provider. It never turns the PDF into a collection authorization.
"""
from __future__ import annotations

import re
from datetime import datetime

import fitz

from app.insurance import identity


def _flat(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def _date(value: str) -> str:
    return datetime.strptime(value.strip(), '%b %d, %Y').date().isoformat()


def _cents(value: str) -> int:
    return round(float(value.replace(',', '')) * 100)


def parse_visit_pdf(content: bytes, *, first: str, last: str, birth: str,
                    service_date: str, rendering_npi: str) -> dict:
    """Return non-identifying summary or raise ValueError for unsupported evidence."""
    if len(content) > 8_000_000 or not content.startswith(b'%PDF-'):
        raise ValueError('Upload an Availity Essentials PDF under 8 MB.')
    try:
        with fitz.open(stream=content, filetype='pdf') as document:
            if not 2 <= len(document) <= 20:
                raise ValueError('The Availity eligibility printout is incomplete.')
            pages = [_flat(page.get_text(sort=True)) for page in document]
    except (fitz.FileDataError, fitz.EmptyFileError) as exc:
        raise ValueError('The PDF could not be read.') from exc
    header = pages[0]
    if 'Availity Essentials' not in header:
        raise ValueError('This is not an Availity Essentials eligibility printout.')
    name = re.search(r'Feedback\s+([A-Z][A-Z\-\s]+),\s*([A-Z][A-Z\-]+)', header)
    dob = re.search(r'Active Coverage\s+([A-Z][a-z]{2} \d{1,2}, \d{4})', header)
    visit = re.search(r'Date of Service\s+([A-Z][a-z]{2} \d{1,2}, \d{4})', header)
    npi = re.search(r'Requesting Provider\s+Name:.*?\s+NPI:\s*(\d{10})', header)
    if not all((name, dob, visit, npi)):
        raise ValueError('Patient, visit date, or requesting provider could not be read.')
    if (identity.normalized(name.group(1)) != identity.normalized(last)
            or identity.normalized(name.group(2)) != identity.normalized(first)
            or _date(dob.group(1)) != identity.birth_date(birth)
            or _date(visit.group(1)) != service_date
            or npi.group(1) != str(rendering_npi)):
        raise ValueError('The patient, date, or requesting provider differs from this visit.')
    if not ('Plan / Product: Aetna Medicare Signature (PPO)' in header
            and 'Group Number: 000003-TX' in header
            and 'Plan Number: 000003-TX000047' in header):
        raise ValueError('The Aetna Signature plan identifiers do not match this planning rule.')
    body = ' '.join(pages[1:])
    if not re.search(r'provider ID you entered is participating in this patient.s network', body, re.I):
        raise ValueError('This response did not confirm the entered provider participates.')
    oop = re.search(r'\$([\d,]+) / Calendar Year\(s\)\s+\$([\d,]+\.\d{2}) Remaining\s+In Network', body)
    if not oop:
        raise ValueError('An in-network individual out-of-pocket balance was not found.')
    blocks = re.split(r'(?=(?:In|Out of) Network Place of Service:)', body)
    office = [block for block in blocks if block.startswith('In Network Place of Service: Office Coverage Level: Individual')]
    specialist = [block for block in office if 'Specialist Visit or Evaluation' in block]
    copays = [re.search(r'\$([\d,]+)(?:\.\d{2})?', block) for block in specialist
              if 'Copay Included In CATAS OOP' in block]
    coinsurance = [re.search(r'\b(\d+)%', block) for block in specialist
                   if 'COINS APPLIES TO CATAS OUT OF POCKET' in block]
    copay_values = {_cents(match.group(1)) for match in copays if match}
    coinsurance_values = {int(match.group(1)) for match in coinsurance if match}
    if len(copay_values) != 1 or len(coinsurance_values) != 1:
        raise ValueError('The specialist office copay or coinsurance is missing or conflicting.')
    oop_annual, oop_remaining = _cents(oop.group(1)), _cents(oop.group(2))
    if oop_remaining > oop_annual:
        raise ValueError('The reported out-of-pocket amounts conflict.')
    return {
        'source': 'Availity Essentials eligibility PDF',
        'service_date': service_date,
        'active': True,
        'network_status': 'in_network',
        'plan': 'Aetna Medicare Signature (PPO)',
        'copay_cents': copay_values.pop(),
        'coinsurance_percent': coinsurance_values.pop(),
        'oop_annual_cents': oop_annual,
        'oop_remaining_cents': oop_remaining,
    }
