"""Group agreement evidence for a patient-bound product and a scoped clinic provider.

A signed network attachment can support a group contract finding. It does not
confirm individual credentialing or authorize a reimbursement rate.
"""
from __future__ import annotations

import re
from datetime import datetime

from app.core import staff_store
from app.insurance import commercial_store, network_participation
from app.insurance import identity as insurance_identity


def _signed_agreement_effective_date(text: str) -> str | None:
    """Read one explicitly labelled agreement date from a verified PDF extraction."""
    values = set()
    for match in re.finditer(r'Effective Date of Agreement\s*:.{0,80}?'
                             r'(\d{1,2}/\d{1,2}/\d{4})\b', text, re.I | re.S):
        try:
            values.add(datetime.strptime(match[1], '%m/%d/%Y').date().isoformat())
        except ValueError:
            return None
    return next(iter(values)) if len(values) == 1 else None


def signed_network_attachment(extracted: dict, network_name: str) -> str | None:
    """Locate a signed agreement's network attachment, never a casual name mention."""
    words = r'\W+'.join(re.escape(part) for part in network_name.split())
    if not words:
        return None
    pages = extracted.get('pages') or []
    for number, page in enumerate(pages, 1):
        text = str(page.get('text') or '')
        if (re.search(r'ATTACHMENT\s+A\s+TEXAS\s+PROVIDER\s+NETWORKS', text, re.I)
                and re.search(r'Authorized\s+Signature\s*:\s*' + words, text, re.I)):
            return f'Page {number}, signed network attachment'
    text = '\n'.join(str(page.get('text') or '') for page in pages)
    if not re.search(r'\b(?:executed|signed|signature)\b', text, re.I):
        return None
    sections = re.findall(r'attachment\s+a\b(.*?)(?=attachment\s+[b-z]\b|$)',
                          text, re.I | re.S)
    if any(re.search(words, section[:12000], re.I) for section in sections):
        return 'Attachment A'
    return None


