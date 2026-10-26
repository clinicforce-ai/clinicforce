"""Synthetic contract and payer rows; no patient or private document content."""
import pytest
from app.insurance import amount_explanation, collection_guidance, library_network_exclusions, library_pricing
from testing.synthetic_policy import ADVANCE_POLICY
from app.insurance.stedi_benefits.normalize import normalize_stedi_271
from app.insurance.ecw.ecw_fields import _tin
from app.api.routers import commercial_estimator  # Load route module before its workflow helpers.
from app.api.routers.commercial_flow import commercial_previsit_pricing
from app.insurance.pricing.visit import price_lines


def test_regional_exclusion_is_compiled_with_page_and_cannot_publish_same_document_participation():
    document = {'id': 'synthetic-contract', 'kind': 'contract', 'payer': 'UnitedHealthcare',
                'practice_id': 'synthetic-practice', 'effective_from': '2026-01-01',
                'effective_to': None, 'created_at': '2026-01-01T00:00:00Z'}
    extracted = {'sha256': 'synthetic-digest', 'pages': [{'number': 7, 'text':
        'Network Participation Appendix\nThe agreement excludes Regional PPO Medicare Advantage and Local PPO Medicare Advantage products. Separate agreements may exist.'}],
        'tables': [{'locator': 'Page 8', 'context': '', 'rows': [
            ['Participation', 'Billing NPI', 'Rendering NPI', 'Organization Name', 'TIN', 'Location ID', 'POS', 'Network', 'Plan', 'Effective From', 'Execution Status'],
            ['Participating', '1234567890', '1234567890', 'Synthetic Clinic', '123456789', 'office', '11', 'PPO', 'Regional PPO Medicare Advantage', '2026-01-01', 'Executed']]}]}
    connection = {'id': 'synthetic-payer', 'stedi_payer_id': '87726', 'display_name': 'UnitedHealthcare', 'aliases': []}
    compiled = library_pricing.compile_document(document, extracted, [connection])
    assert {row['product_kind'] for row in compiled['exclusions']} == {
        'regional_medicare_advantage_ppo', 'local_medicare_advantage_ppo'}
    assert compiled['exclusions'][0]['page'] == 7
    assert compiled['exclusions'][0]['document_id'] == 'synthetic-contract'
    assert compiled['networks'] == []
    included = {**extracted, 'pages': [{'number': 7, 'text':
        'This agreement does not exclude Regional PPO Medicare Advantage products.'}]}
    assert library_network_exclusions.compile_exclusions(document, included) == []


def test_legacy_uhc_upload_compiles_cross_page_network_appendix_exclusion():
    document = {'id': 'legacy-uhc', 'kind': 'contract', 'payer': '',
                'label': 'United Healthcare contract', 'created_at': '2026-01-01T00:00:00Z'}
    extracted = {'sha256': 'synthetic-digest', 'pages': [
        {'number': 4, 'text': 'Section 2. Notwithstanding the above section 1, this Agreement will not apply '
                              'to the Benefit Plan types described in the following line items:'},
        {'number': 5, 'text': 'Regional PPO Medicare Advantage Benefit Plans.\n\n'
                              'Local PPO Medicare Advantage Benefit Plans.\n\n'
                              'Section 3. Definitions: PPO Medicare Advantage Benefit Plans.'}]}
    rows = library_network_exclusions.compile_exclusions(document, extracted)
    assert {(row['product_kind'], row['page']) for row in rows} == {
        ('regional_medicare_advantage_ppo', 5), ('local_medicare_advantage_ppo', 5)}
    assert library_network_exclusions.is_uhc_contract({**document, 'label': 'Unrelated contract'}) is False


