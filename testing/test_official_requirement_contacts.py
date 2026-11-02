"""General official contacts are a verification aid, never member clearance."""
from app.insurance.requirements.official_contact import attach, fallback


def test_cigna_per_cpt_unknown_requirement_gets_cited_call_script():
    rows = {'procedures': [{'code': '95908',
        'authorization': {'status': 'Verification required', 'sources': []},
        'referral': {'status': 'Verification required', 'sources': []}}]}
    attach(rows, {'cigna'}, '2026-11-10')
    item = rows['procedures'][0]
    assert item['authorization']['contact_phone'] == '800-882-4462'
    assert item['referral']['contact_phone'] == '866-494-2111'
    assert '95908' in item['authorization']['call_script']
    assert item['authorization']['contact_source']['url'].startswith('https://www.cigna.com/')
    assert item['authorization']['status'] == 'Verification required'
    assert item['authorization'].get('approval_status') is None


def test_approved_or_not_required_line_does_not_gain_a_call_task():
    rows = {'procedures': [{'code': '99204',
        'authorization': {'status': 'Required', 'approval_status': 'Verified', 'sources': []},
        'referral': {'status': 'Not required', 'sources': []}}]}
    attach(rows, {'aetna'}, '2026-11-10')
    assert 'contact' not in rows['procedures'][0]['authorization']
    assert 'contact' not in rows['procedures'][0]['referral']


def test_mixed_payer_names_do_not_route_to_the_wrong_administrator():
    assert fallback({'cigna', 'aetna'}, 'authorization', '95908', '2026-11-10') is None
    assert fallback({'unknown payer'}, 'authorization', '95908', '2026-11-10') is None
    assert fallback({'cignahealthcare'}, 'authorization', '95908', '2026-11-10')['contact_phone'] == '800-882-4462'
    assert fallback({'bluecrossandblueshieldoftexas'}, 'authorization', '95908', '2026-11-10')['contact_phone'] == '800-972-8088'
