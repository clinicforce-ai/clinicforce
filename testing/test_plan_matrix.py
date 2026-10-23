"""Synthetic patients against all three NMCT workbook sheets; no live payer access."""
from copy import deepcopy
import asyncio
import hashlib
import json
import re
from datetime import date
from types import SimpleNamespace

import httpx

from app.core import library_files
from app.insurance import contract_requirements, plan_matrix, stedi_cpt_authorization
from app.api.routers.commercial_flow.commercial_previsit_requirements import check_cpt_authorization


def visit(payer, plan, *codes, active=True, group='G-1', signal=None):
    result = {'codes': [{'code': code} for code in codes], 'benefit_source': 'Stedi',
              'benefits': {'active': active, 'service_date_matches': True, 'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}},
              'price_range': {'lines': [{'code': code, 'benefits': {
                  'requirements': {'referral': {'payer_signal': signal}} if signal else {}}} for code in codes]}}
    context = {'service_date': '2026-10-12', 'payer_names': [payer], 'plans': [plan],
               'member_id': 'M-1', 'group_number': group,
               'rendering_npi': '1234567890',
               'coverage_period': {'start': '2026-01-01', 'end': '2026-12-31'}}
    return contract_requirements.evaluate(result, context, [])


def test_three_sheets_supply_exact_plan_rules_and_portals():
    workbook = plan_matrix.bundled()
    assert len(workbook['rows']) == 14 and len(workbook['steps']) == 4
    assert workbook['portals']['Blue Cross Blue Shield of Texas']['Portal URL'] == 'availity.com'
    ppo = visit('BCBSTX', 'Blue Choice PPO', '99213')
    hmo = visit('BCBSTX', 'Blue Advantage HMO', '99213')
    assert ppo['referral']['status'] == 'Not required'
    assert hmo['referral']['status'] == 'Required'
    assert 'BCBSTX 2026' in ppo['referral']['sources'][0]['label']
    assert any('Payer Plan Matrix, row 17' in source.get('label', '') for source in ppo['referral']['sources'])
    assert 'BCBSTX 2026' in hmo['referral']['sources'][0]['label']
    assert ppo['referral']['verify_url'] == 'https://availity.com'
    assert any('Portal & Contact Reference, row' in source.get('label', '') for source in ppo['referral']['sources'])
    assert any(source.get('url', '').endswith('/ppo-secd-referral.pdf') for source in ppo['referral']['sources'])
    assert ppo['referral']['contact']
    assert 'referral module' in ppo['referral']['verification']
    assert 'PCP' in hmo['referral']['reason']


def test_scoped_clinic_network_name_can_select_the_referral_rule():
    result = {'codes': [{'code': '99213'}], 'benefit_source': 'Stedi',
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}}
    context = {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'],
               'plans': ['Preferred Provider Option Plus Medical', 'Blue Choice PPO'],
               'member_id': 'M-1', 'group_number': 'G-1', 'rendering_npi': '1234567890',
               'coverage_period': {'start': '2026-01-01', 'end': '2026-12-31'}}
    assert contract_requirements.evaluate(result, context, [])['referral']['status'] == 'Not required'
    context['plans'][0] = 'CI Preferred Provider Option Medical'
    assert contract_requirements.evaluate(result, context, [])['referral']['status'] == 'Not required'


def test_current_identity_mismatch_blocks_stale_matched_matrix_rule():
    result = {'codes': [{'code': '99213'}], 'benefit_source': 'Stedi',
              'identity': {'status': 'review', 'differences': [{'field': 'first_name'}]},
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'stale-binding', 'match_version': 2}}}
    context = {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'],
               'plans': ['Blue Choice PPO'], 'member_id': 'M-1', 'group_number': 'G-1',
               'rendering_npi': '1234567890',
               'coverage_period': {'start': '2026-01-01', 'end': '2026-12-31'}}
    decision = plan_matrix.evaluate(result, context, plan_matrix.bundled())['referral']
    assert decision['status'] == 'Verification required'
    assert 'patient identity' in decision['reason']