def test_exact_product_exclusion_does_not_establish_nonparticipation(monkeypatch):
    row = {'product_kind': 'regional_medicare_advantage_ppo',
           'decision': 'excluded_under_available_agreement', 'document_id': 'first-agreement',
           'document_sha256': 'digest', 'page': 3, 'effective_from': '2026-01-01', 'effective_to': None}
    document = {'id': 'first-agreement', 'payer': 'UnitedHealthcare', 'sha256': 'digest',
                'kind': 'contract', 'label': 'Synthetic general agreement',
                'extraction': {'compiled': {'exclusions': [row]}}}
    monkeypatch.setattr(library_network_exclusions.staff_store, 'list_library', lambda _: [document])
    monkeypatch.setattr(library_network_exclusions.staff_store, 'get_library', lambda *_: document)
    found = library_network_exclusions.applicable_exclusion(
        'synthetic-practice', 'UnitedHealthcare', ['RPPO-UHC Complete Care Support TX-1A'], '2026-10-14')
    assert found['decision'] == 'excluded_under_available_agreement'
    assert found['document_id'] == 'first-agreement'
    assert library_network_exclusions.applicable_exclusion(
        'synthetic-practice', 'UnitedHealthcare', ['Choice Plus'], '2026-10-14') is None
    assert library_network_exclusions.applicable_exclusion(
        'synthetic-practice', 'UnitedHealthcare', ['RPPO-UHC Complete Care Support TX-1A'], '2025-10-14') is None


def test_separate_executed_contract_can_establish_participation_after_exclusion(monkeypatch):
    api = commercial_previsit_pricing._api
    connection = {'id': 'uhc-route', 'stedi_payer_id': '87726',
                  'display_name': 'UnitedHealthcare', 'status': 'staff_guidance_enabled'}
    exclusion = {'decision': 'excluded_under_available_agreement',
                 'product_kind': 'regional_medicare_advantage_ppo',
                 'document_id': 'general-contract', 'page': 4, 'passage': 'Synthetic exclusion'}
    monkeypatch.setattr(api.commercial_store, 'list_connections', lambda _: [connection])
    monkeypatch.setattr(library_network_exclusions, 'applicable_exclusion', lambda *args: exclusion)
    monkeypatch.setattr(commercial_previsit_pricing, '_attach_temporary_uhc_rates', lambda *args: None)
    monkeypatch.setattr(api, '_previsit_practice_path', lambda *args: (
        connection, {'id': 'exact-product', 'plan_matcher': 'RPPO-UHC Complete Care Support TX-1A'},
        {'rules': {}}))
    monkeypatch.setattr(api, '_network_contract_evidence', lambda *args: {
        'document_id': 'separate-executed-contract', 'network_name': 'UHC Regional MA PPO'})
    monkeypatch.setattr(api, '_previsit_range', lambda *args, **kwargs: ({'lines': []}, []))
    result = {'payer': 'UnitedHealthcare', 'codes': [], 'issues': []}
    benefits = {'network_status': 'unknown'}
    inputs = {'service_date': '2026-10-14', 'rendering_provider': {'npi': '1234567890'},
              'place_of_service': '11'}
    source = {'plan': 'RPPO-UHC Complete Care Support TX-1A',
              'payer_plan_names': ['RPPO-UHC Complete Care Support TX-1A'],
              'billing_provider': {'npi': '1234567890'}, 'location_id': 'office'}
    commercial_previsit_pricing._attach_previsit_pricing(
        result, 'synthetic-practice', inputs, source, '87726', benefits)
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['decision'] == 'confirmed_participating'
    assert result['network_evidence']['document_id'] == 'separate-executed-contract'
    assert result['agreement_exclusion']['document_id'] == 'general-contract'

    monkeypatch.setattr(api, '_network_contract_evidence', lambda *args: {
        'document_id': 'general-contract', 'network_name': 'UHC broad PPO'})
    result, benefits = {'payer': 'UnitedHealthcare', 'codes': [], 'issues': []}, {'network_status': 'unknown'}
    commercial_previsit_pricing._attach_previsit_pricing(
        result, 'synthetic-practice', inputs, source, '87726', benefits)
    assert benefits['network_status'] == 'unknown'
    assert result['network_evidence']['decision'] == 'excluded_under_available_agreement'


