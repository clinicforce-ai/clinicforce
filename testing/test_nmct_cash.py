"""Money and tenant regressions against the NMCT supplied Word schedule."""
from copy import deepcopy
import pytest
from app.insurance import nmct_cash as cash
from app.api.routers import commercial_estimator as api
from unittest.mock import patch


def codes(*items):
    return [{'code': code, 'units': units} for code, units in items]


@pytest.mark.parametrize('code,amount', [('99202',25000),('99205',25000),('99212',15000),('99215',15000)])
def test_office_flat_price(code, amount):
    assert cash.quote(codes((code,1)))['min_cents'] == amount
    assert not cash.quote(codes((code,2)))['complete']


def test_discount_is_explicit_new_patient_only():
    assert cash.quote(codes(('99204',1)),selection='friends_family')['min_cents'] == 10000
    assert not cash.quote(codes(('99214',1)),selection='friends_family')['complete']
    assert not cash.quote(codes(('99204',1)),selection='neck_arm')['complete']


@pytest.mark.parametrize('selection,amount,limbs', [('carpal_one',50000,1),('carpal_both',70000,2),('neck_arm',80000,1),('back_leg',70000,1),('neuropathy_legs',80000,2),('full_study',130000,4),('single_nerve',40000,1)])
def test_packages_include_emg_and_office(selection,amount,limbs):
    result=cash.quote(codes(('95910',1),('95886',limbs),('99204',1)),selection=selection)
    assert result['min_cents']==result['max_cents']==amount
    assert 'No extra office' in result['message']


def test_identical_cpts_do_not_identify_package_and_expansion_needs_review():
    rows=codes(('95910',1),('95886',1))
    assert not cash.quote(rows)['complete']
    assert cash.quote(rows,selection='back_leg')['min_cents']==70000
    assert cash.quote(rows,selection='neck_arm')['min_cents']==80000
    assert not cash.quote(codes(('95910',1),('95886',2)),selection='neck_arm')['complete']
    assert not cash.quote(codes(('95886',1)),selection='neck_arm')['complete']
    assert not cash.quote(codes(('95910',1),('95911',1)),selection='neck_arm')['complete']
    assert not cash.quote(codes(('95910',1),('93000',1)),selection='neck_arm')['complete']
    assert not cash.quote(rows,selection='neck_arm',alternatives=True)['complete']


@pytest.mark.parametrize('units,amount',[(100,120000),(155,161250),(200,195000)])
def test_botox_session_once_plus_drug(units,amount):
    assert cash.quote(codes(('64615',1),('64642',1),('J0585',units)))['min_cents']==amount
    assert not cash.quote(codes(('64615',1)))['complete']
    assert not cash.quote(codes(('J0585',units)))['complete']


@pytest.mark.parametrize('code',['11104','11105','95812','95819','93000','99211'])
def test_pending_or_unlisted_is_not_a_final_quote(code):
    assert not cash.quote(codes((code,1)))['complete']


def test_schedule_is_tenant_and_date_scoped_and_overrides_old_cash_only():
    assert cash.schedule(cash.PRACTICE_ID,'2026-09-25')
    assert cash.schedule('other-practice','2026-09-25') is None
    assert cash.schedule(cash.PRACTICE_ID,'2026-09-03') is None
    result={'codes':codes(('95910',1),('95886',1)), 'available_codes':codes(('95910',1)),
            'price_range':{'lines':codes(('95910',1),('95886',1)), 'mode':'services', 'patient_max_cents':12345,
                           'collection':{'amount_cents':12345}, 'cash_max_cents':99}}
    original=deepcopy(result)
    cash.apply(result,'other-practice','2026-09-25',{'cash_selection':'back_leg'})
    assert result==original
    cash.apply(result,cash.PRACTICE_ID,'2026-09-25',{'cash_selection':'back_leg'})
    assert result['price_range']['cash_max_cents']==70000
    assert all(row['cash_included'] and row['cash_cents'] is None for row in result['price_range']['lines'])
    assert result['price_range']['patient_max_cents']==12345
    assert result['price_range']['collection']==original['price_range']['collection']
    cash.apply(result,cash.PRACTICE_ID,'2026-09-25',{})
    assert not result['price_range']['cash_complete'] and 'cash_max_cents' not in result['price_range']


def test_finish_previsit_cash_survives_missing_insurance_contract():
    inputs={'service_date':'2026-09-25','visit_type':'new','lines':[]}
    source={'active_coverages':0}
    result={'issues':[], 'benefits':{'network_status':'unknown','review_reasons':[]}}
    with patch.object(api.commercial_store,'list_connections',return_value=[]):
        api._finish_previsit(result,{},inputs,source,None,{'practice_id':cash.PRACTICE_ID},'')
    assert result['price_range']['cash_min_cents']==25000
    assert result['price_range']['cash_max_cents']==25000
    assert not result['price_range']['complete']
    assert result['price_range']['collection']['amount_cents'] is None
    assert {'J0585','95910','95886'} <= {row['code'] for row in result['available_codes']}


def test_incomplete_quantities_and_partial_components_need_review():
    assert not cash.quote([{'code':'99204','units':1,'modifiers':['26']}])['complete']
    assert not cash.quote(codes(('64615',1),('J0585',None)))['complete']
    assert not cash.quote(codes(('64615',1),('J0585',0)))['complete']
