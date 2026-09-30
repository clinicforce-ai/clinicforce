"""Adapt dated FHIR benefit evidence to the existing service-aware calculator.

This is a format adapter, not a payer response or a source of inferred terms.
The original FHIR resource accompanies the adapted rows in protected storage.
"""
from decimal import Decimal, InvalidOperation
from datetime import date

from app.insurance import commercial


def _code(concept: dict, system: str) -> str | None:
    codes = {row.get('code') for row in (concept or {}).get('coding', [])
             if row.get('system') == system and row.get('code')}
    return next(iter(codes)) if len(codes) == 1 else None


def payload(summary: dict, source: dict, inputs: dict) -> dict | None:
    # A malformed optional FHIR response must fall through to payer verification.
    try:
        return _payload(summary, source, inputs)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def _payload(summary: dict, source: dict, inputs: dict) -> dict | None:
    """Only provider-bound, current, matched policy evidence can avoid a check."""
    row = summary.get('_response') or {}
    if (summary.get('status') != 'verified_active' or 'benefits' not in row.get('purpose', [])
            or row.get('extension') or row.get('modifierExtension')):
        return None
    identifier = (row.get('requestor') or {}).get('identifier') or {}
    provider_npis = {str((source.get(kind) or {}).get('npi') or '')
                     for kind in ('rendering_provider', 'billing_provider')} - {''}
    if identifier.get('system') != 'http://hl7.org/fhir/sid/us-npi' or identifier.get('value') not in provider_npis:
        return None
    policies = [item for item in row.get('insurance', [])
                if (item.get('coverage') or {}).get('reference') == 'Coverage/' + str(source.get('coverage_id'))]
    if (len(policies) != 1 or policies[0].get('inforce') is not True
            or policies[0].get('extension') or policies[0].get('modifierExtension')
            or any(item not in policies and item.get('inforce') is not False for item in row.get('insurance', []))):
        return None
    period = policies[0].get('benefitPeriod') or {}
    try:
        day = date.fromisoformat(inputs['service_date'])
        if (period.get('start') and day < date.fromisoformat(period['start'][:10])
                or period.get('end') and day > date.fromisoformat(period['end'][:10])):
            return None
    except (TypeError, ValueError):
        return None
    entries = [{'code': '1', 'serviceTypeCodes': ['30']}]
    for index, item in enumerate(policies[0].get('item', [])):
        stc = _code(item.get('category'), 'http://terminology.hl7.org/CodeSystem/ex-benefitcategory')
        procedure = _code(item.get('productOrService'), 'http://www.ama-assn.org/go/cpt')
        network = _code(item.get('network'), 'http://terminology.hl7.org/CodeSystem/benefit-network')
        unit = _code(item.get('unit'), 'http://terminology.hl7.org/CodeSystem/benefit-unit')
        term = _code(item.get('term'), 'http://terminology.hl7.org/CodeSystem/benefit-term')
        # Unsupported qualifications cannot be stripped off otherwise usable money.
        if (not stc or unit != 'individual' or network not in {'in', 'out'}
                or type(item.get('authorizationRequired')) is not bool
                or item.get('modifier') or item.get('extension') or item.get('modifierExtension') or item.get('provider')
                or item.get('productOrService') and not procedure):
            return None
        base = {'serviceTypeCodes': [stc], 'coverageLevelCode': 'IND',
                'inPlanNetworkIndicatorCode': 'Y' if network == 'in' else 'N',
                'authOrCertIndicator': 'Y' if item.get('authorizationRequired') is True else
                                       'N' if item.get('authorizationRequired') is False else 'UNKNOWN',
                'source_reference': summary.get('reference'), 'source_item': index}
        if procedure:
            base['compositeMedicalProcedureIdentifier'] = {'procedureCode': procedure}
        if item.get('description'):
            base['additionalInformation'] = [{'description': item['description']}]
        if item.get('excluded'):
            entries.append({**base, 'code': 'I'})
            continue
        for benefit in item.get('benefit', []):
            kind = _code(benefit.get('type'), 'http://terminology.hl7.org/CodeSystem/benefit-type')
            # Annual benefit maximum / copay maximum are NOT remaining OOP.
            if kind not in {'copay', 'deductible', 'copay-percent'} or benefit.get('extension') or benefit.get('modifierExtension'):
                return None
            try:
                if kind == 'copay-percent':
                    percent = benefit.get('allowedUnsignedInt')
                    if type(percent) is not int or not 0 <= percent <= 100:
                        return None
                    entries.append({**base, 'code': 'A', 'benefitPercent': str(Decimal(percent) / 100)})
                    continue
                money = benefit.get('allowedMoney') or {}
                if money.get('currency') != 'USD':
                    return None
                allowed = commercial._money(money.get('value'), 'eCW benefit')
                if kind == 'deductible':
                    used = benefit.get('usedMoney') or {}
                    if term != 'annual' or used.get('currency') != 'USD':
                        return None
                    consumed = commercial._money(used.get('value'), 'eCW used deductible')
                    if consumed > allowed:
                        return None
                    entries.append({**base, 'code': 'C', 'timeQualifierCode': '29',
                                    'benefitAmount': str(Decimal(allowed - consumed) / 100)})
                else:
                    entries.append({**base, 'code': 'B', 'timeQualifierCode': 'SERVICE',
                                    'benefitAmount': str(Decimal(allowed) / 100)})
            except (commercial.CommercialError, InvalidOperation, TypeError, ValueError):
                return None
    if len(entries) == 1:
        return None
    return {'id': summary.get('reference'), 'benefitsInformation': entries,
            'benefit_source': 'eCW saved eligibility', '_ecw_response': row,
            'planDateInformation': {'serviceDate': inputs['service_date']}}
