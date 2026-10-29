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


BLUECARD_MANUAL = 'https://www.bcbstx.com/docs/provider/tx/claims/bluecard/bluecard-program-prov-manual.pdf'
TEXAS_BLUE_NAMES = {
    'bcbstx', 'bcbstexas', 'bluecrossblueshieldtx', 'bluecrossblueshieldtexas',
    'bluecrossblueshieldoftexas', 'bluecrossandblueshieldtx',
    'bluecrossandblueshieldtexas', 'bluecrossandblueshieldoftexas',
}
BLUE_PAYER = re.compile(r'\b(?:bcbs[a-z]*|blue\s*cross|blue\s*shield)\b', re.I)


def eligibility_route(result: dict, payer_id: str, connection: dict | None = None) -> dict | None:
    """Use the Stedi directory route when the home plan has no clinic connection."""
    if connection and connection.get('status') == 'staff_guidance_enabled':
        return connection
    saved = result.get('stedi_payer') or {}
    if (isinstance(saved, dict) and payer_id and str(saved.get('id') or '') == payer_id
            and BLUE_PAYER.search(str(saved.get('name') or ''))):
        return {'id': f'stedi:{payer_id}', 'display_name': saved['name']}
    return None


def _same_local_payer(document_name: str, connection_name: str) -> bool:
    left = insurance_identity.normalized(document_name)
    right = insurance_identity.normalized(connection_name)
    return left == right or (left in TEXAS_BLUE_NAMES and right in TEXAS_BLUE_NAMES)


def no_blue_contract(practice_id: str, payer: dict | None, service_date: str) -> bool:
    """Report absence only when no current Blue contract is registered in the clinic Library."""
    if not payer or not BLUE_PAYER.search(str(payer.get('display_name') or '')) or not service_date:
        return False
    return not any(
        row.get('kind') == 'contract' and row.get('content_type') == 'application/pdf'
        and BLUE_PAYER.search(str(row.get('payer') or ''))
        and (not row.get('effective_from') or row['effective_from'] <= service_date)
        and (not row.get('effective_to') or row['effective_to'] >= service_date)
        for row in staff_store.list_library(practice_id))


def _bluecard_member(route: dict, local: dict, product: str, source: dict) -> dict | None:
    """Link a Blue home plan to the clinic's signed Texas BlueCard agreement."""
    blue_name = BLUE_PAYER
    local_name = str(local.get('display_name') or '')
    route_name = str(route.get('display_name') or '')
    home = source.get('payer_verification_party') or {}
    if (insurance_identity.normalized(local_name) not in TEXAS_BLUE_NAMES
            or len(source.get('payer_plan_names') or []) != 1
            or insurance_identity.normalized(product)
               != insurance_identity.normalized(source['payer_plan_names'][0])
            or re.search(r'\b(?:DENTAL|VISION|PHARMACY|HEARING)\b', product, re.I)):
        return None
    if route.get('id') == local.get('id'):
        name = str(home.get('name') or '')
        if (not blue_name.search(name)
                or insurance_identity.normalized(name) in TEXAS_BLUE_NAMES
                or not str(home.get('payer_id') or '').strip()):
            return None
        return {'home_plan': name, 'home_plan_evidence_path': home.get('evidence_path')}
    if not blue_name.search(route_name):
        return None
    if home and (not blue_name.search(str(home.get('name') or ''))
                 or not str(home.get('payer_id') or '').strip()
                 or insurance_identity.normalized(home.get('name'))
                    != insurance_identity.normalized(route_name)):
        return None
    return {'home_plan': home.get('name') or route_name,
            'home_plan_evidence_path': home.get('evidence_path') or 'Stedi eligibility payer route'}


