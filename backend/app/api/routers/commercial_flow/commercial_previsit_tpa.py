"""Bind a Cigna-delegated administrator's benefit checks to one active visit."""
from __future__ import annotations

from copy import deepcopy
from types import ModuleType

from app.insurance.identity import normalized, patient_benefits_bound


def cigna_primary_route(payer_id: str, payload: dict | None) -> bool:
    """Match Stedi's Cigna ID or its primary payer ID to a Cigna 271."""
    return payer_id in {'HGJLR', '62308'} and (payload or {}).get('payerId') == '62308'


def administrator_route(primary: dict) -> str | None:
    """Only follow the payer's explicit medical-benefit contact to a known route."""
    if primary.get('payerId') != '62308' or len(primary.get('plans') or []) != 1:
        return None
    benefits = (primary['plans'][0] or {}).get('benefits') or {}
    entities = []
    for row in benefits.get('contactFollowingEntityForInformation') or []:
        if (row.get('service') or {}).get('value') not in {'30', '98'}:
            continue
        for entity in row.get('relatedEntities') or []:
            if entity.get('type') not in {'PAYER', 'THIRD_PARTY_ADMINISTRATOR'}:
                continue
            name = entity.get('name') or {}
            label = name.get('organization') if isinstance(name, dict) else name
            entities.append(normalized(label))
    if len(set(entities)) != 1 or not entities:
        return None
    # Exact known Stedi medical eligibility route; a free-text contact is not
    # sufficient to select an arbitrary payer from a directory search.
    if entities[0] in {'allegiancebenefitplanmanagement',
                       'allegiancebenefitplanmanagementincorporated'}:
        return '81040'
    return None


def same_administrator_patient(primary: dict, administrator: dict, payer_id: str) -> bool:
    """TPAs can use their own group number, but cannot change the patient."""
    if administrator.get('payerId') != payer_id:
        return False
    def person(value: dict) -> tuple[str, str, str, str]:
        value = value if isinstance(value, dict) else {}
        name = value.get('name') or {}
        name = name.get('person') or {} if isinstance(name, dict) else {}
        return tuple(normalized(value.get(field) if field in {'memberId', 'dateOfBirth'} else name.get(field))
                     for field in ('memberId', 'dateOfBirth', 'firstName', 'lastName'))
    first, second = person(primary.get('subscriber')), person(administrator.get('subscriber'))
    if not all(first) or first != second or bool(primary.get('dependent')) != bool(administrator.get('dependent')):
        return False
    if primary.get('dependent'):
        if person(primary['dependent']) != person(administrator['dependent']):
            return False
        relationship = lambda value: normalized((value or {}).get('relationToSubscriberCode') or
            (value or {}).get('relationToSubscriber'))
        if (relationship(primary['dependent']) and relationship(administrator['dependent'])
                and relationship(primary['dependent']) != relationship(administrator['dependent'])):
            return False
    first_npi = str((primary.get('provider') or {}).get('npi') or '')
    return bool(first_npi and first_npi == str((administrator.get('provider') or {}).get('npi') or ''))


def merge_administrator_benefits(api: ModuleType, payload: dict, benefits: dict,
        delegated: dict, summary_service: str, service_date: str | None,
        office_service_types: list[str], context: dict) -> None:
    """Use administrator cost sharing while retaining Cigna's date and network evidence."""
    primary = api.commercial.normalize_stedi_271(payload, summary_service, False,
        service_date=service_date, office_visit=True,
        office_service_types=office_service_types, **context)
    primary_medical = (primary if primary.get('active') is True and primary.get('service_date_matches') is not False else
        api.commercial.normalize_stedi_271(payload, '30', False,
            service_date=service_date, **context))
    if (benefits.get('active') is True and benefits.get('service_date_matches') is None
            and primary_medical.get('active') is True and primary_medical.get('service_date_matches') is True):
        benefits['service_date_matches'] = True
        benefits['coverage_evidence'] = primary_medical['coverage_evidence']
        benefits['returned_service_dates'] = primary_medical['returned_service_dates']
    elif (benefits.get('active') is True and benefits.get('service_date_matches') is None
            and primary_medical.get('active') is True
            and primary_medical.get('service_date_matches') is None
            and service_date == api.date.today().isoformat()):
        # Both payers answered an exact same-day inquiry as active, without
        # explicit dates. A later visit must still return dated evidence.
        benefits['service_date_matches'] = True
        benefits['normalization_notes'].append(
            'Both payers reported active coverage on the requested current service date without separate date spans.')
    if (primary.get('network_source') == 'payer_participation_message'
            and benefits.get('network_status') == 'unknown' and not benefits.get('network_conflict')):
        for key in ('network_status', 'network_source', 'network_evidence_ids', 'network_provider_npi'):
            benefits[key] = primary[key]
    general_payload = delegated.get('30')
    if general_payload:
        general = api.commercial.normalize_stedi_271(general_payload, '30', False,
            service_date=service_date, **context)
        for key in ('deductible_remaining_cents', 'deductible_annual_cents',
                    'oop_remaining_cents', 'oop_annual_cents', 'family_oop_remaining_cents'):
            if benefits.get(key) is None and general.get(key) is not None:
                benefits[key] = general[key]
        benefits['reported_options'].extend({**row, 'id': f"administrator30:{row['id']}"}
            for row in general.get('reported_options') or [] if row.get('service_type_code') == '30')
    benefits['normalization_notes'].append(
        'The named plan administrator supplied office benefits for the same patient and visit. Its group identifier may differ from the network payer record.')


