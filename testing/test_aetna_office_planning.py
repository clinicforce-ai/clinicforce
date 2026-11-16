"""Prior-date office figures must remain conditional and use scoped payer rows."""
from unittest.mock import patch

from app.insurance import aetna_office_planning
from app.insurance.requirements.published import _published_rule


def _case():
    result = {
        'payer': 'Aetna', 'checked_at': '2026-11-14T12:00:00Z',
        'reference_only': True, 'payment_mode': 'insurance',
        'plan_details': {'stedi': {'names': ['Aetna Choice POS II']}},
        'identity': {'match_version': 2, 'status': 'matched', 'binding': 'synthetic-person'},
        'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}],
        'reference_benefits': {
            'active': True, 'service_date_matches': False,
            'returned_service_dates': {'start': '2026-11-14'},
            'deductible_remaining_cents': 454704, 'oop_remaining_cents': 754704,
            'reported_options': [
                {'kind': 'copay', 'service_type_code': '98', 'network': 'IN_NETWORK',
                 'amount_cents': 0, 'payer_note': 'Specialist Visit or Evaluation'},
                {'kind': 'coinsurance', 'service_type_code': '98', 'network': 'IN_NETWORK',
                 'percent': '30.0', 'payer_note': 'Specialist Visit or Evaluation'},
                {'kind': 'coinsurance', 'service_type_code': '98', 'network': 'IN_NETWORK',
                 'percent': '0.0', 'payer_note': 'Telemedicine Specialist Visit'},
            ],
        },
    }
    inputs = {'service_date': '2026-11-16', 'place_of_service': '11',
              'rendering_provider': {'npi': '1234567890'}}
    return result, inputs


def test_prior_date_aetna_office_has_conditional_share_but_no_collectable_amount():
    result, inputs = _case()
    rate = {'allowed_cents': 9325, 'source_reference': 'synthetic approved clinic schedule'}
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates', return_value=[rate]) as lookup:
        planning = aetna_office_planning.estimate(result, inputs)
    assert lookup.call_args.args[:3] == ('commercial', '2026-11-16', result['codes'])
    assert planning['deductible_applies_cents'] == 9325
    assert planning['deductible_waived_cents'] == 2798
    assert planning['coinsurance_percent'] == '30.0'
    assert planning['collectable'] is False


def test_conflicting_office_terms_or_unbound_patient_withhold_aetna_figure():
    result, inputs = _case()
    result['reference_benefits']['reported_options'].append({
        'kind': 'coinsurance', 'service_type_code': '98', 'network': 'IN_NETWORK',
        'percent': '20', 'payer_note': 'Specialist Visit or Evaluation'})
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates') as lookup:
        assert aetna_office_planning.estimate(result, inputs) is None
    lookup.assert_not_called()
    result['reference_benefits']['reported_options'].pop()
    result['identity']['status'] = 'review'
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates') as lookup:
        assert aetna_office_planning.estimate(result, inputs) is None
    lookup.assert_not_called()


def test_visit_date_response_does_not_use_prior_date_planning_path():
    result, inputs = _case()
    result['reference_benefits']['service_date_matches'] = True
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates') as lookup:
        assert aetna_office_planning.estimate(result, inputs) is None
    lookup.assert_not_called()


def test_saved_payer_response_without_attached_reference_uses_same_rule():
    result, inputs = _case()
    result['benefits'] = result.pop('reference_benefits')
    result.pop('reference_only')
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 9325}]):
        assert aetna_office_planning.estimate(result, inputs)['deductible_applies_cents'] == 9325


def test_old_or_explicitly_out_of_network_response_cannot_set_office_planning():
    result, inputs = _case()
    result['reference_benefits']['network_status'] = 'out_of_network'
    assert aetna_office_planning.estimate(result, inputs) is None
    result['reference_benefits']['network_status'] = 'unknown'
    result['reference_benefits']['returned_service_dates']['start'] = '2026-11-01'
    assert aetna_office_planning.estimate(result, inputs) is None


def test_explicit_claim_deferral_is_not_replaced_by_office_planning():
    result, inputs = _case()
    result['collection_guidance'] = {
        'amount_cents': 0,
        'collection_rule': {'mode': 'aetna_choice_family_oop_deferral'},
    }
    assert aetna_office_planning.estimate(result, inputs) is None


def test_choice_pos_ii_published_office_requirements_keep_member_check():
    result, inputs = _case()
    result['benefits'] = result['reference_benefits']
    result['benefit_source'] = 'Stedi'
    context = {'service_date': inputs['service_date'], 'payer_names': {'aetna'},
               'pos': '11', 'member_id': 'synthetic-member'}
    for kind in ('referral', 'authorization'):
        decision = _published_rule(kind, result, context, ['Aetna Choice POS II'], {'99203'})
        assert decision['status'] == 'Not required'
        assert decision['member_specific_check'] is True
        assert decision['sources'][0]['url'].startswith('https://www.aetna.com/')


def test_prior_day_aetna_medicare_copay_is_patient_share_not_plan_share():
    result, inputs = _case()
    result['plan_details']['stedi'] = {
        'names': ['Aetna Medicare Value Plus (PPO)'],
        'group_number': '000003-TX', 'plan_number': '000003-TX000083'}
    result['reference_benefits'].update(
        returned_service_dates={'start': '2026-11-15'},
        network_status='in_network', deductible_remaining_cents=0,
        oop_remaining_cents=530265)
    result['codes'][0]['code'] = '99213'
    result['reference_benefits']['reported_options'] = [
        {'kind': 'copay', 'service_type_code': '98', 'network': 'IN_NETWORK',
         'amount_cents': 5000, 'payer_note': 'Specialist Visit or Evaluation'},
        {'kind': 'coinsurance', 'service_type_code': '98', 'network': 'IN_NETWORK',
         'percent': '0', 'payer_note': 'Specialist Visit or Evaluation'}]
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 9633, 'source_reference': 'synthetic clinic fee'}]) as lookup:
        planning = aetna_office_planning.estimate(result, inputs)
    assert lookup.call_args.args[:3] == ('advantage', '2026-11-16', result['codes'])
    assert (planning['allowed_cents'], planning['copay_cents'], planning['plan_share_cents']) == (9633, 5000, 4633)
    assert planning['deductible_applies_cents'] == 5000
    assert planning['collectable'] is False
    result['reference_benefits']['network_status'] = 'unknown'
    with patch.object(aetna_office_planning.aetna_fees, 'matching_rates',
                      return_value=[{'allowed_cents': 9633}]):
        assert aetna_office_planning.estimate(result, inputs)['plan_share_cents'] == 4633
    result['reference_benefits']['network_status'] = 'out_of_network'
    assert aetna_office_planning.estimate(result, inputs) is None
    result['reference_benefits']['network_status'] = 'in_network'
    result['reference_benefits']['qmb_status'] = 'protected'
    assert aetna_office_planning.estimate(result, inputs) is None
