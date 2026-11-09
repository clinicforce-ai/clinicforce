"""Synthetic UHC D-SNP office planning, without member data."""

from app.insurance import collection_guidance, commercial, uhc_dsnp_office_proxy
from app.api.routers import commercial_estimator as api  # Register workflow imports.
from app.api.routers.commercial_flow import commercial_previsit_rate_reference as rate_reference


def _result():
    identity = {'status': 'matched', 'binding': 'synthetic-binding', 'match_version': 2}
    return {
        'payer': 'UnitedHealthcare', 'payment_mode': 'insurance', 'visit_type': 'new',
        'identity': identity, 'reason': 'Facial weakness', 'service_date': '2026-11-09',
        'checked_at': '2026-11-08T13:00:00Z',
        'plan_details': {'stedi': {'names': ['UnitedHealthcare Dual Complete TX-S003 (HMO-POS D-SNP)'],
                                   'plan_number': 'H4514-021-000'}},
        'benefits': {'active': True, 'service_date_matches': True, 'identity': identity,
                     'resolved_service_type': '98',
                     'copay_cents': 0, 'deductible_remaining_cents': 0,
                     'coinsurance_percent': '20.00', 'oop_remaining_cents': 890071,
                     'network_status': 'unknown'},
        'available_codes': [{'id': 'office', 'code': '99203', 'units': 1,
                             'source': 'conditional office E/M alternative'}],
        'codes': [], 'price_range': {'mode': 'alternatives', 'lines': []},
    }


def test_unselected_new_patient_office_scenario_has_numeric_share_without_collection():
    result = _result()
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    proxy = uhc_dsnp_office_proxy.estimate(result, '2026-11-09')
    assert proxy['reference_total_cents'] == 8807
    assert proxy['patient_share_cents'] == 1761
    assert proxy['scenario_cpt'] == '99203'
    assert proxy['cpt_selected'] is False
    assert result['price_range'].get('allowed_min_cents') is None
    result['fee_proxy_estimate'] = proxy
    assert collection_guidance.build(result)['amount_cents'] is None


def test_no_cross_plan_or_conflicting_benefit_proxy():
    result = _result()
    result['plan_details']['stedi']['names'] = ['UnitedHealthcare Choice Plus']
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    assert 'temporary_rate_reference' not in result
    assert uhc_dsnp_office_proxy.estimate(result, '2026-11-09') is None
    result = _result()
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    result['benefits']['has_secondary'] = True
    assert uhc_dsnp_office_proxy.estimate(result, '2026-11-09') is None


def test_stedi_shaped_office_response_without_echoed_service_date_still_prices_requested_visit():
    raw = {'subscriber': {'memberId': 'SYNTHETIC', 'firstName': 'Example',
                          'lastName': 'Member', 'dateOfBirth': '1950-01-01',
                          'planNumber': 'H4514-021-000'},
           'benefitsInformation': [
               {'code': '1', 'serviceTypeCodes': ['30'],
                'planCoverageDescription': 'UnitedHealthcare Dual Complete TX-S003 (HMO-POS D-SNP)'},
               {'code': 'B', 'serviceTypeCodes': ['98'], 'benefitAmount': '0',
                'timeQualifierCode': '7', 'coverageLevelCode': 'IND',
                'inPlanNetworkIndicatorCode': 'W'},
               {'code': 'A', 'serviceTypeCodes': ['98'], 'benefitPercent': '.20',
                'coverageLevelCode': 'IND', 'inPlanNetworkIndicatorCode': 'W'},
               {'code': 'C', 'serviceTypeCodes': ['30'], 'benefitAmount': '0',
                'timeQualifierCode': '29', 'coverageLevelCode': 'IND',
                'inPlanNetworkIndicatorCode': 'Y'},
               {'code': 'G', 'serviceTypeCodes': ['30'], 'benefitAmount': '8900.71',
                'timeQualifierCode': '29', 'coverageLevelCode': 'IND',
                'inPlanNetworkIndicatorCode': 'Y'}]}
    result = _result()
    result['benefits'] = commercial.normalize_stedi_271(raw, '98', False,
        office_visit=True, service_date='2026-11-09', place_of_service='11')
    result['benefits']['identity'] = result['identity']
    assert result['benefits']['service_date_matches'] is None
    assert result['benefits']['network_status'] == 'unknown'
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    assert uhc_dsnp_office_proxy.estimate(result, '2026-11-09')['patient_share_cents'] == 1761
    result.pop('checked_at')
    assert uhc_dsnp_office_proxy.estimate(result, '2026-11-09') is None


def test_selected_99204_uses_its_own_fee_and_never_prices_extra_procedure():
    result = _result()
    result['codes'] = [{'id': 'office', 'code': '99204', 'units': 1, 'modifiers': []}]
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    proxy = uhc_dsnp_office_proxy.estimate(result, '2026-11-09')
    assert proxy['reference_total_cents'] == 13066
    assert proxy['patient_share_cents'] == 2613
    assert proxy['cpt_selected'] is True
    result['codes'].append({'id': 'extra', 'code': '95886', 'units': 1, 'modifiers': []})
    assert uhc_dsnp_office_proxy.estimate(result, '2026-11-09') is None


def test_unselected_office_alternatives_remain_an_unselected_99203_scenario():
    result = _result()
    result['service_selection_status'] = 'preview'
    result['selected_ids'] = []
    result['codes'] = [{'id': code, 'code': code, 'units': 1, 'modifiers': [],
                        'source': 'conditional office E/M alternative'}
                       for code in ('99202', '99203', '99204', '99205')]
    result['available_codes'] = result['codes']
    rate_reference.refresh_saved_temporary_rates(result, 'synthetic-practice',
        {'service_date': '2026-11-09', 'place_of_service': '11'}, {}, None)
    proxy = uhc_dsnp_office_proxy.estimate(result, '2026-11-09')
    assert proxy['scenario_cpt'] == '99203'
    assert proxy['cpt_selected'] is False
    assert proxy['patient_share_cents'] == 1761


def test_scoped_clinic_99203_rate_takes_precedence_over_published_scenario():
    result = _result()
    result['service_selection_status'] = 'preview'
    result['selected_ids'] = []
    result['codes'] = [{'id': code, 'code': code, 'units': 1, 'modifiers': [],
                        'source': 'conditional office E/M alternative'}
                       for code in ('99202', '99203', '99204', '99205')]
    result['available_codes'] = result['codes']
    result['temporary_rate_reference'] = {'source': 'NMCT Clinical Library Blue Cross fee schedule',
        'complete': True, 'lines': [
            {'id': code, 'code': code, 'units': 1, 'modifiers': [],
             'unit_reference_cents': 10117 if code == '99203' else 9000,
             'source_reference': 'library:synthetic'} for code in ('99202', '99203', '99204', '99205')]}
    rate_reference._attach_uhc_dsnp_office_reference(result,
        {'service_date': '2026-11-09', 'place_of_service': '11'})
    proxy = uhc_dsnp_office_proxy.estimate(result, '2026-11-09')
    assert proxy['reference_total_cents'] == 10117
    assert proxy['patient_share_cents'] == 2023
