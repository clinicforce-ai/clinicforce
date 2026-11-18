"""Clinic-stated 2026 Aetna Choice POS II office participation is narrowly scoped."""
from copy import deepcopy
from unittest.mock import patch

from app.api.routers.commercial_flow.commercial_previsit_network_guard import (
    apply_nmct_aetna_office_attestation, recheck_saved_network_evidence)


def _case():
    identity = {'match_version': 2, 'status': 'matched', 'binding': 'synthetic-person'}
    benefits = {'active': True, 'identity': identity, 'service_date_matches': False,
                'returned_service_dates': {'start': '2026-11-17'},
                'network_status': 'unknown'}
    plan = {'names': ['Aetna Choice POS II'], 'group_number': '047925802400006'}
    result = {'payer': 'Aetna', 'identity': identity, 'plan_details': {'stedi': plan}}
    inputs = {'service_date': '2026-11-18', 'place_of_service': '11',
              'rendering_provider': {'npi': '1720615453'}}
    source = {'rendering_provider': {'npi': '1720615453'}}
    return result, benefits, inputs, source, plan


def test_clinic_attestation_labels_exact_aetna_group_and_provider_in_network():
    result, benefits, inputs, source, plan = _case()
    assert apply_nmct_aetna_office_attestation(result, benefits, 'primary', inputs, source, plan)
    assert benefits['network_status'] == 'in_network'
    assert benefits['network_source'] == 'clinic_attested_aetna_office'
    result, benefits, inputs, source, plan = _case()
    source['rendering_provider'] = {}
    assert apply_nmct_aetna_office_attestation(result, benefits, 'primary', inputs, source, plan)


def test_saved_result_rechecks_the_scoped_clinic_network_statement():
    result, benefits, inputs, source, plan = _case()
    result['benefits'] = benefits
    case = {'previsit_context': {'inputs': inputs, 'source': source}}
    with patch('app.api.routers.commercial_flow.commercial_previsit_network_guard.staff_store.list_library',
               return_value=[]):
        recheck_saved_network_evidence(result, case, 'primary', {'display_name': 'Aetna'})
    assert benefits['network_status'] == 'in_network'
    assert result['network_evidence']['source'] == 'clinic_attested_aetna_office'


def test_attestation_rejects_other_group_provider_or_conflicting_payer_tier():
    result, benefits, inputs, source, plan = _case()
    for change in ('group', 'provider', 'other_practice', 'out_of_network', 'identity'):
        r, b, i, s, p = deepcopy((result, benefits, inputs, source, plan))
        if change == 'group':
            p['group_number'] = 'OTHER'
        elif change == 'provider':
            i['rendering_provider']['npi'] = '1234567890'
        elif change == 'out_of_network':
            b.update(network_status='out_of_network', network_source='payer_participation_message')
        elif change == 'identity':
            b['identity']['status'] = 'review'
        practice = 'other' if change == 'other_practice' else 'primary'
        assert not apply_nmct_aetna_office_attestation(r, b, practice, i, s, p), change
