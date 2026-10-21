"""Build and interpret Stedi COB checks without inferring claim payer order."""
from __future__ import annotations

from datetime import date
from copy import deepcopy
import re

from app.insurance import commercial
from app.insurance.identity import birth_date, payer_identity_verified


URL = 'https://healthcare.us.stedi.com/2024-04-01/coordination-of-benefits'


def with_ecw_birth_dates(eligibility: object, coverage: dict) -> tuple[object, tuple[str, ...]]:
    """Fill absent 271 DOBs from the matched eCW policy, never overwrite a payer value."""
    if not isinstance(eligibility, dict) or not isinstance(coverage, dict):
        return eligibility, ()
    result = eligibility
    sources = []
    for person, field, source in (('subscriber', 'subscriber_birth_date', 'subscriber date of birth from eCW Coverage'),
                                  ('dependent', 'patient_birth_date', 'dependent date of birth from eCW Patient')):
        returned = eligibility.get(person)
        if not isinstance(returned, dict) or not returned or returned.get('dateOfBirth'):
            continue
        try:
            value = birth_date(coverage.get(field))
            date.fromisoformat(value)
        except (ValueError, TypeError):
            continue
        if result is eligibility:
            result = deepcopy(eligibility)
        result[person]['dateOfBirth'] = value
        sources.append(source)
    return result, tuple(sources)


def _visit_date(value: str, today: date) -> tuple[str | None, str | None]:
    try:
        service = date.fromisoformat(value)
        try:
            earliest = today.replace(year=today.year - 2)
        except ValueError:
            earliest = today.replace(year=today.year - 2, day=28)
        if service > today:
            return None, 'cob_future_visit_date'
        if service < earliest:
            return None, 'cob_visit_date_too_old'
        return service.isoformat(), None
    except (ValueError, TypeError):
        return None, 'cob_visit_date_invalid'


def _person(value: object, *, member: bool, label: str | None = None) -> tuple[dict | None, tuple[str, ...]]:
    label = label or ('subscriber' if member else 'dependent')
    required = (f'{label} first name', f'{label} surname', f'{label} date of birth')
    if member:
        required += (f'{label} member ID',)
    if not isinstance(value, dict):
        return None, required
    try:
        person = commercial.payer_person(value)
    except (AttributeError, TypeError):
        return None, required
    first, last, dob = (person.get(key) for key in ('firstName', 'lastName', 'dateOfBirth'))
    missing = []
    if not isinstance(first, str) or not first.strip():
        missing.append(f'{label} first name')
    if not isinstance(last, str) or not last.strip():
        missing.append(f'{label} surname')
    try:
        born = birth_date(dob)
        date.fromisoformat(born)
    except (ValueError, TypeError):
        born = None
        missing.append(f'{label} date of birth')
    identifier = person.get('memberId')
    if member and (not isinstance(identifier, str) or not identifier.strip()):
        missing.append(f'{label} member ID')
    if missing:
        return None, tuple(missing)
    result = {'firstName': first.strip(), 'lastName': last.strip(), 'dateOfBirth': born}
    if member:
        result['memberId'] = identifier.strip()
    return result, ()


def prepare_request(payer: dict, provider: dict, eligibility: dict, benefits: dict,
                    service_date: str, *, today: date | None = None) -> tuple[dict | None, str | None, tuple[str, ...]]:
    """Return a COB request or the exact preflight blocker without patient values."""
    if not isinstance(payer, dict) or not isinstance(payer.get('stediId'), str) or not payer['stediId']:
        return None, 'cob_payer_route_missing', ()
    support = payer.get('transactionSupport') or {}
    if not isinstance(support, dict) or support.get('coordinationOfBenefits') != 'SUPPORTED':
        return None, 'cob_payer_route_unsupported', ()
    if not isinstance(benefits, dict) or benefits.get('active') is not True:
        return None, 'cob_active_coverage_missing', ()
    if benefits.get('service_date_matches') is False:
        return None, 'cob_visit_coverage_date_mismatch', ()
    identity = benefits.get('identity') or {}
    if not isinstance(identity, dict):
        return None, 'cob_identity_mismatch', ()
    if (not payer_identity_verified(identity)
            or identity.get('differences') or identity.get('policy_differences')):
        return None, 'cob_identity_mismatch', ()
    if not isinstance(eligibility, dict):
        return None, 'cob_eligibility_response_missing', ()
    visit, date_reason = _visit_date(service_date, today or date.today())
    if date_reason:
        return None, date_reason, ()
    dependent_record = eligibility.get('dependent')
    parent_record = eligibility.get('subscriber')
    dependent_id = dependent_record.get('memberId') if isinstance(dependent_record, dict) else None
    parent_id = parent_record.get('memberId') if isinstance(parent_record, dict) else None
    dependent_has_own_id = (isinstance(dependent_id, str) and bool(dependent_id.strip())
                            and dependent_id.strip() != str(parent_id or '').strip())
    # Stedi identifies a dependent with their own member ID as the subscriber
    # for this COB lookup; the parent is not included in that request.
    subscriber, missing = _person(dependent_record if dependent_has_own_id else parent_record,
                                  member=True, label='dependent' if dependent_has_own_id else 'subscriber')
    if missing:
        return None, 'cob_dependent_fields_missing' if dependent_has_own_id else 'cob_subscriber_fields_missing', missing
    dependent = None
    if dependent_record and not dependent_has_own_id:
        dependent, missing = _person(dependent_record, member=False)
        if missing:
            return None, 'cob_dependent_fields_missing', missing
    if not isinstance(provider, dict):
        return None, 'cob_provider_identity_missing', ('provider NPI', 'provider name')
    npi = provider.get('rendering_npi')
    if (re.fullmatch(r'\d{10}', str(npi or ''))
            and all(isinstance(provider.get(field), str) and provider[field].strip()
                    for field in ('rendering_first', 'rendering_last'))):
        requesting = {'npi': npi, 'firstName': provider['rendering_first'], 'lastName': provider['rendering_last']}
    elif (re.fullmatch(r'\d{10}', str(provider.get('billing_npi') or ''))
          and isinstance(provider.get('organization_name'), str)
          and provider['organization_name'].strip()):
        requesting = {'npi': provider['billing_npi'], 'organizationName': provider['organization_name']}
    else:
        return None, 'cob_provider_identity_missing', ('provider NPI', 'provider name')
    body = {'tradingPartnerServiceId': payer['stediId'], 'provider': requesting,
            'subscriber': subscriber, 'encounter': {'dateOfService': visit, 'serviceTypeCode': '30'}}
    if dependent:
        body['dependent'] = dependent
    return body, None, ()


