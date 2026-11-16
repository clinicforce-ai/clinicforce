"""Visit-linked EHR instructions are evidence, never patient-specific defaults."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.insurance import ecw_backend, ecw_billing, collection_guidance
from app.api.routers import commercial_estimator as api
from testing.synthetic_policy import ADVANCE_POLICY


def fields(text):
    return ecw_backend.appointment_fields({'resourceType': 'Encounter', 'id': 'visit',
        'meta': {'lastUpdated': (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()},
        'extension': [{'url': 'https://ehr.test/BillingNotes', 'valueString': text}]})


def result(text):
    return {'billing_note': ecw_billing.collection_note(fields(text)),
        'benefits': {}, 'eligibility_failure': {'kind': 'provider_enrollment'},
        'price_range': {'lines': [{'code': '99203', 'units': 1, 'benefits': {}}], 'mode': 'services'}}


@pytest.mark.parametrize('text,amount', [
    ('No Need To Take any money From Patient. Secondary Will Takecare.', 0),
    ('no need to take any money from patient', 0),
    ('ACTIVE, DED MET, NO COINS, NO COPAY', 0),
    ('Pt Is Active, $47.35 Copay, Ded Has Been Met', 4735),
    ('Patient is active; $83 Copay; deductible met', 8300)])
def test_explicit_note_fallback_retains_evidence_without_asserting_eligibility(text, amount):
    item = result(text)
    guidance = collection_guidance.build(item)
    assert guidance['amount_cents'] == amount
    assert guidance['basis'] == 'ecw_billing_note'
    assert guidance['billing_note']['text'] == text
    assert guidance['billing_note']['source'].startswith('Encounter/visit.')
    assert item['benefits'] == {} and item['eligibility_failure']['kind'] == 'provider_enrollment'
    assert guidance['services'][0]['patient_cents'] is None


@pytest.mark.parametrize('text', [
    'If secondary pays, do not collect', 'Patient says no need to take money from patient',
    'Do not collect $50', 'Inactive, $50 copay, ded met', 'Active, $50 copay, deductible not met',
    'Active, $50 copay, ded met; $80 copay', 'Active, no copay, ded met',
    'Active, $50 copay, ded met; first ten visits only', 'Active, $999999999999 copay, ded met'])
def test_ambiguous_conditional_or_invalid_notes_do_not_supply_amounts(text):
    assert ecw_billing.collection_note(fields(text)) is None


@pytest.mark.parametrize('change', ['patient_comment', 'missing_time', 'future_time', 'foreign_source', 'conflict'])
def test_only_dated_billing_fields_from_the_matched_visit_are_used(change):
    value = fields('Active, $37 copay, ded met')
    if change == 'patient_comment': value[0]['label'] = 'Patient instructions'
    if change == 'missing_time': value[0].pop('updated_at')
    if change == 'future_time': value[0]['updated_at'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    if change == 'foreign_source': value[0]['source'] = 'Patient/p.note'
    if change == 'conflict': value += fields('Active, $62 copay, ded met')
    assert ecw_billing.collection_note(value) is None


def test_no_collection_directive_handles_secondary_but_copay_does_not_assume_coordination():
    value = result('No need to collect money from the patient')
    value['benefits']['has_secondary'] = True
    assert collection_guidance.build(value)['amount_cents'] == 0
    value['billing_note'] = ecw_billing.collection_note(fields('Active, $47 copay, ded met'))
    assert collection_guidance.build(value)['amount_cents'] is None


def test_note_copay_does_not_override_stedi_or_cover_extra_services():
    value = result('Active, $47 copay, ded met')
    value['collection_policy'] = ADVANCE_POLICY
    value['benefits'] = {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}
    value['arrival_collection'] = {'amount_cents': 7200}
    assert collection_guidance.build(value)['amount_cents'] == 7200
    value.pop('arrival_collection')
    value['price_range']['lines'][0]['benefits']['coinsurance_percent'] = '20'
    assert collection_guidance.build(value)['amount_cents'] is None
    value['price_range']['lines'][0]['benefits'] = {}
    extra = deepcopy(value)
    extra['price_range']['lines'].append({'code': '95910', 'units': 1, 'benefits': {}})
    assert collection_guidance.build(extra)['amount_cents'] is None
    assert collection_guidance.build(value, {'payments': {'reference': 'receipt', 'amount_cents': 1200}})['amount_cents'] == 3500


def test_visit_note_copay_cannot_replace_unknown_network_for_active_payer_benefits():
    value = result('Active, $47 copay, ded met')
    value['benefits'] = {'active': True, 'network_status': 'unknown', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}
    assert collection_guidance.build(value)['amount_cents'] is None
    value['benefits']['network_status'] = 'in_network'
    value['benefits']['network_source'] = 'clinic_library_contract'
    assert collection_guidance.build(value)['amount_cents'] == 4700
    value['recovery'] = {'reason_code': 'multiple_active_policies'}
    assert collection_guidance.build(value)['amount_cents'] is None


@pytest.mark.parametrize('label,text', [
    ('Billing Notes', 'Self Pay'),
    ('Billing Notes', 'Patient stated they will self pay'),
    ('General comments', 'Patient will self pay'),
    ('Appointment note', 'Self Pay'),
    ('Appointment note', 'Pt elected to self-pay'),
])
def test_ehr_self_pay_note_uses_the_active_schedule_and_explicit_staff_selection_wins(label, text):
    inputs = {'service_date': '2026-10-07', 'visit_type': 'new', 'lines': []}
    note_fields = fields(text)
    note_fields[0]['label'] = label
    source = {'active_coverages': 0, 'appointment_fields': note_fields}
    schedule = {'id': 'schedule', 'filename': 'approved.csv', 'effective_from': '2026-01-01',
        'rows': [{'code': str(c), 'description': 'Office', 'cash_cents': 18723} for c in range(99202, 99206)]}
    case = {}
    value = {'benefits': {}, 'issues': []}
    with patch.object(api.commercial_store, 'list_connections', return_value=[]), patch.object(api.practice_prices, 'current', return_value=schedule), patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline test')):
        api._finish_previsit(value, case, inputs, source, None, {'practice_id': 'clinic'}, '')
        assert value['payment_mode'] == 'cash' and value['cash_quote']['min_cents'] == 18723
        case['visit_review']['payment_mode'] = 'insurance'
        api._finish_previsit(value, case, inputs, source, None, {'practice_id': 'clinic'}, '')
        assert value['payment_mode'] == 'insurance'


def test_four_area_cash_booking_uses_full_study_package_without_guessing_cpts():
    inputs = {'service_date': '2026-11-16', 'visit_type': 'established',
              'reason': 'Reason: EMG 4 Areas', 'appointment_reason': 'EMG 4 Areas', 'lines': []}
    source = {'active_coverages': 0, 'coverage_retrieval': {'status': 'empty'},
              'appointment_notes_status': 'not_exposed', 'appointment_fields': []}
    schedule = {'id': 'schedule', 'filename': 'synthetic-cash.docx', 'effective_from': '2026-09-04',
        'rows': [{'code': 'named:full-study', 'cash_cents': 130000, 'price_kind': 'named_package',
                  'label': 'Comprehensive Full Study - Arms & Legs',
                  'likely_cpt_examples': '95912 or 95913 + appropriate EMG units',
                  'description': 'Comprehensive Full Study - Arms & Legs'}]}
    case = {}
    result = {'benefits': {}, 'issues': []}
    with patch.object(api.commercial_store, 'list_connections', return_value=[]), \
         patch.object(api.practice_prices, 'current', return_value=schedule), \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline test')):
        api._finish_previsit(result, case, inputs, source, None, {'practice_id': 'clinic'}, '')
        case['visit_review']['cash_selection'] = ''
        cleared = {'benefits': {}, 'issues': []}
        api._finish_previsit(cleared, case, inputs, source, None, {'practice_id': 'clinic'}, '')
        case['visit_review']['payment_mode'] = 'insurance'
        insured = {'benefits': {}, 'issues': []}
        api._finish_previsit(insured, case, inputs, source, None, {'practice_id': 'clinic'}, '')
    assert result['payment_mode'] == 'cash'
    assert result['cash_quote']['selection'] == 'named:full-study'
    assert result['cash_quote']['selection_source'] == 'appointment_scope'
    assert result['cash_quote']['min_cents'] == 130000
    assert result['cash_quote']['likely_cpt_examples'] == '95912 or 95913 + appropriate EMG units'
    assert result['collection_guidance']['amount_cents'] == 130000
    assert 'confirm the planned scope' in result['collection_guidance']['reason']
    assert result['collection_guidance']['services'] == []
    assert result['selected_ids'] == []
    assert cleared['cash_quote']['selection'] == '' and cleared['collection_guidance']['amount_cents'] is None
    assert insured['payment_mode'] == 'insurance' and insured['cash_quote']['selection'] == ''


@pytest.mark.parametrize('change', [
    {'coverage_retrieval': {'status': 'unavailable'}},
    {'coverage_retrieval': {'status': 'returned'}},
    {'active_coverages': 1},
    {'payer': 'Example Payer'},
    {'insurance_candidates': [{'payer': 'Example Payer'}]},
    {'saved_eligibility': {'status': 'verified_active'}},
])
def test_incomplete_or_other_insurance_evidence_does_not_prove_empty_coverage(change):
    source = {'coverage_retrieval': {'status': 'empty'}, 'active_coverages': 0, **change}
    assert not ecw_billing.cash_without_coverage(source)


@pytest.mark.parametrize('text', [
    'Patient will self pay if insurance denies',
    'Patient said they may self pay',
    'Patient will not self pay',
    'Patient self paid at the last visit',
])
def test_conditional_or_historical_appointment_comments_do_not_select_cash(text):
    value = fields(text)
    value[0]['label'] = 'General comments'
    assert ecw_billing.collection_note(value) is None


def test_conflicting_appointment_self_pay_comments_do_not_select_cash():
    choice = fields('Patient will self pay')
    choice[0]['label'] = 'Appointment note'
    negated = fields('Patient will not self pay')
    negated[0]['label'] = 'General comments'
    assert ecw_billing.collection_note([*choice, *negated]) is None


def test_staff_payment_preference_in_ecw_general_notes_selects_cash_without_patient_comments():
    note = fields('Patient payment preference: Self Pay\nPatient entered comments on healow : I have insurance')
    note[0]['label'] = 'General Notes'
    assert ecw_billing.collection_note(note)['kind'] == 'self_pay'
    note[0]['text'] = 'Patient entered comments on healow : Patient payment preference: Self Pay'
    assert ecw_billing.collection_note(note) is None
    note[0]['text'] = 'Patient payment preference: Self Pay\nPatient payment preference: Insurance'
    assert ecw_billing.collection_note(note) is None


@pytest.mark.parametrize('self_pay_source', ['registration', 'billing_note'])
def test_active_uhc_benefits_do_not_automatically_follow_ecw_self_pay(self_pay_source):
    inputs = {'service_date': '2026-10-07', 'visit_type': 'new', 'lines': []}
    source = {'active_coverages': 1, 'self_pay_recorded': self_pay_source == 'registration',
              'appointment_fields': fields('Self Pay') if self_pay_source == 'billing_note' else []}
    route = {'id': 'clinic-route', 'stedi_payer_id': 'uhc-route', 'status': 'draft',
             'display_name': 'UnitedHealthcare Group Medicare Advantage (PPO)', 'rules': {}}
    case = {}
    value = {'payer': 'AARP Regional PPO', 'benefits': {'active': True,
             'service_date_matches': True}, 'issues': []}
    with patch.object(api.commercial_store, 'list_connections', return_value=[route]), \
         patch.object(api.practice_prices, 'current', return_value=None), \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline test')):
        api._finish_previsit(value, case, inputs, source, None, {'practice_id': 'clinic'}, 'uhc-route')
        assert value['payment_mode'] == 'insurance'
        case['visit_review']['payment_mode'] = 'cash'
        api._finish_previsit(value, case, inputs, source, None, {'practice_id': 'clinic'}, 'uhc-route')
        assert value['payment_mode'] == 'cash'


@pytest.mark.parametrize('payer_terms', [{'deductible_remaining_cents': 10000}, {'active': False}, {'copay_cents': 6000}])
def test_conflicting_payer_terms_prevent_a_note_copay_fallback(payer_terms):
    value = result('Active, $47 copay, ded met')
    value['price_range']['lines'][0]['benefits'] = payer_terms
    assert collection_guidance.build(value)['amount_cents'] is None


@pytest.mark.parametrize('line_change', [
    {'service_date_matches': False}, {'network_conflict': True},
    {'has_secondary': True}, {'historical_only': True},
    {'identity': {'status': 'review'}}, {'network_status': 'out_of_network'},
])
def test_visit_note_copay_cannot_bypass_selected_cpt_conflict(line_change):
    value = result('Active, $47 copay, ded met')
    value['benefits'] = {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract',
                         'identity': {'status': 'matched', 'binding': 'synthetic-current-binding',
                                      'match_version': 2}}
    value['price_range']['lines'][0]['benefits'] = line_change
    assert collection_guidance.build(value)['amount_cents'] is None


@pytest.mark.parametrize('field', ['coverage_status_conflict', 'coverage_dates_invalid'])
def test_visit_note_copay_cannot_bypass_plan_coverage_conflict(field):
    value = result('Active, $47 copay, ded met')
    value['benefits'] = {'active': True, 'network_status': 'in_network',
                         'network_source': 'clinic_library_contract',
                         'identity': {'status': 'matched', 'binding': 'synthetic-current-binding',
                                      'match_version': 2}, field: True}
    assert collection_guidance.build(value)['amount_cents'] is None
    value['billing_note'] = ecw_billing.collection_note(fields('No need to collect money from the patient'))
    assert collection_guidance.build(value)['amount_cents'] == 0
