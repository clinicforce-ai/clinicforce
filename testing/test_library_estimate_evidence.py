"""Uploaded evidence feeds estimates without changing payer facts or other clinics."""
import hashlib
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import patch

import pytest

from app.core import object_store, staff_store
from app.insurance import commercial, library_network, practice_prices


def word_prices():
    table = lambda rows: '<w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>' for value in row) + '</w:tr>' for row in rows) + '</w:tbl>'
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>1. Office visits</w:t></w:r></w:p>' + table([
                ['Service', 'CPT Range', 'Cash Price'], ['New visit', '99202-99205', '$220'],
                ['Established visit', '99212-99215', '$140'], ['Friends and Family discounted new visit', '99202-99205', '$80']])
            + '<w:p><w:r><w:t>2. Procedures — suggested prices pending approval</w:t></w:r></w:p>'
            + table([['Service', 'CPT', 'Cash Price'], ['Procedure', '11104', '$400']]) + '</w:body></w:document>')
    return data.getvalue()


def test_mixed_word_library_uses_final_rows_without_activating_proposals_or_discounts():
    content = word_prices()
    with pytest.raises(commercial.CommercialError, match='provisional'):
        practice_prices.read_rows(content, 'prices.docx')
    document = {'id': 'doc', 'kind': 'self_pay', 'filename': 'prices.docx', 'effective_from': '2026-01-01',
                'object_key': 'private', 'sha256': hashlib.sha256(content).hexdigest()}
    with patch.object(staff_store, 'list_library', return_value=[document]) as listing, \
         patch.object(object_store, 'get_object_bytes', return_value=content):
        config = practice_prices.from_library('clinic-a', '2026-10-07')
        listing.assert_called_with('clinic-a')
        assert config['id'] == 'library:doc'
        assert len(config['rows']) == 8
        assert {r['cash_cents'] for r in config['rows']} == {22000, 14000}
        assert practice_prices.from_library('clinic-a', '2025-12-31') is None
        document['sha256'] = 'changed'
        with pytest.raises(commercial.CommercialError, match='verified'):
            practice_prices.from_library('clinic-a', '2026-10-07')


def test_contract_matches_group_exact_product_and_date_without_inventing_rates():
    documents = [{'id': 'contract', 'filename': 'contract.pdf', 'pages': [{'text':
        'Example Payer Provider Agreement with Sample Neurology PLLC. EFFECTIVE DATE: May 15, 2026'}]}]
    matrices = [{'id': 'matrix', 'tables': [[['Payer', 'Plan Names on Accepted List'],
                    ['Example Payer', 'Example Payer Choice POS / POS II; Example Payer Open Choice PPO']]]}]
    args = (documents, matrices, 'Example Payer', ['Example Payer Choice POS II'], {'name': 'Sample Neurology'})
    result = library_network.match(*args, '2026-10-07')
    assert result['evidence_reference'] == 'library:contract'
    assert result['effective_from'] == '2026-05-15'
    assert 'rates' not in result
    assert library_network.match(*args, '2026-05-01') is None
    assert library_network.match(documents, matrices, 'Other Payer', args[3], args[4], '2026-10-07') is None
    assert library_network.match(documents, matrices, args[2], ['Example Payer Limited HMO'], args[4], '2026-10-07') is None
    assert library_network.match(documents, matrices, args[2], args[3], {'name': 'Different Medical Group'}, '2026-10-07') is None
    assert library_network.match(documents * 2, matrices, *args[2:], '2026-10-07') is None


def test_library_participation_does_not_override_a_conflicting_payer_statement():
    from app.api.routers import commercial_estimator as api
    from test_collection_guidance import office_payload
    payload = office_payload()
    payload['plans'][0]['benefits']['limitations'] = [{'service': {'system': 'STC', 'value': '98'},
        'network': {'indicator': 'IN_NETWORK'}, 'additionalInformation': [
            {'description': 'The provider ID you entered is not participating in this patient network.'}]}]
    benefits = commercial.normalize_stedi_271(payload, '98', False)
    assert benefits['network_conflict']
    result = {'issues': [], 'codes': [{'id': 'office', 'code': '99203', 'units': 1}]}
    with patch.object(api.library_network, 'network', return_value={'evidence_reference': 'library:contract'}), \
         patch.object(api.commercial_store, 'list_connections', return_value=[]):
        api._attach_previsit_pricing(result, 'clinic', {'service_date': '2026-10-07', 'place_of_service': '11'},
            {'payer': 'Example', 'payer_plan_names': ['Example PPO'], 'billing_provider': {'name': 'Sample Neurology'}},
            'payer', benefits, payload=payload)
    assert benefits['network_status'] == 'unknown'
