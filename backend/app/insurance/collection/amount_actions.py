"""Concrete collection actions for unresolved insured estimates."""
from __future__ import annotations

from app.insurance import commercial
from app.insurance.benefit_review import selected_service_blocker, selected_service_detail
from app.insurance.collection.enrollment_actions import ENROLLMENT_REASON_ACTION, ENROLLMENT_DEFAULT
from app.insurance.collection.pricing_actions import priced_line_issue, priced_payer_gap
from app.insurance.collection.requirements import requirement_blocker


def _payer_label(result: dict, fallback: str) -> str:
    return 'UHC' if commercial.is_united_healthcare_payer(result) else str(result.get('payer') or fallback)


def _mismatch_fields(result: dict) -> str:
    identity = result.get('identity') or (result.get('benefits') or {}).get('identity') or {}
    fields = {row.get('field') for row in [*(identity.get('differences') or []),
                                             *(identity.get('policy_differences') or [])]}
    labels = {'first_name': 'first name', 'last_name': 'surname', 'birth_date': 'date of birth',
              'member_id': 'member ID', 'subscriber_first': 'subscriber first name',
              'subscriber_last': 'subscriber surname', 'relationship': 'subscriber relationship'}
    return ', '.join(label for field, label in labels.items() if field in fields)


def _collection_action(code: str, result: dict, source_errors: list[dict] | None = None) -> str:
    botox = (result.get('service_plan') or {}).get('kind') == 'botox' or result.get('botox_review') is not None
    line_blocker = selected_service_blocker((result.get('benefits') or {}).get('network_status') or 'unknown',
        result.get('identity') or (result.get('benefits') or {}).get('identity') or {},
        (result.get('price_range') or {}).get('lines') or [])
    if line_blocker and line_blocker[0] == code:
        return selected_service_detail(line_blocker)[1]
    if code in {'cost_share_term_unresolved', 'deductible_applicability_conflict',
                'individual_deductible_unresolved', 'individual_oop_unresolved'}:
        payer_gap = priced_payer_gap(result, _payer_label(result, 'the payer'))
        if payer_gap and payer_gap[1] == code:
            return payer_gap[2]
    if code == 'saved_clinic_pricing_changed':
        return 'rerun the insured estimate with the current clinic contract and CPT rates before collecting'
    if code == 'clinic_agreement_exclusion':
        if (result.get('network_evidence') or {}).get('decision') == 'out_of_network_by_clinic_contract_inventory':
            return ('select the clinician-documented CPT and obtain its applicable out-of-network '
                    'allowance before collecting')
        return ('verify a separately executed agreement or a product-specific UHC provider network statement, '
                'select the clinician-documented CPT, and obtain its applicable allowed amount before collecting')
    if code == 'qmb_protected':
        return 'submit the claim and follow the QMB billing protections'
    if code == 'qmb_status_unresolved':
        return 'verify the member’s QMB status and applicable billing protections before collecting'
    if code == 'provider_enrollment':
        failure_reason = (result.get('eligibility_failure') or {}).get('reason_code')
        return ENROLLMENT_REASON_ACTION.get(failure_reason, ENROLLMENT_DEFAULT)[1]
    if code == 'multiple_active_policies':
        mismatches = _mismatch_fields(result)
        recovery = result.get('recovery') or {}
        count = recovery.get('active_policy_count')
        if type(count) is not int:
            count = sum(row.get('active_medical') is True for row in recovery.get('candidates') or [])
        order = 'primary, secondary and tertiary order' if type(count) is int and count >= 3 else 'payer order'
        products = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
        if any('CHOICE PLUS' in str(name).upper() for name in products):
            coordination = result.get('coordination') or {}
            identity = result.get('identity') or (result.get('benefits') or {}).get('identity') or {}
            if coordination.get('state') == 'indeterminate':
                if identity.get('status') == 'review':
                    return (f'check the submitted Stedi COB transaction outcome before retrying, reconcile '
                            f'{mismatches or "patient and subscriber identity"} with UHC against eCW, then rerun '
                            'eligibility and COB before selecting the documented office CPT')
                return 'check the submitted Stedi COB transaction outcome before retrying, then select the documented office CPT'
            if identity.get('status') == 'review':
                return (f'reconcile {mismatches or "patient and subscriber identity"} with UHC against eCW, '
                        f'then rerun eligibility and a UHC Stedi coordination-of-benefits check for {order} '
                        'before selecting the documented office CPT')
            if coordination.get('status') in {'primacy_undetermined', 'primacy_unmapped'}:
                return f'ask UHC and the other payers to establish {order}, then select the documented office CPT'
            if coordination.get('status') == 'primacy_reported':
                return (f'match the payer-reported roles to all active policies, verify secondary benefits, '
                        'and select the documented office CPT before billing')
            if coordination.get('status') == 'primacy_conflict':
                return 'ask UHC to reconcile conflicting medical payer roles by benefit scope and visit date, then select the office CPT'
            if coordination.get('status') == 'no_cob_found':
                return f'ask all payers to establish {order} because Stedi COB found no other plan, then select the office CPT'
            if coordination.get('status') == 'no_benefit_overlap':
                return 'select the clinician-documented office CPT, then ask each payer which policy covers it because Stedi COB found no overlapping benefits'
            if coordination.get('status') == 'cob_not_required':
                return 'select the clinician-documented office CPT and ask each payer how to bill it because Stedi says COB is not required'
            if coordination.get('status') in {'rejected', 'invalid_response'}:
                return 'reconcile the member details and UHC COB route, rerun UHC eligibility and COB, then select the documented office CPT'
            if coordination.get('reason_code') == 'cob_payer_route_unsupported':
                return f'ask UHC directly to establish {order}, then select the documented office CPT'
            if coordination.get('reason_code') in {'cob_payer_route_missing', 'cob_payer_route_lookup_failed',
                                                   'cob_payer_route_unresolved',
                                                   'cob_payer_support_unreported'}:
                return f'resolve the exact UHC COB payer route, establish {order}, and select the documented office CPT'
            if coordination.get('reason_code') == 'cob_credentials_missing':
                return 'clinic administrator: connect the Stedi COB account, establish payer order, and select the documented office CPT'
            if coordination.get('reason_code') == 'cob_future_visit_date':
                return 'reopen this visit on its date to run UHC COB, then select the clinician-documented office CPT'
            if coordination.get('reason_code') == 'cob_visit_date_too_old':
                return f'ask UHC to establish {order} for this older visit, then select the documented office CPT'
            if coordination.get('reason_code') == 'cob_visit_date_invalid':
                return 'correct the visit date in eCW, then run the UHC COB check and select the documented office CPT'
            if coordination.get('reason_code') in {'cob_subscriber_fields_missing', 'cob_dependent_fields_missing'}:
                fields = ', '.join(coordination.get('missing_fields') or []) or 'payer member identity fields'
                return f'obtain {fields} from UHC, then run the COB check and select the documented office CPT'
            if coordination.get('reason_code') == 'cob_provider_identity_missing':
                return 'link the rendering or billing provider NPI and name, then run the UHC COB check and select the documented office CPT'
            if coordination.get('reason_code') in {'cob_visit_coverage_date_mismatch', 'cob_active_coverage_missing',
                                                   'cob_eligibility_response_missing'}:
                return 'obtain a UHC 271 for this visit date and member, then run COB and select the documented office CPT'
            return ((f'reconcile {mismatches}, ' if mismatches else '')
                    + f'run a UHC Stedi coordination-of-benefits check for {order}, '
                    'and select the documented office CPT before billing')
        return (f'confirm {order} with the payers'
                + (f' and reconcile {mismatches}' if mismatches else '') + ' before billing')
    if code == 'policy_identity_mismatch':
        mismatches = _mismatch_fields(result)
        action = f'reconcile {mismatches or "patient identity"} with UHC'
        service_plan = result.get('service_plan') or {}
        if botox and not result.get('codes'):
            action += ', then select the documented injection, drug units and any separate E/M service'
        elif not botox and ((result.get('price_range') or {}).get('mode') == 'alternatives'
                or (service_plan and not service_plan.get('selected_codes'))):
            action += ', select the documented office CPT'
        if (not botox or any(str(row.get('code') or '').startswith('992') for row in result.get('codes') or [])) and any('which copay or coinsurance term applies' in str(reason).lower()
               for reason in (result.get('benefits') or {}).get('review_reasons', [])):
            action += ', and ask UHC which office copay applies at this place of service'
        return action + ' before using these benefit balances'
    if code == 'payer_identity_match_missing':
        missing = (result.get('identity') or (result.get('benefits') or {}).get('identity') or {}).get('missing_fields') or []
        labels = {'member_id': 'member ID', 'patient_demographics': 'patient name/date of birth',
                  'relationship': 'subscriber relationship'}
        fields = ', '.join(labels.get(field, field) for field in missing)
        return (f'confirm {fields} with UHC and eCW, then rerun the insured estimate' if fields else
                'compare the saved payer patient, subscriber and member ID with eCW, then rerun the insured estimate')
    if code in {'clinic_contract_missing', 'clinic_product_mismatch', 'clinic_payer_connection_draft'}:
        products = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
        if any('REGIONAL PPO' in str(name).upper() for name in products):
            certified = (result.get('network_evidence') or {}).get('decision') == 'out_of_network_by_clinic_contract_inventory'
            steps = [] if certified else ['check UHC Regional PPO participation under a separate agreement']
            if not ((result.get('service_plan') or {}).get('selected_codes') or result.get('selected_ids')):
                steps.append('select the documented E/M CPT')
            steps.append('obtain that CPT’s allowed amount from UHC or a validated Medicare benchmark' if certified
                         else 'obtain that CPT’s allowed amount from the applicable agreement or UHC out-of-network quote')
            benefits = result.get('benefits') or {}
            if benefits.get('oop_annual_conflict_ids'):
                steps.append('ask UHC which annual individual out-of-pocket maximum applies')
            if benefits.get('oop_remaining_conflict_ids'):
                steps.append('ask UHC which remaining individual out-of-pocket balance applies')
            return ', '.join(steps[:-1]) + (', and ' if len(steps) > 1 else '') + steps[-1]
        if code == 'clinic_payer_connection_draft':
            return ('clinic administrator: link an applicable agreement and verified network for this exact UHC '
                    'product and provider, enable its payer connection and estimate profile, then rerun the insured estimate')
        return (f"confirm this exact product and rendering provider with {_payer_label(result, 'the payer')}, "
                'then select the clinician-documented CPT')
    if code == 'provider_network_unresolved':
        return 'link the exact plan and rendering provider to the current clinic network agreement, then rerun the insured estimate'
    if code == 'coverage_status_conflict':
        return 'ask the payer whether the planned CPT is covered on this visit date, then rerun the insured estimate'
    if code == 'coverage_date_invalid':
        return 'obtain the correct coverage dates from the payer for this visit, then rerun the insured estimate'
    if code == 'provider_network_conflict':
        payer = _payer_label(result, 'the payer')
        return f'ask {payer} whether the rendering provider participates in this exact product at this location, correct the clinic network record, and rerun the insured estimate'
    if code in {'contract_rate_missing', 'contract_rate_conflict'}:
        lines = [line for line in (result.get('price_range') or {}).get('lines') or []
                 if line.get('calculation') is None and line.get('code')]
        codes = ', '.join(dict.fromkeys(f"CPT {line['code']}" for line in lines)) or 'the documented CPT'
        payer = _payer_label(result, 'payer')
        products = (((result.get('plan_details') or {}).get('stedi') or {}).get('names') or [])
        product = str(products[0]).strip()[:120] if len(products) == 1 else ''
        product_scope = f' under {product}' if product and product.casefold() not in {'ci', 'commercial', 'ppo', 'hmo'} else ''
        if code == 'contract_rate_conflict':
            return f'choose the applicable {payer} contract rate for {codes}{product_scope}, the rendering provider, visit date and location'
        return f'load the active {payer} contract rate for {codes}{product_scope}, the rendering provider, visit date and location'
    if code == 'planned_cpt_unselected':
        return 'price the booked service alternatives from the applicable plan and clinic evidence; narrow the CPT level when current care supports it'
    if code == 'planned_cpt_missing':
        return ('document the performed injection, drug units and any separate E/M service, then rerun the insured estimate'
                if botox else 'document the planned CPT in eCW and rerun the insured estimate')
    if code == 'botox_clinical_review_required':
        return 'complete the clinician review of the selected Botox care and rerun the insured estimate'
    if code == 'service_selection_invalid':
        issue = str(((result.get('price_range') or {}).get('service_issues') or [''])[0]).split(';', 1)[0].strip()
        return (f'{issue[:1].lower()}{issue[1:].rstrip(".")} and rerun the insured estimate' if issue else
                'correct the selected CPT combination and rerun the insured estimate')
    if code == 'service_pricing_issue':
        line_issue = priced_line_issue(result)
        if line_issue:
            code_value, issue = line_issue
            return f'address {code_value}: {issue.replace(";", ",").rstrip(".")}, then rerun the insured estimate'
    if code == 'calculation_unavailable':
        return 'have the clinic administrator inspect the selected CPT pricing result and rerun the insured estimate'
    if code == 'financial_source_error':
        if any(error.get('kind') == 'invalid_calculation' for error in (source_errors or [])):
            return 'have the clinic administrator correct the price calculation and rerun the insured estimate'
        return 'verify the flagged benefit field with the payer and rerun the insured estimate'
    if code == 'saved_payer_response_unavailable':
        return 'rerun the payer check before collecting or applying the saved benefit terms'
    if code == 'out_of_network_allowance_missing':
        payer = _payer_label(result, 'the payer')
        next_step = (f'ask {payer} for the CPT-specific out-of-network allowed amount for this member, '
                     'rendering provider, place and visit date, then have a clinic administrator record the payer reference under Services')
        if (result.get('price_range') or {}).get('mode') == 'alternatives':
            return f'{next_step} for each booked service alternative'
        return next_step
    if code == 'no_policy_returned':
        return 'reconcile the member and policy details with the payer before filing the claim'
    if code == 'card_conflict':
        return 'compare the insurance card with the eCW policy record, correct the mismatched field, and rerun the payer check'
    if code == 'member_identity_unverified':
        return 'correct the rejected member or policyholder details with the payer and rerun the eligibility check'
    if code in {'referral_required', 'authorization_required', 'referral_authorization_required'}:
        return requirement_blocker(result)[2]
    return 'resolve the stated input before billing the patient'