def test_applied_product_alias_retains_administrator_approval():
    source = {'plan': 'Choice Plus', 'payer_plan_bound': True,
              'payer_plan_scope': {'plan_number': 'synthetic-plan'}}
    network = {'id': 'synthetic-network', 'plan_matcher': 'Choice PPO',
               'evidence_reference': 'library:synthetic-contract'}
    approval = {'products': ['Choice Plus'], 'network_id': 'synthetic-network',
                'network_reference': 'library:synthetic-contract',
                'plan_scope': source['payer_plan_scope'], 'valid_from': '2026-01-01',
                'valid_to': '2026-12-31', 'actor_id': 'synthetic-admin',
                'reference': 'synthetic-review', 'verified_at': '2026-01-02T12:00:00Z'}
    profile = {'rules': {'product_mappings': [approval], 'plan_product_names': ['Choice Plus'],
                        'library_source': {'document_id': 'synthetic-contract',
                                           'reference': 'library:synthetic-contract'}}}
    assert commercial_previsit_pricing._product_mapping_evidence(
        profile, network, source, '2026-10-14')['reference'] == 'synthetic-review'
    assert commercial_previsit_pricing._product_mapping_evidence(
        profile, network, source, '2027-01-01') is None
    assert commercial_previsit_pricing._product_mapping_evidence(
        profile, {**network, 'plan_matcher': 'Choice Plus'}, source, '2026-10-14') is None


def test_mixed_medical_and_vision_oop_rows_keep_candidate_and_quarantine_zero():
    annual = lambda amount, **extra: {'code': 'G', 'benefitAmount': amount,
        'timeQualifierCode': '23', 'serviceTypeCodes': ['30'], 'coverageLevel': 'INDIVIDUAL',
        'inPlanNetworkIndicatorCode': 'W', **extra}
    payload = {'plans': [{'name': 'RPPO-UHC Complete Care Support TX-1A'},
                         {'name': 'Spectera Vision Plan'}],
               'benefitsInformation': [
                   {'code': '1', 'serviceTypeCodes': ['30'], 'planCoverage': 'RPPO-UHC Complete Care Support TX-1A'},
                   annual('9250', insuranceType='PPO', messages=['COMBINED']),
                   annual('0', dates={'plan': {'end': '2026-12-31'}}),
                   {'code': 'B', 'benefitAmount': '5', 'serviceTypeCodes': ['98'],
                    'coverageLevel': 'INDIVIDUAL', 'inPlanNetworkIndicatorCode': 'W',
                    'planCoverage': 'Spectera Vision Plan'}]}
    result = normalize_stedi_271(payload, '98', False, office_visit=True)
    assert result['oop_annual_cents'] is None
    assert result['oop_medical_candidate_ids'] == ['benefitsInformation[1]']
    assert result['oop_quarantined_ids'] == ['benefitsInformation[2]']
    assert result['copay_cents'] is None
    rows = {row['id']: row for row in result['reported_options']}
    assert rows['benefitsInformation[1]']['amount_cents'] == 925000
    assert rows['benefitsInformation[1]']['plan_association'] == 'medical_candidate'
    assert rows['benefitsInformation[2]']['amount_cents'] == 0
    assert rows['benefitsInformation[2]']['plan_association'] == 'unassigned'
    assert rows['benefitsInformation[2]']['effective_dates']


def test_supplementary_plan_or_service_code_quarantines_unassigned_medical_oop():
    payload = {'plans': [{'name': 'Synthetic Medical PPO'}, {'name': 'Synthetic Vision Plan'}],
               'benefitsInformation': [
                   {'code': '1', 'serviceTypeCodes': ['30'], 'planCoverage': 'Synthetic Medical PPO'},
                   {'code': 'G', 'benefitAmount': '0', 'timeQualifierCode': '23',
                    'serviceTypeCodes': ['30'], 'coverageLevel': 'INDIVIDUAL',
                    'inPlanNetworkIndicatorCode': 'W'},
                   {'code': 'B', 'benefitAmount': '5', 'serviceTypeCodes': ['AL'],
                    'coverageLevel': 'INDIVIDUAL', 'inPlanNetworkIndicatorCode': 'W'}]}
    result = normalize_stedi_271(payload, '98', False, office_visit=True)
    assert result['oop_annual_cents'] is None
    assert result['oop_quarantined_ids'] == ['benefitsInformation[1]']
    assert result['copay_cents'] is None


