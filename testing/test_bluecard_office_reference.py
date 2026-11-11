from app.api.routers import commercial_estimator  # Load the route before its pricing helper.
from app.api.routers.commercial_flow import commercial_previsit_rate_reference as reference


def test_incomplete_benefits_do_not_duplicate_a_resolved_allowed_rate(monkeypatch):
    calls = []
    monkeypatch.setattr(reference, '_attach_temporary_blue_cross_rates',
                        lambda *args: calls.append(args))
    result = {'price_range': {'complete': False, 'lines': [
        {'code': '99203', 'allowed_cents': 10117,
         'allowed_basis': 'clinic_contract', 'rate_resolution': {'status': 'RESOLVED'}}]}}
    reference.attach_blue_cross_reference_if_unpriced(result, 'synthetic', {}, {}, 'Blue Cross Blue Shield Tx')
    assert calls == []
    result['price_range']['lines'][0]['rate_resolution']['status'] = 'MISSING'
    reference.attach_blue_cross_reference_if_unpriced(result, 'synthetic', {}, {}, 'Blue Cross Blue Shield Tx')
    assert len(calls) == 1


def test_prudent_buyer_uses_only_current_scoped_library_fee(monkeypatch):
    result = {'payer': 'Blue Cross Blue Shield Tx',
              'plan_details': {'stedi': {'names': ['PPO PRUDENT BUYER INCENTIVE']}},
              'benefits': {'network_status': 'in_network'},
              'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}]}
    inputs = {'rendering_provider': {'npi': '1111111111'}, 'place_of_service': '11',
              'service_date': '2026-11-06'}
    source = {'billing_provider': {'npi': '2222222222'}, 'location_id': 'office'}
    monkeypatch.setattr(reference._api.commercial_store, 'list_connections', lambda practice: [
        {'id': 'blue', 'status': 'staff_guidance_enabled', 'stedi_payer_id': 'QDTRP',
         'display_name': 'Blue Cross Blue Shield Tx'}])
    monkeypatch.setattr(reference._api.commercial_store, 'list_networks', lambda practice: [
        {'id': 'network', 'payer_connection_id': 'blue', 'status': 'verified',
         'rendering_npi': '1111111111', 'billing_npi': '2222222222', 'location_id': 'office',
         'place_of_service': '11', 'effective_from': '2026-01-01'}])
    monkeypatch.setattr(reference._api.commercial_store, 'list_profiles', lambda practice: [
        {'payer_connection_id': 'blue', 'provider_network_id': 'network',
         'status': 'staff_guidance_enabled'}])
    monkeypatch.setattr(reference._api.commercial_store, 'matching_rates',
                        lambda *args: [{'code': '99203', 'modifier': '', 'allowed_cents': 10117,
                                        'source_reference': 'library:synthetic-fee'}])
    assert reference.prudent_buyer_office_reference(result)
    reference._attach_temporary_blue_cross_rates(result, 'practice', inputs, source, 'BlueCard member')
    fee = result['temporary_rate_reference']
    assert fee['complete'] is True
    assert fee['lines'][0]['unit_reference_cents'] == 10117
    assert 'not confirmed allowed amounts for this BlueCard member' in fee['message']
    assert not reference.prudent_buyer_office_reference({**result,
        'plan_details': {'stedi': {'names': ['PPO OTHER']}}})


def test_prudent_buyer_uses_supplied_fee_only_in_exact_office_scope(monkeypatch):
    from app.insurance import network_participation

    monkeypatch.setattr(network_participation, 'is_nmct_practice', lambda practice: practice == 'nmct')
    monkeypatch.setattr(reference._api.commercial_store, 'list_connections', lambda practice: [])
    result = {'payer': 'Blue Cross Blue Shield Tx',
              'plan_details': {'stedi': {'names': ['PPO PRUDENT BUYER INCENTIVE'],
                                         'group_number': '174302MCAF'}},
              'benefits': {'network_status': 'in_network'},
              'codes': [{'id': 'office', 'code': '99203', 'units': 1, 'modifiers': []}]}
    inputs = {'rendering_provider': {'npi': '1111111111'}, 'place_of_service': '11',
              'service_date': '2026-11-06'}
    source = {'billing_provider': {'npi': '2222222222'}, 'location_id': 'office'}
    reference.attach_prudent_buyer_office_reference(result, 'nmct', inputs, source)
    assert result['temporary_rate_reference']['lines'][0]['unit_reference_cents'] == 10117
    assert 'not a payer allowed amount' in result['temporary_rate_reference']['message']
    other_date = {**result, 'temporary_rate_reference': None}
    reference.attach_prudent_buyer_office_reference(other_date, 'nmct',
                                                    {**inputs, 'service_date': '2026-11-07'}, source)
    assert other_date['temporary_rate_reference'] is None
