from app.insurance import commercial_store, contract_requirements
from app.insurance.requirements.stored import decide


def _visit():
    row = {'id': 'published-choice-plus-99213', 'payer_key': 'unitedhealthcare',
           'product_key': 'choiceplus', 'cpt': '99213', 'network_tier': 'in_network',
           'place_of_service': '11', 'effective_from': '2026-01-01',
           'effective_to': '2026-12-31', 'referral_required': False,
           'prior_authorization_required': None, 'source_label': '2026 guide',
           'source_url': 'https://example.test/guide'}
    result = {'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'in_network'},
              'identity': {'match_version': 2, 'status': 'matched', 'binding': 'synthetic'},
              'benefit_source': 'Stedi'}
    context = {'service_date': '2026-10-27', 'member_id': 'synthetic', 'pos': '11',
               'payer_names': ['UnitedHealthcare'], 'stored_rules': [row]}
    return result, context


def test_scoped_requirement_never_turns_unknown_auth_into_no():
    result, context = _visit()
    assert decide('referral', result, context, {'99213'}, ['UnitedHealthcare Choice Plus'])['status'] == 'Not required'
    assert decide('authorization', result, context, {'99213'}, ['UnitedHealthcare Choice Plus']) is None
    assert decide('referral', result, context, {'99213'}, ['UnitedHealthcare Navigate']) is None
    assert decide('referral', result, {**context, 'member_id': None}, {'99213'}, ['UnitedHealthcare Choice Plus']) is None
    assert decide('referral', {**result, 'benefits': {**result['benefits'], 'active': None}},
                  context, {'99213'}, ['UnitedHealthcare Choice Plus']) is None


def test_stored_rule_reaches_visit_cards_without_clearing_authorization():
    result, context = _visit()
    result['codes'] = [{'code': '99213', 'units': 1}]
    context['plans'] = ['UnitedHealthcare Choice Plus']
    cards = contract_requirements.evaluate(result, context, [])
    assert cards['referral']['status'] == 'Not required'
    assert cards['authorization']['status'] == 'Verification required'
    assert cards['procedures'][0]['referral']['status'] == 'Not required'


def test_optional_stored_rules_are_empty_before_database_setup(monkeypatch):
    monkeypatch.setattr(commercial_store.patient_store, 'DATABASE_URL', '')
    monkeypatch.delenv('DB_HOST', raising=False)
    monkeypatch.delenv('DATABASE_PASSWORD', raising=False)
    assert commercial_store.list_cpt_requirements('synthetic-practice') == []