def resolve(practice_id: str, payer: dict | None, inputs: dict, source: dict,
            result: dict, benefits: dict) -> dict | None:
    """Expose a dated group/network contract, separately from clinician credentialing and rates."""
    if (not payer or benefits.get('network_conflict') or benefits.get('network_status') == 'out_of_network'
            or not source.get('payer_plan_bound')):
        return None
    service_date = str(inputs.get('service_date') or '')
    product = (((result.get('plan_details') or {}).get('stedi') or {}).get('identified_product')
               or next(iter(source.get('payer_plan_names') or []), ''))
    billing = source.get('billing_provider') or {}
    rendering_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    billing_npi, ecw_tin = str(billing.get('npi') or ''), str(billing.get('tin') or '').replace('-', '')
    location, pos = str(source.get('location_id') or ''), str(inputs.get('place_of_service') or '')
    if not service_date or not product:
        return None
    candidates = []
    for network in commercial_store.list_networks(practice_id):
        if (network.get('payer_connection_id') != payer.get('id') or network.get('status') != 'verified'
                or not network.get('effective_from') or network['effective_from'] > service_date
                or (network.get('effective_to') and network['effective_to'] < service_date)):
            continue
        relationship = network_participation.product_network_relationship(
            payer.get('display_name') or '', product, source.get('payer_network_names') or [],
            network.get('network_name') or '', service_date)
        if (not relationship and insurance_identity.generic_plan_label(product)
                and not source.get('payer_network_names')
                and insurance_identity.normalized(source.get('plan'))
                == insurance_identity.normalized(network.get('plan_matcher'))):
            relationship = {'source': 'clinic_plan_matcher',
                            'network_name': network.get('network_name'),
                            'warning': 'The payer did not identify this member product with the clinic network name.'}
        warnings = []
        if not relationship:
            reported = source.get('payer_network_names') or []
            if reported:
                warnings.append(f"Payer network {str(reported[0])[:80]} differs from signed {network['network_name']}.")
            else:
                warnings.append(f"Member product {str(product)[:80]} has no documented link to signed {network['network_name']}.")
        if (source.get('plan') and network.get('plan_matcher')
                and insurance_identity.normalized(source['plan']) !=
                    insurance_identity.normalized(network['plan_matcher'])):
            warnings.append('eCW plan code differs from the clinic agreement record.')
        for label, actual, expected in (
                ('billing NPI', billing_npi, network.get('billing_npi')),
                ('rendering NPI', rendering_npi, network.get('rendering_npi')),
                ('billing TIN', ecw_tin, str(network.get('tin') or '').replace('-', '')),
                ('visit location', location, network.get('location_id')),
                ('place of service', pos, network.get('place_of_service'))):
            if actual and expected and actual != str(expected):
                warnings.append(f'{label} differs from the agreement record.')
            elif not actual:
                warnings.append(f'{label} is missing from the visit.')
            elif not expected:
                warnings.append(f'{label} is missing from the agreement record.')
        if billing.get('tin_conflict'):
            warnings.append('eCW has conflicting billing TINs.')
        candidates.append((network, relationship or {'source': 'clinic_agreement_unmapped'}, warnings))
    if not candidates:
        return None
    fewest = min(len(row[2]) for row in candidates)
    closest = [row for row in candidates if len(row[2]) == fewest]
    if len(closest) != 1:
        return None
    network, relationship, warnings = closest[0]
    tin = str(network.get('tin') or '').replace('-', '')
    documents = []
    for metadata in staff_store.list_library(practice_id):
        if (metadata.get('kind') != 'contract' or metadata.get('content_type') != 'application/pdf'
                or not metadata.get('payer')
                or (metadata.get('effective_to') and metadata['effective_to'] < service_date)
                or insurance_identity.normalized(metadata['payer']) !=
                   insurance_identity.normalized(payer.get('display_name'))):
            continue
        document = staff_store.get_library(metadata['id'], practice_id)
        extracted = (document or {}).get('extraction') or {}
        if (not document or not document.get('sha256') or extracted.get('sha256') != document['sha256']):
            continue
        text = '\n'.join(str(page.get('text') or '') for page in extracted.get('pages') or [])
        signed_effective = _signed_agreement_effective_date(text)
        metadata_effective = str(metadata.get('effective_from') or '')
        if (metadata_effective and signed_effective and metadata_effective != signed_effective):
            continue
        effective = metadata_effective or signed_effective
        if not effective or effective > service_date:
            continue
        if not re.search(r'\b(?:executed|signed|signature)\b', text, re.I):
            continue
        locator = signed_network_attachment(extracted, str(network.get('network_name') or ''))
        if not locator:
            continue
        documents.append((document, effective, 'Library metadata' if metadata_effective else 'signed agreement text', locator))
    if len(documents) != 1:
        return None
    document, agreement_effective, effective_source, locator = documents[0]
    return {'decision': 'in_network_contract_supported', 'network_name': network['network_name'],
            'product': product, 'product_network_source': relationship['source'],
            'product_network_reference': relationship.get('reference'),
            'billing_npi': billing_npi, 'rendering_npi': rendering_npi, 'tin': tin,
            'tin_source': 'eCW billing provider' if ecw_tin else 'scoped clinic network record',
            'location_id': location, 'place_of_service': pos, 'effective_from': network['effective_from'],
            'effective_to': network.get('effective_to'), 'service_date': service_date,
            'document_id': document['id'], 'document_label': document.get('label'),
            'document_sha256': document['sha256'], 'provider_participation': 'clinic_agreement',
            'agreement_effective_from': agreement_effective,
            'agreement_effective_source': effective_source,
            'network_attachment_source': 'signed network page' if locator.startswith('Page ') else 'attachment A section',
            'mismatch_warnings': warnings,
            'reason': (f"The signed clinic agreement names {network['network_name']}. "
                       + (' '.join(warnings) if warnings else 'No visit detail differs from the agreement record.'))}