async def check_administrator(api: ModuleType, *, primary: dict, coverage: dict, inputs: dict,
                              body: dict, basic_body: dict, api_key: str, case: dict,
                              session: dict) -> tuple[dict, str]:
    """Keep each paid inquiry single-shot; retain an indeterminate state for review."""
    route = administrator_route(primary)
    if not route or coverage.get('active_coverages') != 1 or coverage.get('other_active_coverage'):
        return {}, 'complete'
    primary_benefits = api.commercial.normalize_stedi_271(primary, '30', False,
        service_date=inputs['service_date'])
    if (primary_benefits.get('active') is not True or primary_benefits.get('service_date_matches') is False
            or not patient_benefits_bound(api.insurance_identity.compare(primary, coverage))):
        return {}, 'complete'
    subscriber = primary.get('subscriber') or {}
    if not all(subscriber.get(field) for field in ('memberId', 'dateOfBirth', 'name')):
        return {}, 'complete'
    case['previsit']['administrator_request_state'] = 'pending'
    case.update(await api.run_in_threadpool(api._save, case, session, 'previsit-administrator-pending'))
    linked = {}
    for stc in ('98', '30'):
        next_body, next_basic = deepcopy(body), deepcopy(basic_body)
        for request_body in (next_body, next_basic):
            request_body['payerId'] = route
            request_body['subscriber'] = {**request_body['subscriber'],
                'memberId': subscriber['memberId'], 'dateOfBirth': subscriber['dateOfBirth'],
                'name': subscriber['name']}
            request_body['encounter']['services'] = [{'system': 'STC', 'value': stc}]
        try:
            async with api.httpx.AsyncClient(timeout=api.httpx.Timeout(30, connect=5)) as client:
                response, extra, _ = await api._post_stedi_eligibility(client,
                    {'Authorization': api_key, 'Content-Type': 'application/json'}, next_body, next_basic)
        except (api.httpx.HTTPError, ValueError):
            case['previsit']['administrator_request_state'] = 'indeterminate'
            case.update(await api.run_in_threadpool(api._save, case, session, 'previsit-administrator-indeterminate'))
            return linked, 'indeterminate'
        normalized_benefits = (api.commercial.normalize_stedi_271(extra, stc, False,
            service_date=inputs['service_date']) if isinstance(extra, dict) else {})
        if (response.is_error or not isinstance(extra, dict)
                or api.commercial.eligibility_errors(extra)
                or not same_administrator_patient(primary, extra, route)
                or normalized_benefits.get('active') is not True
                or normalized_benefits.get('service_date_matches') is False):
            case['previsit']['administrator_request_state'] = 'partial'
            case.update(await api.run_in_threadpool(api._save, case, session, 'previsit-administrator-partial'))
            return linked, 'partial'
        linked[stc] = extra
    def administrator_group(response: dict) -> str:
        subscriber = response.get('subscriber') or {}
        additional = subscriber.get('additionalInformation') or {}
        return normalized(api.commercial.plan_details(response).get('group_number') or
            additional.get('groupNumber') or additional.get('groupOrPolicyNumber'))
    office_group, medical_group = (administrator_group(linked[stc]) for stc in ('98', '30'))
    office_names = {normalized(name) for name in api.commercial.plan_details(linked['98']).get('names') or []}
    medical_names = {normalized(name) for name in api.commercial.plan_details(linked['30']).get('names') or []}
    if (not office_group or office_group != medical_group
            or office_names and medical_names and office_names.isdisjoint(medical_names)):
        linked.pop('30')
        case['previsit']['administrator_request_state'] = 'partial'
        return linked, 'partial'
    case['previsit']['administrator_request_state'] = 'complete'
    return linked, 'complete'