def test_generic_bcbstx_ppo_label_shows_relevant_research_without_clearing_patient_rule():
    evidence = visit('BCBSTX', 'PREFERRED PROVIDER OPTION PLUS MEDICAL', '99203')
    named = visit('Blue Cross Blue Shield Tx', 'PREFERRED PROVIDER OPTION PLUS MEDICAL', '99203')
    assert named['referral']['sources'][0]['label'] == evidence['referral']['sources'][0]['label']
    assert evidence['referral']['status'] == 'Verification required'
    assert 'PPO' in evidence['referral']['reason']
    assert 'no referral generally' in evidence['referral']['reason'].lower()
    assert 'row 17' in evidence['referral']['sources'][0]['label']
    assert evidence['authorization']['status'] == 'Verification required'
    assert '99203' in evidence['authorization']['reason']
    assert 'row 17' in evidence['authorization']['sources'][0]['label']
    assert visit('BCBSTX', 'Unknown medical plan', '99203')['referral']['status'] == 'Verification required'
    hmo = visit('BCBSTX', 'Health Maintenance Organization Medical', '99203')
    assert hmo['referral']['status'] == 'Verification required'
    assert 'row 18' in hmo['referral']['sources'][0]['label']
    mixed = plan_matrix.evaluate({'codes': [{'code': '99203'}]},
        {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'], 'plans': ['PPO', 'HMO']}, plan_matrix.bundled())
    assert 'rows 17, 18' in mixed['referral']['sources'][0]['label']


def test_explicit_bluecard_label_uses_out_of_state_research():
    evidence = visit('BCBSTX', 'Participating out-of-state BlueCard/BCBS networks', '99203')
    assert evidence['referral']['status'] == 'Verification required'
    assert 'row 19' in evidence['referral']['sources'][0]['label']


def test_humana_plan_types_and_pending_participation_are_distinct():
    assert visit('Humana', 'Humana Medicare HMO', '99213')['referral']['status'] == 'Verification required'
    for plan in ('Humana Medicare PPO', 'Humana Medicare Network PFFS'):
        result = visit('Humana', plan, '99213')
        assert result['referral']['status'] == 'Verification required'
        assert 'contract is active' in result['referral']['reason']
    botox = visit('Humana', 'Humana Medicare HMO', '64615')
    assert botox['authorization']['status'] == 'Verification required'
    assert botox['authorization']['verify_url'] == 'https://provider.humana.com'


def test_cpt_specific_trigger_keeps_requirement_and_approval_distinct():
    ordinary = visit('BCBSTX', 'Blue Choice PPO', '99213')['authorization']
    botox = visit('BCBSTX', 'Blue Choice PPO', '64615')['authorization']
    assert ordinary['status'] == 'Verification required'
    assert botox['status'] == 'Verification required'
    assert 'separate injection code' in botox['reason']
    assert botox['pa_approval'] == 'UNVERIFIED'
    assert 'Availity' in botox['verification']
    assert botox['contact']
    assert visit('Aetna', 'Open Choice PPO', '64612')['authorization']['status'] == 'Verification required'
    emg = visit('BCBSTX', 'Blue Choice PPO', '95910')['authorization']
    assert emg['status'] == 'Verification required' and 'product and ASO exceptions' in emg['reason']


def test_exact_stedi_cpt_and_office_setting_settle_authorization_over_matrix():
    rows = [{'service': {'system': 'CPT', 'value': code},
             'network': {'indicator': 'IN_NETWORK'}, 'placesOfService': [{'code': pos}],
             'priorAuthIndicator': indicator}
            for code in ('95908', '95886')
            for pos, indicator in (('11', 'NOT_REQUIRED'), ('21', 'REQUIRED'))]
    payload = {'plans': [{'benefits': {'coverageBasis': rows}}]}
    office = stedi_cpt_authorization.decisions(payload, ['95908', '95886'], '11', 'in_network')
    assert office == {'95908': 'Not required', '95886': 'Not required'}
    assert stedi_cpt_authorization.decisions(payload, ['95908'], '21', 'in_network') == {'95908': 'Required'}
    assert stedi_cpt_authorization.decisions(payload, ['95908'], '11', 'out_of_network') == {}
    rows.append({**rows[0], 'placesOfService': [{'code': '11'}], 'priorAuthIndicator': 'REQUIRED'})
    assert '95908' not in stedi_cpt_authorization.decisions(payload, ['95908'], '11', 'in_network')
    rows.pop()
    result = {'codes': [{'id': code, 'code': code, 'units': 1} for code in office],
              'benefit_source': 'Stedi', 'identity': {'status': 'matched', 'binding': 'synthetic-current-binding',
                                                       'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'in_network'},
              'cpt_authorization': {'state': 'complete', 'service_date': '2026-10-12', 'pos': '11',
                                     'network': 'in_network', 'patient_binding': 'synthetic-current-binding',
                                     'decisions': office, 'check_id': 'ec_synthetic-check'}}
    context = {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'], 'plans': ['Blue Choice PPO'],
               'member_id': 'M-1', 'group_number': 'G-1', 'rendering_npi': '1234567890', 'pos': '11'}
    evidence = contract_requirements.evaluate(result, context, [])
    assert evidence['authorization']['status'] == 'Not required'
    assert {item['authorization']['status'] for item in evidence['procedures']} == {'Not required'}
    dated = deepcopy(result)
    dated['benefits'].update(service_date_matches=None,
                             coverage_evidence={'status': 'ACTIVE', 'start': '2026-01-01'})
    assert contract_requirements.evaluate(dated, context, [])['authorization']['status'] == 'Not required'
    dated['benefits']['coverage_evidence']['start'] = '2026-11-01'
    assert contract_requirements.evaluate(dated, context, [])['authorization']['status'] == 'Verification required'
    stale = deepcopy(result)
    stale['cpt_authorization']['pos'] = '21'
    stale_evidence = contract_requirements.evaluate(stale, context, [])
    assert stale_evidence['authorization']['status'] == 'Verification required'


def test_selected_cpts_are_checked_once_and_saved_with_exact_stedi_scope():
    response = {'payerId': 'PAYER', 'provider': {'npi': '1234567890'},
                'subscriber': {'memberId': 'MEMBER', 'dateOfBirth': '1970-01-01',
                               'name': {'person': {'firstName': 'Test', 'lastName': 'Member'}}},
                'id': 'ec_synthetic-check', 'plans': [{'benefits': {'coverageBasis': [
                    {'service': {'system': 'CPT', 'value': code}, 'network': {'indicator': 'IN_NETWORK'},
                     'placesOfService': [{'code': '11'}], 'priorAuthIndicator': 'NOT_REQUIRED'}
                    for code in ('95908', '95886')]}}]}
    sent = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def post(self, _url, **kwargs):
            sent.append(kwargs['json'])
            return SimpleNamespace(is_error=False, json=lambda: response)
    async def thread(fn, *args, **kwargs): return fn(*args, **kwargs)
    def save(case, *_):
        case['revision'] = case.get('revision', 0) + 1
        return case
    api = SimpleNamespace(re=re, hashlib=hashlib, json=json, date=date, run_in_threadpool=thread,
                          _save=save, STEDI_URL='https://example.test/eligibility',
                          httpx=SimpleNamespace(AsyncClient=lambda **_: Client(), Timeout=httpx.Timeout,
                                                HTTPError=httpx.HTTPError),
                          object_store=SimpleNamespace(put_staff_document=lambda *_: 'protected-object'))
    payload = {'payerId': 'PAYER', 'provider': {'npi': '1234567890', 'name': {'organization': 'Clinic'}},
               'subscriber': {'memberId': 'MEMBER', 'dateOfBirth': '1970-01-01',
                              'name': {'person': {'firstName': 'Test', 'lastName': 'Member'}}}}
    result = {'codes': [{'code': '95908'}, {'code': '95886'}],
              'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2},
              'benefits': {'active': True, 'service_date_matches': None, 'network_status': 'in_network',
                           'coverage_evidence': {'status': 'ACTIVE', 'start': '2026-01-01'}}}
    inputs = {'service_date': '2026-10-12', 'place_of_service': '11'}
    case = asyncio.run(check_cpt_authorization(api, result, {'id': 'synthetic'}, payload, inputs,
                                                {'practice_id': 'synthetic'}, 'synthetic-key'))
    assert case['cpt_authorization']['decisions'] == {'95908': 'Not required', '95886': 'Not required'}
    assert len(sent) == 1 and len(sent[0]['encounter']['services']) == 2
    asyncio.run(check_cpt_authorization(api, result, case, payload, inputs,
                                         {'practice_id': 'synthetic'}, 'synthetic-key'))
    assert len(sent) == 1
    outside = deepcopy(result)
    outside['benefits']['coverage_evidence']['start'] = '2026-11-01'
    asyncio.run(check_cpt_authorization(api, outside, {'id': 'synthetic-other'}, payload, inputs,
                                         {'practice_id': 'synthetic'}, 'synthetic-key'))
    assert len(sent) == 1


def test_rental_network_missing_plan_and_checklist_gaps_fail_closed():
    rental = visit('HealthSmart', 'HealthSmart Accel Network', '99213')
    assert rental['referral']['status'] == rental['authorization']['status'] == 'Verification required'
    assert 'underlying payer' in rental['referral']['reason']
    assert visit('BCBSTX', 'Unknown PPO', '99213')['referral']['status'] == 'Verification required'
    incomplete = visit('BCBSTX', 'Blue Choice PPO', '99213', group='', active=False)
    assert incomplete['referral']['status'] == 'Verification required'
    assert 'group number' in incomplete['referral']['reason']
    assert visit('BCBSTX', 'Blue Essentials', '99213')['referral']['status'] == 'Required'


def test_payer_and_contract_conflicts_keep_both_sources():
    payer = visit('BCBSTX', 'Blue Choice PPO', '99213', signal='required')['referral']
    assert payer['status'] == 'Required'
    assert any('Payer Plan Matrix' in source.get('label', '') for source in payer['sources'])
    assert any('Stedi' in source.get('label', '') for source in payer['sources'])
    pdf = {'id': 'contract-1', 'kind': 'contract', 'content_type': 'application/pdf',
           'payer': 'BCBSTX', 'filename': 'synthetic-contract.pdf', 'sha256': 'digest',
           'extraction': {'sha256': 'digest', 'pages': [{'number': 4, 'text': ''}],
                          'tables': [{'locator': 'Page 4', 'rows': [
                              ['CPT', 'Plan', 'Network', 'Rendering NPI', 'Billing NPI', 'POS', 'Effective From', 'Referral'],
                              ['99213', 'Blue Advantage HMO', 'Preferred', '1234567890', '0987654321', '11', '2026-01-01', 'Not required']]}]}}
    result = {'codes': [{'code': '99213'}], 'benefit_source': 'Stedi',
              'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                           'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}}
    context = {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'], 'plans': ['Blue Advantage HMO'],
               'member_id': 'M-1', 'group_number': 'G-1', 'coverage_period': {'start': '2026-01-01'},
               'rendering_npi': '1234567890',
               'payer_verified': True, 'network': 'Preferred', 'rendering_npi': '1234567890',
               'billing_npi': '0987654321', 'pos': '11'}
    referral = contract_requirements.evaluate(result, context, [pdf])['referral']
    assert referral['status'] == 'Verification required'
    assert any(source.get('page') == 4 for source in referral['sources'])


def test_existing_plan_matrix_upload_can_replace_bundled_research():
    _, tables = library_files.structured_content(plan_matrix.WORKBOOK.read_bytes(), plan_matrix.WORKBOOK.name)
    tables = deepcopy(tables)
    table = next(row for row in tables if row['locator'] == 'Sheet Payer Plan Matrix')
    index = table['row_numbers'].index(17)
    table['rows'][index][7] = 'Yes'
    table['rows'][index][8] = 'This updated exact plan requires a PCP referral before the visit.'
    uploaded = {'id': 'synthetic-matrix', 'kind': 'plan_matrix', 'filename': 'updated-research.xlsx', 'sha256': 'new',
                'extraction': {'sha256': 'new', 'tables': tables}}
    data = plan_matrix.uploaded([uploaded])
    evidence = plan_matrix.evaluate({'codes': [{'code': '99213'}], 'benefit_source': 'Stedi',
        'benefits': {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                     'identity': {'status': 'matched', 'binding': 'synthetic-current-binding', 'match_version': 2}}},
        {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'], 'plans': ['Blue Choice PPO'],
         'member_id': 'M-1', 'group_number': 'G-1', 'rendering_npi': '1234567890',
         'coverage_period': {'start': '2026-01-01'}}, data)
    assert evidence['referral']['status'] == 'Verification required'
    assert 'updated-research.xlsx' in evidence['referral']['sources'][0]['label']
    assert evidence['referral']['sources'][0]['document_id'] == 'synthetic-matrix'
    assert plan_matrix.uploaded([uploaded | {'effective_from': '2027-01-01'}], '2026-10-12') is plan_matrix.bundled()
    assert plan_matrix.uploaded([uploaded | {'extraction': {'sha256': 'new', 'tables': tables[:2]}}], '2026-10-12') is plan_matrix.bundled()
