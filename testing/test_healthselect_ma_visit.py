"""Synthetic checks for the HealthSelect MA virtual-visit arrival decision."""

from io import BytesIO
from unittest.mock import patch

import fitz
from PIL import Image

from app.insurance import amount_explanation, collection_guidance, encounter_summary, published_plan_benefits, v1_sources


def test_ecw_card_pair_is_ocr_read_and_identified():
    card = Image.new('RGB', (600, 350), 'white')
    image = BytesIO()
    card.save(image, format='PNG')
    pdf = fitz.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((30, 35), 'Patient: SAMPLE, Alex DOB: Jan 01, 1980')
    page.insert_text((30, 185), 'Document: insurance')
    for x in (39, 307):
        page.insert_image(fitz.Rect(x, 210, x + 256, 363), stream=image.getvalue())
    text = ('Plan Type: Member ID: Group #:\nMedical SYNTHETIC123 13546\n'
            'Name: Effective Dates:\nAlex P Sample 01/01/2026 - 12/31/2026\n'
            'Date of Birth: Policy Status:\n01/01/1980 Active\n'
            'HealthSelect Medicare Advantage PPO Plan\n'
            'Group Number: 13546 H2001-817-000 Payer ID: 87726\nPCP $0 Spec $0')
    with patch('app.insurance.golden._image_text', return_value=text) as ocr:
        fields, _, _ = v1_sources.read_card_fields([pdf.tobytes()])
    assert ocr.called
    assert {key: fields.get(key) for key in ('member_id', 'group_number', 'cms_plan_id', 'payer', 'specialist_copay')} == {
        'member_id': 'SYNTHETIC123', 'group_number': '13546', 'cms_plan_id': 'H2001-817',
        'payer': 'UnitedHealthcare', 'specialist_copay': '$0',
    }


def _visit():
    plan = {'names': ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'],
            'group_number': '13546', 'plan_number': None}
    benefits = {'active': True, 'service_date_matches': True, 'network_status': 'in_network',
                'network_source': 'clinic_library_contract', 'copay_cents': 0,
                'coinsurance_percent': '0', 'plan_details': plan}
    return {'payer': 'United Healthcare', 'service_date': '2026-11-09', 'reason': 'Televisit',
            'identity': {'status': 'matched', 'binding': 'synthetic', 'match_version': 2},
            'benefits': benefits, 'plan_details': {'stedi': plan},
            'recovery': {'reason_code': 'multiple_active_policies', 'active_policy_count': 2},
            'card_check': {'status': 'matched', 'cms_plan_id': 'H2001-817', 'patient_name_matches': True},
            'service_selection_status': 'preview', 'service_plan': {'recommended_ids': ['tele']},
            'codes': [{'id': 'tele', 'code': '98006', 'units': 1}],
            'price_range': {'mode': 'services', 'complete': False, 'conditional': False,
                            'lines': [{'id': 'tele', 'code': '98006', 'units': 1, 'calculation': None,
                                       'benefits': {'active': True, 'service_date_matches': True,
                                                    'copay_cents': 0, 'coinsurance_percent': '0'}}]}}


def test_zero_arrival_is_visible_while_final_liability_and_cob_remain_open():
    result = _visit()
    published_plan_benefits.apply_plan_terms(result['benefits'], result['plan_details']['stedi'],
                                              result['service_date'], True)
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'healthselect_ma_zero_televisit'
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None
    explanation = amount_explanation.build({**result, 'collection_guidance': guidance,
                                            'encounter_summary': summary}, guidance)
    assert '$0' in explanation['sentence']
    assert 'payer' in explanation['sentence'].lower()


def test_saved_visit_with_secondary_line_and_conditional_price_still_collects_zero():
    result = _visit()
    result['price_range']['conditional'] = True
    result['price_range']['lines'][0]['benefits']['has_secondary'] = True
    published_plan_benefits.apply_saved_group_ma_plan_terms(result)
    guidance = collection_guidance.build(result)
    assert guidance['basis'] == 'healthselect_ma_zero_televisit'
    assert guidance['amount_cents'] == 0
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None