def _signed_bluecard_clause(extracted: dict) -> str | None:
    for number, page in enumerate(extracted.get('pages') or [], 1):
        text = str(page.get('text') or '')
        if re.search(r'\bBlueCard\.\s*Provider acknowledges and agrees\b.{0,500}?'
                     r'\bProvider will provide or arrange for Covered Services\b', text, re.I | re.S):
            return f'Page {number}, BlueCard participation clause'
    return None


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
        if re.search(r'ATTACHMENT\s+A\s+TEXAS\s+PROVIDER\s+NETWORKS', text, re.I):
            if re.search(r'Authorized\s+Signature\s*:\s*' + words, text, re.I):
                return f'Page {number}, signed network attachment'
            for match in re.finditer(words, text, re.I):
                preceding = text[max(0, match.start() - 120):match.start()]
                following = text[match.end():match.end() + 340]
                if (re.search(r'Authorized\s+Signature\s*:', preceding, re.I)
                        and re.search(r'Provider\s+Network', following[:100], re.I)
                        and re.search(r'\bDate\s*:', following, re.I)
                        and re.search(r'\d{1,2}/\d{1,2}/\d{4}', following)):
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
    if (not payer or benefits.get('active') is not True
            or benefits.get('service_date_matches') is False
            or benefits.get('network_conflict') or benefits.get('network_status') == 'out_of_network'
            or not source.get('payer_plan_bound')):
        return None
    service_date = str(inputs.get('service_date') or '')
    plan_details = ((result.get('plan_details') or {}).get('stedi') or {})
    product = (plan_details.get('identified_product')
               or next(iter(source.get('payer_plan_names') or []), ''))
    fep_basic = (product == 'FEP Blue Basic PSHB'
                 and source.get('payer_plan_names') in (['BASIC'], ['FEP Blue Basic PSHB'])
                 and plan_details.get('names') == ['BASIC'])
    if BLUE_PAYER.search(str(payer.get('display_name') or '')) and (
            len(source.get('payer_plan_names') or []) != 1 or
            (not fep_basic and insurance_identity.normalized(product) !=
             insurance_identity.normalized(source['payer_plan_names'][0])) or
            (not fep_basic and len(plan_details.get('names') or []) == 1 and
             insurance_identity.normalized(product) !=
             insurance_identity.normalized(plan_details['names'][0]))):
        return None
    billing = source.get('billing_provider') or {}
    rendering_npi = str((inputs.get('rendering_provider') or {}).get('npi') or '')
    billing_npi, ecw_tin = str(billing.get('npi') or ''), str(billing.get('tin') or '').replace('-', '')
    location, pos = str(source.get('location_id') or ''), str(inputs.get('place_of_service') or '')
    if not service_date or not product:
        return None
    local_connections = [row for row in commercial_store.list_connections(practice_id)
                         if row.get('status') == 'staff_guidance_enabled'
                         and insurance_identity.normalized(row.get('display_name')) in TEXAS_BLUE_NAMES]
    local_blue = local_connections[0] if len(local_connections) == 1 else None
    if str(payer.get('id') or '').startswith('stedi:') and not local_blue:
        return None
    candidates = []
    for network in commercial_store.list_networks(practice_id):
        if (network.get('payer_connection_id') not in {payer.get('id'), (local_blue or {}).get('id')}
                or network.get('status') != 'verified'
                or not network.get('effective_from') or network['effective_from'] > service_date
                or (network.get('effective_to') and network['effective_to'] < service_date)):
            continue
        same_route = network.get('payer_connection_id') == payer.get('id')
        relationship = (network_participation.product_network_relationship(
            payer.get('display_name') or '', product, source.get('payer_network_names') or [],
            network.get('network_name') or '', service_date) if same_route else None)
        bluecard_candidate = (_bluecard_member(payer, local_blue, product, source) if local_blue
            and network.get('payer_connection_id') == local_blue.get('id') else None)
        if (not relationship and not bluecard_candidate and same_route
                and BLUE_PAYER.search(str(payer.get('display_name') or ''))
                and not re.search(r'\b(?:DENTAL|VISION|PHARMACY|HEARING)\b', product, re.I)):
            relationship = {'source': 'clinic_blue_contract_policy',
                            'network_name': network.get('network_name')}
        if not relationship and not bluecard_candidate:
            continue
        tin = str(network.get('tin') or '').replace('-', '')
        if (not all((billing_npi, rendering_npi, tin, location, pos,
                     network.get('billing_npi'), network.get('rendering_npi'),
                     network.get('location_id'), network.get('place_of_service')))
                or billing_npi != str(network['billing_npi'])
                or rendering_npi != str(network['rendering_npi'])
                or location != str(network['location_id'])
                or pos != str(network['place_of_service'])
                or (ecw_tin and ecw_tin != tin)
                or billing.get('tin_conflict')):
            continue
        warnings = []
        if (source.get('plan') and network.get('plan_matcher')
                and insurance_identity.normalized(source['plan']) !=
                    insurance_identity.normalized(network['plan_matcher'])):
            warnings.append('eCW plan code differs from the clinic agreement record.')
        candidates.append((network, relationship, warnings,
                           local_blue if bluecard_candidate and not relationship else payer,
                           bluecard_candidate))
    if not candidates:
        return None
    fewest = min(len(row[2]) for row in candidates)
    closest = [row for row in candidates if len(row[2]) == fewest]
    if len(closest) != 1:
        return None
    network, relationship, warnings, contract_payer, bluecard_candidate = closest[0]
    tin = str(network.get('tin') or '').replace('-', '')
    documents = []
    for metadata in staff_store.list_library(practice_id):
        if (metadata.get('kind') != 'contract' or metadata.get('content_type') != 'application/pdf'
                or not metadata.get('payer')
                or (metadata.get('effective_to') and metadata['effective_to'] < service_date)
                or not _same_local_payer(str(metadata['payer']), str(contract_payer.get('display_name') or ''))):
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
    if not relationship and bluecard_candidate:
        if not locator.startswith('Page '):
            return None
        clause = _signed_bluecard_clause(document.get('extraction') or {})
        if not clause:
            return None
        relationship = {'source': 'signed_bluecard_agreement', 'reference': BLUECARD_MANUAL,
                        'network_name': network['network_name'], 'bluecard_clause_source': clause,
                        'home_plan': bluecard_candidate['home_plan'],
                        'home_plan_evidence_path': bluecard_candidate['home_plan_evidence_path']}
    return {'decision': 'in_network_contract_supported', 'network_name': network['network_name'],
            'contract_plan_matcher': network.get('plan_matcher'),
            'product': product, 'product_network_source': relationship['source'],
            'product_network_reference': relationship.get('reference'),
            'bluecard_clause_source': relationship.get('bluecard_clause_source'),
            'home_plan': relationship.get('home_plan'),
            'home_plan_evidence_path': relationship.get('home_plan_evidence_path'),
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
            'reason': ((f"The clinic applies its signed Blue agreement for {network['network_name']} to this Blue medical plan. "
                        if relationship['source'] == 'clinic_blue_contract_policy' else
                        f"The signed clinic agreement names {network['network_name']}. ")
                       + (' '.join(warnings) if warnings else 'No visit detail differs from the agreement record.'))}
