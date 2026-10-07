"""Visit-linked EHR instructions are evidence, never patient-specific defaults."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from app.insurance import ecw_backend, ecw_billing, collection_guidance
from app.api.routers import commercial_estimator as api


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
    value['benefits'] = {'active': True, 'network_status': 'in_network'}
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


def test_ehr_self_pay_note_uses_the_active_schedule_and_explicit_staff_selection_wins():
    inputs = {'service_date': '2026-10-07', 'visit_type': 'new', 'lines': []}
    source = {'active_coverages': 0, 'appointment_fields': fields('Self Pay')}
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


@pytest.mark.parametrize('payer_terms', [{'deductible_remaining_cents': 10000}, {'active': False}, {'copay_cents': 6000}])
def test_conflicting_payer_terms_prevent_a_note_copay_fallback(payer_terms):
    value = result('Active, $47 copay, ded met')
    value['price_range']['lines'][0]['benefits'] = payer_terms
    assert collection_guidance.build(value)['amount_cents'] is None