def test_zero_arrival_requires_matching_card_and_zero_payer_terms():
    result = _visit()
    published_plan_benefits.apply_plan_terms(result['benefits'], result['plan_details']['stedi'],
                                              result['service_date'], True)
    for change in ({'card_check': {'status': 'conflict', 'conflicts': ['member_id']}},
                   {'price_range': {**result['price_range'], 'lines': [
                       {**result['price_range']['lines'][0], 'benefits': {'copay_cents': 2000,
                         'coinsurance_percent': '0', 'active': True}}]}},
                   {'plan_details': {'stedi': {**result['plan_details']['stedi'], 'group_number': 'OTHER'}}}):
        changed = {**result, **change}
        if 'plan_details' in change:
            changed['benefits'] = {**result['benefits'], 'published_plan_rule': None}
        assert collection_guidance.build(changed)['amount_cents'] is None


def test_zero_arrival_from_independent_ecw_check_retains_card_discrepancy():
    result = _visit()
    result['card_check'] = {'status': 'conflict', 'conflicts': ['member_id', 'group_number'],
                            'independent_ecw_check': True, 'patient_name_matches': False}
    published_plan_benefits.apply_plan_terms(result['benefits'], result['plan_details']['stedi'],
                                             result['service_date'], True)
    guidance = collection_guidance.build(result)
    assert guidance['basis'] == 'healthselect_ma_zero_televisit'
    assert guidance['amount_cents'] == 0
    result['price_range']['lines'][0]['benefits']['copay_cents'] = 2500
    assert collection_guidance.build(result)['amount_cents'] is None


def _office_biopsy():
    result = _visit()
    result.update(reason='Skin Biopsy', place_of_service='11',
                  card_check={'status': 'conflict', 'conflicts': ['member_id'],
                              'independent_ecw_check': True},
                  codes=[{'id': 'office', 'code': '99213', 'units': 1, 'modifiers': []},
                         {'id': 'biopsy', 'code': '11104', 'units': 1, 'modifiers': []}],
                  selected_ids=['office', 'biopsy'])
    result['service_plan']['recommended_ids'] = result['selected_ids']
    result['benefits']['has_secondary'] = True
    result['price_range']['lines'] = [
        {'id': row['id'], 'code': row['code'], 'units': 1, 'modifiers': [],
         'allowed_cents': fee,
         'allowed_basis': 'medicare_fee_schedule',
         'benefits': {'active': True, 'service_date_matches': None,
                      'network_status': 'in_network', 'copay_cents': 0,
                      'coinsurance_percent': '0', 'deductible_applies': None,
                      'resolved_service_type': '98' if row['code'] == '99213' else '2',
                      'copay_id': 'synthetic-copay', 'coinsurance_id': 'synthetic-coinsurance',
                      'has_secondary': True, 'source_errors': []}}
        for row, fee in zip(result['codes'], (9633, 12195))]
    published_plan_benefits.apply_saved_group_ma_plan_terms(result)
    return result


def test_office_biopsy_has_zero_check_in_despite_secondary_coverage():
    result = _office_biopsy()
    guidance = collection_guidance.build(result)
    assert guidance['amount_cents'] == 0
    assert guidance['basis'] == 'healthselect_ma_zero_office_biopsy'
    summary = encounter_summary.build({**result, 'collection_guidance': guidance})
    assert summary['collect_today_cents'] == 0
    assert summary['estimated_total_patient_min_cents'] is None
    explanation = amount_explanation.build({**result, 'collection_guidance': guidance,
                                            'encounter_summary': summary}, guidance)
    assert '$0' in explanation['sentence']


def test_office_biopsy_zero_requires_both_covered_zero_cost_lines():
    from copy import deepcopy
    base = _office_biopsy()
    for change in ('copay', 'coinsurance', 'biopsy', 'extra_service', 'card', 'network',
                   'deductible', 'service_type', 'payer_source'):
        result = deepcopy(base)
        if change == 'copay':
            result['price_range']['lines'][1]['benefits']['copay_cents'] = 2500
        elif change == 'coinsurance':
            result['price_range']['lines'][1]['benefits']['coinsurance_percent'] = '20'
        elif change == 'biopsy':
            result['price_range']['lines'][1]['code'] = '11105'
        elif change == 'extra_service':
            result['codes'].append({'id': 'third', 'code': '88305', 'units': 1})
        elif change == 'card':
            result['card_check'].pop('independent_ecw_check')
        elif change == 'network':
            result['benefits']['network_status'] = 'unknown'
        elif change == 'service_type':
            result['price_range']['lines'][1]['benefits']['resolved_service_type'] = '98'
        elif change == 'payer_source':
            result['price_range']['lines'][1]['benefits']['copay_id'] = None
        else:
            result['benefits']['deductible_applies'] = True
        assert collection_guidance.build(result)['amount_cents'] is None, change