def test_mixed_dental_status_does_not_hide_explicit_medical_office_oop():
    def row(service, **values):
        return {'service': {'system': 'STC', 'value': service}, **values}

    scope = 'DIAGNOSTIC MEDICAL PROCEDURE - PROFESSIONAL;OFFICE VISIT - PROFESSIONAL'
    payload = {'plans': [{'benefits': {
        'statuses': [row('30', status='ACTIVE_COVERAGE',
                         planCoverageDescription='Synthetic Dental Plan'),
                     row('30', status='ACTIVE_COVERAGE',
                         planCoverageDescription='Synthetic Medical PPO')],
        'deductible': [row('2', amount='1000', timePeriod='CALENDAR_YEAR',
                           coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                       row('2', amount='0', timePeriod='REMAINING',
                           coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'})],
        'outOfPocket': [row('30', amount='3500', timePeriod='CALENDAR_YEAR',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'},
                            messages=[scope]),
                        row('30', amount='0', timePeriod='REMAINING',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'},
                            messages=[scope])],
        'coPayment': [row('98', amount='50', timePeriod='VISIT',
                          coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'})]}}]}
    result = normalize_stedi_271(payload, '98', True, office_visit=True)
    assert result['deductible_remaining_cents'] is None
    assert result['oop_annual_cents'] == 350000
    assert result['oop_remaining_cents'] == 0
    assert result['oop_quarantined_ids'] == []
    payload['plans'][0]['benefits']['outOfPocket'][1]['messages'] = [scope + ';DENTAL CARE']
    result = normalize_stedi_271(payload, '98', True, office_visit=True)
    assert result['oop_remaining_cents'] is None


def test_explicit_medical_oop_annual_links_only_its_matching_remaining_row():
    annual = lambda amount, level, **extra: {'amount': amount,
        'timePeriod': 'CALENDAR_YEAR', 'service': {'system': 'STC', 'value': '30'},
        'coverageLevel': level, 'network': {'indicator': 'IN_NETWORK'}, **extra}
    remaining = lambda amount, level: {'amount': amount,
        'timePeriod': 'REMAINING', 'service': {'system': 'STC', 'value': '30'},
        'coverageLevel': level, 'network': {'indicator': 'IN_NETWORK'}}
    payload = {'plans': [{'benefits': {'statuses': [{'status': 'Active'}],
        'outOfPocket': [annual('4000', 'INDIVIDUAL', messages=['INT MED AND RX']),
                        remaining('3496.08', 'INDIVIDUAL'),
                        annual('8000', 'FAMILY', messages=['INT MED AND RX']),
                        remaining('7041.35', 'FAMILY')],
        'coPayment': [{'amount': '5', 'service': {'system': 'STC', 'value': 'AL'},
                       'coverageLevel': 'INDIVIDUAL',
                       'network': {'indicator': 'IN_NETWORK'}}]}}]}
    result = normalize_stedi_271(payload, '98', True, office_visit=True)
    assert result['oop_annual_cents'] == 400000
    assert result['oop_remaining_cents'] == 349608
    assert result['oop_quarantined_ids'] == []
    options = {row['id']: row for row in result['reported_options']}
    assert options['plans[0].benefits.outOfPocket[2]']['amount_cents'] == 800000
    assert options['plans[0].benefits.outOfPocket[3]']['amount_cents'] == 704135


def test_in_person_specialist_terms_ignore_remote_programs_and_bind_medical_limits():
    def row(service, **fields):
        return {'service': {'system': 'STC', 'value': service}, **fields}

    payload = {'plans': [{'benefits': {
        'statuses': [row('30', status='ACTIVE_COVERAGE',
                         planCoverageDescription='MED/SURG - PPO'),
                     row('98', status='ACTIVE_COVERAGE')],
        'coPayment': [row('98', amount='30', timePeriod='EPISODE',
                          coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'},
                          messages=['SPECIALIST']),
                      row('98', amount='30', timePeriod='EPISODE',
                          coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                      row('98', amount='10', timePeriod='EPISODE',
                          coverageLevel='INDIVIDUAL', network={'indicator': 'IN_AND_OUT_OF_NETWORK'},
                          messages=['SPECIALIST E-VISIT']),
                      row('98', amount='0', coverageLevel='INDIVIDUAL',
                          network={'indicator': 'IN_NETWORK'},
                          messages=['SPECIALIST HINGE HEALTH PROGRAM'])],
        'coInsurance': [row('98', percent='0', coverageLevel='INDIVIDUAL',
                            network={'indicator': 'IN_NETWORK'}, messages=['SPECIALIST']),
                        row('98', percent='0', coverageLevel='INDIVIDUAL',
                            network={'indicator': 'IN_NETWORK'}),
                        row('98', percent='0', coverageLevel='INDIVIDUAL',
                            network={'indicator': 'IN_NETWORK'},
                            messages=['SPECIALIST DIABETES MANAGEMENT PROGRAM OMADA HEALTH ONLY'])],
        'outOfPocket': [row('30', amount='4500', timePeriod='CALENDAR_YEAR',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                        row('30', amount='1250.01', timePeriod='REMAINING',
                            coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                        row('30', amount='9000', timePeriod='CALENDAR_YEAR',
                            coverageLevel='FAMILY', network={'indicator': 'IN_NETWORK'}),
                        row('30', amount='5250.01', timePeriod='REMAINING',
                            coverageLevel='FAMILY', network={'indicator': 'IN_NETWORK'})],
        'deductible': [row('30', amount='1200', timePeriod='CALENDAR_YEAR',
                           coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                       row('30', amount='0', timePeriod='REMAINING',
                           coverageLevel='INDIVIDUAL', network={'indicator': 'IN_NETWORK'}),
                       row('30', amount='2400', timePeriod='CALENDAR_YEAR',
                           coverageLevel='FAMILY', network={'indicator': 'IN_NETWORK'}),
                       row('30', amount='1200', timePeriod='REMAINING',
                           coverageLevel='FAMILY', network={'indicator': 'IN_NETWORK'})],
        'limitations': [row('30', amount='5000', timePeriod='LIFETIME',
                            messages=['TRAVEL EXPENSES FOR COVERED SERVICES OVER 50 MILES'])],
        'nonCovered': [row('AL', network={'indicator': 'IN_AND_OUT_OF_NETWORK'})],
    }}]}
    result = normalize_stedi_271(payload, '98', False, office_visit=True,
                                  place_of_service='11', service_date='2026-10-26')
    assert result['active'] is True
    assert result['network_status'] == 'unknown'
    assert result['copay_cents'] == 3000
    assert result['coinsurance_percent'] == '0'
    assert result['oop_annual_cents'] == 450000
    assert result['oop_remaining_cents'] == 125001
    assert result['deductible_annual_cents'] == 120000
    assert result['deductible_remaining_cents'] == 0
    options = {option['id']: option for option in result['reported_options']}
    for index, (amount, level, period) in enumerate([
        (450000, 'INDIVIDUAL', 'CALENDAR_YEAR'), (125001, 'INDIVIDUAL', 'REMAINING'),
        (900000, 'FAMILY', 'CALENDAR_YEAR'), (525001, 'FAMILY', 'REMAINING'),
    ]):
        option = options[f'plans[0].benefits.outOfPocket[{index}]']
        assert (option['amount_cents'], option['coverage_level'], option['time_period'],
                option['network'], option['plan_association']) == (
                    amount, level, period, 'IN_NETWORK', 'medical')
    for index, (amount, level, period) in enumerate([
        (120000, 'INDIVIDUAL', 'CALENDAR_YEAR'), (0, 'INDIVIDUAL', 'REMAINING'),
        (240000, 'FAMILY', 'CALENDAR_YEAR'), (120000, 'FAMILY', 'REMAINING'),
    ]):
        option = options[f'plans[0].benefits.deductible[{index}]']
        assert (option['amount_cents'], option['coverage_level'], option['time_period'],
                option['network'], option['plan_association']) == (
                    amount, level, period, 'IN_NETWORK', 'medical')
    assert all('travel expenses' not in reason.lower() for reason in result['review_reasons'])
    priced = price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [], result,
                         {'place_of_service': '11', 'office_service_types': ['98', 'BY', '1']},
                         payload=payload, service_date='2026-10-26')
    assert priced['lines'][0]['benefits']['copay_cents'] == 3000
    assert priced['lines'][0]['benefits']['oop_remaining_cents'] == 125001
    assert priced['lines'][0]['allowed_cents'] is None
    summary = commercial_previsit_pricing._previsit_benefits(
        payload, {'active_coverages': 1}, '2026-10-26', ['30', '98'],
        inputs={'lines': [{'code': '99203', 'service_status': 'scheduled', 'units': 1}],
                'place_of_service': '11', 'service_date': '2026-10-26'}, source={})
    assert summary['copay_cents'] == 3000
    payload['plans'][0]['benefits']['coPayment'][1]['messages'] = ['FIRST VISIT ONLY']
    conditional = normalize_stedi_271(payload, '98', False, office_visit=True,
                                       place_of_service='11', service_date='2026-10-26')
    assert conditional['copay_cents'] is None
    payload['plans'][0]['benefits']['statuses'].append(
        row('30', status='ACTIVE_COVERAGE', planCoverageDescription='Synthetic Vision Plan'))
    mixed = normalize_stedi_271(payload, '98', False, office_visit=True,
                                place_of_service='11', service_date='2026-10-26')
    assert mixed['oop_remaining_cents'] is None
    payload['plans'][0]['benefits']['statuses'].pop()
    payload['plans'][0]['benefits']['outOfPocket'].append(row(
        '30', amount='150', timePeriod='REMAINING', coverageLevel='INDIVIDUAL',
        network={'indicator': 'IN_NETWORK'}, planCoverageDescription='Synthetic Vision Plan'))
    conflicted = normalize_stedi_271(payload, '98', False, office_visit=True,
                                     place_of_service='11', service_date='2026-10-26')
    assert conflicted['oop_remaining_cents'] is None
    payload['plans'][0]['benefits']['outOfPocket'].pop()
    payload['plans'][0]['benefits']['statuses'][0]['status'] = 'INACTIVE_COVERAGE'
    inactive = normalize_stedi_271(payload, '98', False, office_visit=True,
                                   place_of_service='11', service_date='2026-10-26')
    assert inactive['oop_remaining_cents'] is None


def test_ecw_billing_tin_requires_explicit_identifier_label():
    assert _tin({'identifier': [{'system': 'http://hl7.org/fhir/sid/us-ein',
                                 'value': '12-3456789'}]}) == '123456789'
    assert _tin({'identifier': [{'system': 'urn:synthetic:account', 'value': '123456789'}]}) is None
    assert _tin({'identifier': [{'system': 'urn:synthetic:tin', 'value': '123456789'},
                                {'system': 'urn:synthetic:tin', 'value': '987654321'}]}) is None


def test_sourced_clinic_nonparticipation_selects_out_of_network_benefits():
    payload = {'benefitsInformation': [
        {'code': '1', 'serviceTypeCodes': ['30']},
        {'code': 'B', 'benefitAmount': '10', 'serviceTypeCodes': ['98'],
         'inPlanNetworkIndicatorCode': 'Y'},
        {'code': 'B', 'benefitAmount': '40', 'serviceTypeCodes': ['98'],
         'inPlanNetworkIndicatorCode': 'N'},
        {'code': 'C', 'benefitAmount': '100', 'timeQualifierCode': '29',
         'coverageLevel': 'INDIVIDUAL', 'serviceTypeCodes': ['30'],
         'inPlanNetworkIndicatorCode': 'W'}]}
    selected = normalize_stedi_271(payload, '98', False,
        provider_out_of_network_verified=True, office_visit=True, procedure_code='99203')
    assert selected['network_status'] == 'out_of_network'
    assert selected['network_source'] == 'clinic_nonparticipation_rule'
    assert selected['copay_cents'] == 4000
    assert selected['deductible_remaining_cents'] == 10000
    assert 'benefitsInformation[1]' not in selected['selected_benefit_ids']
    lines = price_lines([{'id': 'office', 'code': '99203', 'units': 1}], [],
        {'network_status': 'out_of_network', 'network_source': 'clinic_nonparticipation_rule',
         'identity': {'status': 'matched'}},
        {'place_of_service': '11', 'provider_npi': '1234567890'},
        payload=payload, service_date='2026-10-14')
    assert lines['lines'][0]['benefits']['copay_cents'] == 4000
    assert lines['lines'][0]['allowed_cents'] is None


def test_exclusion_is_decisive_clinician_reason():
    result = {'payer': 'UnitedHealthcare', 'payment_mode': 'insurance',
              'plan_details': {'stedi': {'names': ['RPPO-UHC Complete Care Support TX-1A']}},
              'benefits': {'active': True, 'network_status': 'unknown',
                           'service_date_matches': True},
              'identity': {'status': 'matched', 'match_version': 2, 'binding': 'synthetic'},
              'network_evidence': {'decision': 'excluded_under_available_agreement',
                                   'product_kind': 'regional_medicare_advantage_ppo'},
              'price_range': {'mode': 'alternatives', 'lines': []}}
    explanation = amount_explanation.build(result, {'amount_cents': None})
    assert explanation['reason_code'] == 'clinic_agreement_exclusion'
    assert 'RPPO-UHC Complete Care Support TX-1A is a UHC Regional Medicare Advantage PPO product and is excluded' in explanation['sentence']
    assert 'separate' in explanation['sentence']


def test_ma_out_of_network_explanation_does_not_suggest_balance_billing():
    result = {'payment_mode': 'insurance',
              'plan_details': {'stedi': {'names': ['RPPO-UHC Complete Care Support TX-1A']}},
              'benefits': {'network_status': 'out_of_network',
                           'network_source': 'payer_participation_message'},
              'price_range': {'complete': True, 'mode': 'services', 'lines': [
                  {'code': '99203', 'calculation': {'patient_cents': 4000,
                   'copay_cents': 4000, 'deductible_cents': 0, 'coinsurance_cents': 0,
                   'allowed_cap_adjustment_cents': 0, 'oop_adjustment_cents': 0}}]}}
    explanation = amount_explanation.build(result, {'amount_cents': 4000,
        'basis': 'anticipated_services'})
    assert 'extra doctor charges' not in explanation['sentence']


@pytest.mark.parametrize('name', [
    'RPPO-UHC Complete Care Support TX-1A',
    'UnitedHealthcare Medicare Advantage Group PPO',
    'UnitedHealthcare Medicare Advantage HMO',
    'UnitedHealthcare Medicare Advantage D-SNP',
])
def test_ma_advance_requires_patient_bound_qmb_clearance(name):
    result = {'payment_mode': 'insurance',
              'collection_policy': ADVANCE_POLICY,
              'plan_details': {'stedi': {'names': [name]}},
              'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'in_network', 'network_source': 'clinic_library_contract'},
              'identity': {'status': 'matched', 'match_version': 2, 'binding': 'synthetic-binding'},
              'price_range': {'complete': True, 'mode': 'services', 'patient_min_cents': 5000,
                              'patient_max_cents': 5000, 'lines': [{'id': 'office', 'code': '99203',
                              'calculation': {'patient_cents': 5000}, 'benefits': {}}]}}
    assert collection_guidance.build(result)['amount_cents'] is None
    result['billing_protection'] = {'status': 'not_qmb', 'reference': 'synthetic payer statement',
                                    'verified_at': '2026-10-14T12:00:00Z',
                                    'identity_binding': 'another-member'}
    assert collection_guidance.build(result)['amount_cents'] is None
    result['billing_protection']['identity_binding'] = 'synthetic-binding'
    assert collection_guidance.build(result)['amount_cents'] == 5000
    result['benefits']['qmb_status'] = 'protected'
    protected = collection_guidance.build(result)
    assert protected['amount_cents'] == 0
    assert protected['basis'] == 'qmb_protection'
