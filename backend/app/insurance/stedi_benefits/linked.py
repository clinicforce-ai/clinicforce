"""Bind separate Stedi inquiries before sharing plan-wide medical limits."""
from __future__ import annotations

from app.insurance.stedi_benefits.fields import plan_details
from app.insurance.stedi_benefits.normalize import normalize_stedi_271


def same_member_and_medical_plan(primary: dict, additional: dict) -> bool:
    def person(value: dict) -> tuple[str, ...]:
        name_field = value.get('name') or {}
        name = (name_field.get('person') or {}) if isinstance(name_field, dict) else {}
        name = name if isinstance(name, dict) else {}
        return tuple(str(item or '').strip().casefold() for item in (
            value.get('memberId'), value.get('dateOfBirth'),
            name.get('firstName'), name.get('lastName'), value.get('relationToSubscriber')))
    if not isinstance(primary.get('subscriber'), dict) or not isinstance(additional.get('subscriber'), dict):
        return False
    if not all(person(primary['subscriber'])[index] for index in (0, 2, 3)):
        return False
    if person(primary['subscriber']) != person(additional['subscriber']):
        return False
    if bool(primary.get('dependent')) != bool(additional.get('dependent')):
        return False
    if primary.get('dependent') and (not isinstance(primary['dependent'], dict)
            or not isinstance(additional['dependent'], dict)
            or person(primary['dependent']) != person(additional['dependent'])):
        return False
    if ((primary.get('provider') or {}).get('npi') and (additional.get('provider') or {}).get('npi')
            and primary['provider']['npi'] != additional['provider']['npi']):
        return False
    if primary.get('payerId') and additional.get('payerId') and primary['payerId'] != additional['payerId']:
        return False
    first, second = plan_details(primary), plan_details(additional)
    if any(first.get(key) and second.get(key) and first[key] != second[key]
           for key in ('group_number', 'plan_number')):
        return False
    names, other_names = first.get('names') or [], second.get('names') or []
    return (len(names) == len(other_names) == 1
            and names[0].strip().casefold() == other_names[0].strip().casefold())


def merge_linked_medical_oop(primary: dict, office: dict, service_date: str | None) -> dict:
    """Use only a matched STC 30 medical OOP limit; retain STC 98 office cost sharing."""
    general_payload = ((primary.get('_service_payloads') or {}).get('30')
                       if primary.get('payerId') == 'QDTRP' else None)
    if not isinstance(general_payload, dict) or not same_member_and_medical_plan(primary, general_payload):
        return office
    general = normalize_stedi_271(general_payload, '30', False,
        provider_out_of_network_verified=office.get('network_status') == 'out_of_network',
        service_date=service_date)
    if (general.get('active') is not True or general.get('service_date_matches') is False
            or general.get('coverage_status_conflict') or general.get('coverage_dates_invalid')
            or general.get('source_errors') or general.get('has_secondary')
            or general.get('oop_annual_conflict_ids') or general.get('oop_remaining_conflict_ids')
            or general.get('oop_medical_candidate_ids') or general.get('oop_quarantined_ids')):
        return office
    if any(office.get(key) is not None and general.get(key) is not None
           and office[key] != general[key] for key in ('oop_annual_cents', 'oop_remaining_cents')):
        office.setdefault('review_reasons', []).append(
            'The office and general medical inquiries disagree on the individual out-of-pocket limit. Confirm the applicable balance with the payer.')
        office['requires_review'] = True
        return office
    for value, source in (('oop_annual_cents', 'oop_annual_id'),
                          ('oop_remaining_cents', 'oop_id'),
                          ('family_oop_remaining_cents', None)):
        if office.get(value) is None and general.get(value) is not None:
            office[value] = general[value]
            if source and general.get(source):
                office[source] = 'stc30:' + general[source]
    if office.get('oop_remaining_cents') is not None:
        office['review_reasons'] = [reason for reason in office.get('review_reasons', [])
            if reason != 'Confirm the remaining individual out-of-pocket maximum.']
        office['requires_review'] = bool(office['review_reasons'])
    office.setdefault('reported_options', []).extend(
        {**row, 'id': 'stc30:' + row['id']} for row in general.get('reported_options', [])
        if row.get('kind') == 'out_of_pocket')
    return office
