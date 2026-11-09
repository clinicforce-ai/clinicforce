"""Official web findings stay scoped to the workbook row, CPT and visit date."""
from app.insurance import plan_matrix
from app.insurance.contract_requirements import evaluate
from app.insurance.requirements import public_source_lookup as web


REFERRAL_TEXT = (
    'Choice and Choice Plus health plans allow members to choose a physician or specialist '
    'but do not need a referral to receive benefits. '
    'Navigate members must receive a referral from their PCP to see a network specialist '
    'for benefits to be covered.')
PA_TEXT = 'Prior authorization requirements for UnitedHealthcare commercial plans Effective November 1, 2026. 64615'


def case(plan='Choice Plus'):
    result = {'codes': [{'id': 'ncs', 'code': '95908', 'units': 1},
                        {'id': 'emg', 'code': '95886', 'units': 1}],
              'benefit_source': 'Stedi',
              'benefits': {'active': True, 'service_date_matches': True,
                           'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}}}
    context = {'service_date': '2026-11-09', 'payer_names': ['UnitedHealthcare'],
               'plans': [plan], 'member_id': 'SYNTHETIC', 'group_number': 'SYNTHETIC',
               'rendering_npi': '1111111111', 'billing_npi': '2222222222', 'pos': '11'}
    return result, context


def fake_read(url, day):
    return (PA_TEXT if url.endswith('.pdf') else REFERRAL_TEXT, 'synthetic-digest')


def test_choice_plus_official_sources_clear_both_diagnostic_codes(monkeypatch):
    monkeypatch.setattr(web, '_read', fake_read)
    result, context = case()
    findings = web.lookup(result, context, plan_matrix.bundled())
    assert {kind: item['status'] for kind, item in findings.items()} == {
        'referral': 'Not required', 'authorization': 'Not required'}
    result['public_source_rules'] = findings
    decisions = evaluate(result, context, [])
    assert [(row['referral']['status'], row['authorization']['status'])
            for row in decisions['procedures']] == [('Not required', 'Not required')] * 2
    assert all(any(source.get('source_type') == 'official_live_web'
                   for source in row['authorization']['sources']) for row in decisions['procedures'])


def test_payer_reported_choice_plus_network_name_matches_workbook(monkeypatch):
    monkeypatch.setattr(web, '_read', fake_read)
    result, context = case('UNITEDHEALTHCARE CHOICE PLUS')
    matched = plan_matrix.matched_row(context, plan_matrix.bundled())
    assert matched is not None and matched[0] == 11
    findings = web.lookup(result, context, plan_matrix.bundled())
    assert findings['referral']['status'] == 'Not required'
    assert findings['authorization']['status'] == 'Not required'
    context['plans'] = ['United Healthcare', 'Choice Plus', 'UNITEDHEALTHCARE CHOICE PLUS']
    assert web.lookup(result, context, plan_matrix.bundled())['authorization']['status'] == 'Not required'


def test_navigate_referral_and_auth_are_distinct(monkeypatch):
    monkeypatch.setattr(web, '_read', fake_read)
    result, context = case('Navigate')
    findings = web.lookup(result, context, plan_matrix.bundled())
    assert {kind: item['status'] for kind, item in findings.items()} == {
        'referral': 'Required', 'authorization': 'Not required'}


def test_generic_payer_or_unavailable_source_does_not_become_no(monkeypatch):
    monkeypatch.setattr(web, '_read', fake_read)
    result, context = case('United Healthcare')
    assert web.lookup(result, context, plan_matrix.bundled()) == {}
    context['plans'] = ['Choice Plus']
    monkeypatch.setattr(web, '_read', lambda url, day: None)
    assert web.lookup(result, context, plan_matrix.bundled()) == {}
    result['benefits']['identity'] = {'status': 'review'}
    monkeypatch.setattr(web, '_read', fake_read)
    assert web.lookup(result, context, plan_matrix.bundled()) == {}


def test_listed_code_or_wrong_version_cannot_be_cleared(monkeypatch):
    result, context = case()
    monkeypatch.setattr(web, '_read', lambda url, day: (
        PA_TEXT + ' 95908' if url.endswith('.pdf') else REFERRAL_TEXT, 'synthetic-digest'))
    assert 'authorization' not in web.lookup(result, context, plan_matrix.bundled())
    monkeypatch.setattr(web, '_read', lambda url, day: (
        PA_TEXT.replace('November 1, 2026', 'October 1, 2026') if url.endswith('.pdf')
        else REFERRAL_TEXT, 'synthetic-digest'))
    assert 'authorization' not in web.lookup(result, context, plan_matrix.bundled())
