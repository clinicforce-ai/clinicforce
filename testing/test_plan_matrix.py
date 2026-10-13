"""Synthetic patients against all three NMCT workbook sheets; no live payer access."""
from copy import deepcopy

from app.core import library_files
from app.insurance import contract_requirements, plan_matrix


def visit(payer, plan, *codes, active=True, group='G-1', signal=None):
    result = {'codes': [{'code': code} for code in codes], 'benefit_source': 'Stedi',
              'benefits': {'active': active, 'service_date_matches': True, 'network_status': 'in_network',
                           'identity': {'status': 'matched'}},
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
    assert 'Payer Plan Matrix, row 17' in ppo['referral']['sources'][0]['label']
    assert all('Stedi' not in source.get('label', '') for source in ppo['referral']['sources'])
    assert 'Payer Plan Matrix, row 18' in hmo['referral']['sources'][0]['label']
    assert ppo['referral']['verify_url'] == 'https://availity.com'
    assert 'Portal & Contact Reference, row' in ppo['referral']['sources'][1]['label']
    assert ppo['referral']['sources'][2]['url'].endswith('/ppo-secd-referral.pdf')
    assert ppo['referral']['contact']
    assert 'referral module' in ppo['referral']['verification']
    assert 'PCP' in hmo['referral']['reason']


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
    assert visit('Humana', 'Humana Medicare HMO', '99213')['referral']['status'] == 'Required'
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
    assert botox['status'] == 'Required'
    assert 'Approval status' in botox['reason'] and 'final billed CPTs' in botox['reason']
    assert botox['approval_status'] == 'Not verified'
    assert 'Availity' in botox['verification']
    assert botox['contact']
    assert visit('Aetna', 'Open Choice PPO', '64612')['authorization']['status'] == 'Required'
    emg = visit('BCBSTX', 'Blue Choice PPO', '95910')['authorization']
    assert emg['status'] == 'Verification required' and 'EMG/NCS code 95910' in emg['reason']


def test_rental_network_missing_plan_and_checklist_gaps_fail_closed():
    rental = visit('HealthSmart', 'HealthSmart Accel Network', '99213')
    assert rental['referral']['status'] == rental['authorization']['status'] == 'Verification required'
    assert 'underlying payer' in rental['referral']['reason']
    assert visit('BCBSTX', 'Unknown PPO', '99213')['referral']['status'] == 'Verification required'
    incomplete = visit('BCBSTX', 'Blue Choice PPO', '99213', group='', active=False)
    assert incomplete['referral']['status'] == 'Verification required'
    assert 'group number' in incomplete['referral']['reason']
    assert visit('BCBSTX', 'Blue Essentials', '99213')['referral']['status'] == 'Verification required'


def test_payer_and_contract_conflicts_keep_both_sources():
    payer = visit('BCBSTX', 'Blue Choice PPO', '99213', signal='required')['referral']
    assert payer['status'] == 'Verification required'
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
                           'identity': {'status': 'matched'}}}
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
                     'identity': {'status': 'matched'}}},
        {'service_date': '2026-10-12', 'payer_names': ['BCBSTX'], 'plans': ['Blue Choice PPO'],
         'member_id': 'M-1', 'group_number': 'G-1', 'rendering_npi': '1234567890',
         'coverage_period': {'start': '2026-01-01'}}, data)
    assert evidence['referral']['status'] == 'Required'
    assert 'updated-research.xlsx' in evidence['referral']['sources'][0]['label']
    assert evidence['referral']['sources'][0]['document_id'] == 'synthetic-matrix'
    assert plan_matrix.uploaded([uploaded | {'effective_from': '2027-01-01'}], '2026-10-12') is plan_matrix.bundled()
    assert plan_matrix.uploaded([uploaded | {'extraction': {'sha256': 'new', 'tables': tables[:2]}}], '2026-10-12') is plan_matrix.bundled()
