"""Synthetic 2026 GEHA group office benefit and collection boundaries."""
from copy import deepcopy

import pytest

from app.insurance import collection_guidance, published_plan_benefits


DAY = '2026-11-05'
PLAN = {'names': ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'],
        'group_number': '16615'}
IDENTITY = {'status': 'matched', 'binding': 'synthetic', 'match_version': 2}


def case():
    benefits = {'active': True, 'network_status': 'in_network',
                'network_source': 'clinic_library_contract', 'identity': IDENTITY,
                'qmb_status': 'unknown'}
    published_plan_benefits.apply_plan_terms(benefits, PLAN, DAY, True)
    return {'benefits': benefits, 'identity': IDENTITY, 'service_date': DAY,
            'payer': 'United Healthcare', 'plan_details': {'stedi': PLAN},
            'price_range': {'mode': 'services', 'conditional': False, 'service_issues': [],
                            'lines': [{'id': 'office', 'code': '99203', 'units': 1,
                                       'allowed_cents': None,
                                       'benefits': {'active': True, 'copay_cents': 0,
                                                    'coinsurance_percent': '0',
                                                    'deductible_applies': None,
                                                    'identity': IDENTITY,
                                                    'network_status': 'in_network'}}]},
            'selected_ids': ['office'], 'service_selection_status': 'selected'}


def test_published_zero_office_without_payer_deductible_oop_or_allowance():
    result = case()
    assert result['benefits']['published_plan_rule']['in_network_oop_max_cents'] == 0
    assert result['benefits'].get('deductible_remaining_cents') is None
    assert result['benefits'].get('oop_remaining_cents') is None
    guidance = collection_guidance.build(result)
    assert (guidance['amount_cents'], guidance['basis'], guidance['status']) == (
        0, 'published_geha_pshb_zero_office', 'zero_advance')


@pytest.mark.parametrize('change', [
    'different_group', 'different_plan', 'other_year', 'unbound', 'network',
    'another_service', 'different_cpt', 'nonzero_copay', 'deductible_applies',
    'source_error', 'secondary', 'identity', 'inactive',
])
def test_zero_office_requires_exact_current_product_and_safe_selected_service(change):
    result = deepcopy(case())
    if change in {'different_group', 'different_plan', 'other_year', 'unbound'}:
        plan = deepcopy(PLAN)
        day = DAY
        bound = True
        if change == 'different_group':
            plan['group_number'] = '99999'
        elif change == 'different_plan':
            plan['names'] = ['UnitedHealthcare Medicare Advantage PPO']
        elif change == 'other_year':
            day = '2027-01-05'
        else:
            bound = False
        result['benefits'].pop('published_plan_rule')
        published_plan_benefits.apply_plan_terms(result['benefits'], plan, day, bound)
    elif change == 'network':
        result['benefits']['network_status'] = 'out_of_network'
    elif change == 'another_service':
        result['price_range']['lines'].append({'id': 'diagnostic', 'code': '95908', 'units': 1,
                                               'benefits': {'active': True}})
        result['selected_ids'].append('diagnostic')
    elif change == 'different_cpt':
        result['price_range']['lines'][0]['code'] = '95908'
    elif change == 'nonzero_copay':
        result['price_range']['lines'][0]['benefits']['copay_cents'] = 3000
    elif change == 'deductible_applies':
        result['price_range']['lines'][0]['benefits']['deductible_applies'] = True
    elif change == 'source_error':
        result['benefits']['source_errors'] = [{'kind': 'saved_raw_unavailable'}]
    elif change == 'secondary':
        result['benefits']['has_secondary'] = True
    elif change == 'identity':
        result['identity'] = {'status': 'review', 'match_version': 2}
    elif change == 'inactive':
        result['benefits']['active'] = False
    guidance = collection_guidance.build(result)
    assert guidance['basis'] != 'published_geha_pshb_zero_office'