def request_body(payer: dict, provider: dict, eligibility: dict, benefits: dict,
                 service_date: str, *, today: date | None = None) -> dict | None:
    """Only use a matched active 271 and a COB-supported payer for this visit."""
    return prepare_request(payer, provider, eligibility, benefits, service_date, today=today)[0]


def response_summary(payload: object) -> dict:
    """Retain payer-reported primacy without assigning policies by discovery order."""
    if not isinstance(payload, dict) or payload.get('errors'):
        return {'status': 'rejected', 'primacy_determined': False, 'payer_roles': []}
    coordination = payload.get('coordinationOfBenefits')
    if not isinstance(coordination, dict):
        return {'status': 'invalid_response', 'primacy_determined': False, 'payer_roles': []}
    classification = coordination.get('classification')
    no_cob = {'MemberFoundNoCob': 'no_cob_found',
              'CoverageOverlapNoBenefitOverlap': 'no_benefit_overlap',
              'CoverageOverlapExistsNotSubjectToCob': 'cob_not_required'}
    if isinstance(classification, str) and classification in no_cob:
        return {'status': 'invalid_response' if coordination.get('primacyDetermined') is True else no_cob[classification],
                'primacy_determined': False, 'payer_roles': [],
                'coverage_overlap': coordination.get('coverageOverlap') is True,
                'benefit_overlap': coordination.get('benefitOverlap') is True,
                'classification': classification}
    determined = coordination.get('primacyDetermined') is True
    roles = []
    for benefit in payload.get('benefitsInformation') or []:
        if not isinstance(benefit, dict) or benefit.get('code') != 'R':
            continue
        service_codes = {str(code) for code in benefit.get('serviceTypeCodes') or []}
        service_names = {str(name).casefold() for name in benefit.get('serviceTypes') or []}
        if not (service_codes & {'1', '30', '98'} or 'medical care' in service_names):
            continue
        for entity in benefit.get('benefitsRelatedEntities') or []:
            if not isinstance(entity, dict):
                continue
            role = str(entity.get('entityIdentifier') or '')
            if role not in {'Primary Payer', 'Secondary Payer', 'Tertiary Payer'}:
                continue
            name = entity.get('entityName')
            if isinstance(name, str) and name.strip():
                entry = {'role': role, 'payer_name': name.strip()}
                if entry not in roles:
                    roles.append(entry)
    if not determined:
        roles = []
    names_by_role = {}
    roles_by_name = {}
    for entry in roles:
        names_by_role.setdefault(entry['role'], set()).add(entry['payer_name'].casefold())
        roles_by_name.setdefault(entry['payer_name'].casefold(), set()).add(entry['role'])
    conflicting = any(len(names) > 1 for names in names_by_role.values()) or any(
        len(payer_roles) > 1 for payer_roles in roles_by_name.values())
    return {'status': 'primacy_conflict' if determined and conflicting else
                      'primacy_reported' if determined and roles else
                      'primacy_unmapped' if determined else 'primacy_undetermined',
            'primacy_determined': determined, 'payer_roles': roles,
            'coverage_overlap': coordination.get('coverageOverlap') is True,
            'benefit_overlap': coordination.get('benefitOverlap') is True,
            'classification': classification}
