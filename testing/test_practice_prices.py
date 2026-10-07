"""Document prices remain explicit, versioned and isolated between practices."""
import io
import os
from uuid import uuid4
from zipfile import ZipFile
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest
import fitz
from app.core import patient_store, staff_store
from app.insurance import commercial, practice_prices

CSV = b'CPT,Description,Cash Price\n99202-99205,Consultation,$250\n95910,Nerve studies,$700\n'


def test_explicit_prices_from_csv_word_and_pdf_tables():
    rows = practice_prices.read_rows(CSV, 'clinic.csv')
    assert [r['code'] for r in rows] == ['99202', '99203', '99204', '99205', '95910']
    assert rows[0]['cash_cents'] == 25000
    data = io.BytesIO()
    with ZipFile(data, 'w') as file:
        file.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>' + ''.join('<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>' for c in row) + '</w:tr>' for row in [('CPT','Description','Cash Price'), ('99203','Consultation','$250')]) + '</w:tbl></w:body></w:document>')
    assert practice_prices.read_rows(data.getvalue(), 'clinic.docx') == [rows[1]]
    with fitz.open() as document:
        page = document.new_page()
        for x in [40, 130, 320, 440]:
            page.draw_line((x, 40), (x, 100))
        for y in [40, 70, 100]:
            page.draw_line((40, y), (440, y))
        for y, row in [(60, ['CPT', 'Description', 'Cash Price']), (90, ['99203', 'Consultation', '$250'])]:
            for x, value in zip([45, 135, 325], row): page.insert_text((x, y), value, fontsize=10)
        assert practice_prices.read_rows(document.tobytes(), 'clinic.pdf') == [rows[1]]
    for bad in (b'CPT,Cash Price\n99203,=5*3', b'CPT,Cash Price\n99203/95910,700', b'CPT,Cash Price\n99203,100\n99203,200', b'CPT,Description,Cash Price\n99203,Proposed,100', b'\xff'):
        with pytest.raises(commercial.CommercialError): practice_prices.read_rows(bad, 'bad.csv')


def test_cash_never_changes_insurance_or_sums_alternative_services():
    result = {'available_codes': [], 'price_range': {'mode': 'options', 'patient_max_cents': 1234, 'lines': [{'code':'99203','units':1}, {'code':'99204','units':1}]}}
    config = {'id':'version', 'filename':'Prices.csv', 'effective_from':'2026-09-01', 'rows':practice_prices.read_rows(CSV, 'prices.csv')}
    practice_prices.apply(result, config)
    assert result['price_range']['cash_max_cents'] == 25000
    assert result['price_range']['patient_max_cents'] == 1234
    result['price_range']['lines'].append({'code':'99999','units':1})
    practice_prices.apply(result, config)
    assert not result['price_range']['cash_complete'] and 'cash_max_cents' not in result['price_range']


@pytest.mark.skipif(not os.getenv('STAFF_TEST_DATABASE_URL'), reason='Requires disposable PostgreSQL')
def test_schedule_activation_and_effective_dates_are_practice_scoped():
    url = os.environ['STAFF_TEST_DATABASE_URL']; schema = 'cash_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with patch.object(patient_store, 'DATABASE_URL', make_conninfo(url, options=f'-csearch_path={schema}')), patch.object(patient_store, '_schema_initialized', False), patch.object(staff_store, '_schema_initialized', False):
            draft = practice_prices.create('clinic-a', 'admin-a', CSV, 'clinic.csv', '2026-09-01')
            assert practice_prices.current('clinic-a', '2026-09-28') is None
            with pytest.raises(commercial.CommercialError): practice_prices.activate('clinic-b', draft['id'])
            practice_prices.activate('clinic-a', draft['id'])
            assert practice_prices.current('clinic-b', '2026-09-28') is None
            assert practice_prices.current('clinic-a', '2026-08-31') is None
            assert practice_prices.current('clinic-a', '2026-09-28')['rows'][0]['cash_cents'] == 25000
            future = practice_prices.create('clinic-a', 'admin-a', CSV.replace(b'$250', b'$275'), 'new.csv', '2027-01-01')
            practice_prices.activate('clinic-a', future['id'])
            assert practice_prices.current('clinic-a', '2026-09-28')['id'] == draft['id']
            assert practice_prices.current('clinic-a', '2027-01-01')['id'] == future['id']
    finally:
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.parametrize('practice_id', ['clinic-a', 'f378bc13-af1d-4ea5-bfbd-8cd102e42ddc'])
@pytest.mark.parametrize('amount', [17321, 28109, None])
def test_previsit_uses_active_schedule_without_clinic_defaults(practice_id, amount):
    from app.api.routers import commercial_estimator as api
    inputs = {'service_date': '2026-10-07', 'visit_type': 'new', 'lines': []}
    source = {'active_coverages': 0, 'self_pay_recorded': True}
    # Recalculation must discard the old built-in schedule, including saved prices.
    result = {'issues': [], 'benefits': {}, 'cash_quote': {'complete': True, 'min_cents': 25000, 'max_cents': 25000},
              'cash_schedule': {'source': 'old embedded schedule'}, 'collection_policy': 'defer_original_medicare_secondary'}
    config = {'id': 'version', 'filename': 'approved.csv', 'effective_from': '2026-01-01',
              'rows': [{'code': str(c), 'cash_cents': amount, 'description': 'Office visit'} for c in range(99202, 99206)]} if amount is not None else None
    with patch.object(api.commercial_store, 'list_connections', return_value=[]), \
         patch.object(api.practice_prices, 'current', return_value=config) as current, \
         patch.object(api.cpt_suggester.boto3, 'client', side_effect=RuntimeError('offline test')):
        api._finish_previsit(result, {}, inputs, source, None, {'practice_id': practice_id}, '')
    current.assert_called_once_with(practice_id, '2026-10-07')
    assert result['payment_mode'] == 'cash'
    assert 'cash_schedule' not in result and 'collection_policy' not in result
    assert result['price_range'].get('cash_min_cents') == amount
    assert result['collection_guidance']['amount_cents'] is None  # No insured amount.
    if amount is not None:
        assert result['cash_quote']['min_cents'] == amount
        assert result['practice_cash_source']['version'] == 'version'
    else:
        assert not result['price_range']['cash_complete']
