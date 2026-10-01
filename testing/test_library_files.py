"""Original clinic files are readable, inert and linked only within their clinic."""
from io import BytesIO
from zipfile import ZipFile
from unittest.mock import patch

import pytest

from app.core import library_files
from app.insurance import commercial_store, nmct_cash


def word_file(text='Clinic cash prices &lt;script&gt;bad()&lt;/script&gt;'):
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>$250</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return data.getvalue()


def workbook_file():
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Clinic plan matrix" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/sharedStrings.xml', '<sst><si><t>Aetna</t></si></sst>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet><sheetData><row><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>Clinic rules &lt;img src=x onerror=bad()&gt;</t></is></c></row></sheetData></worksheet>')
    return data.getvalue()


def test_office_previews_preserve_text_tables_and_do_not_create_active_content():
    html = library_files.office_preview(word_file(), 'cash.docx')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<table>' in html and '$250' in html
    html = library_files.office_preview(workbook_file(), 'plans.xlsx')
    assert 'Clinic plan matrix' in html and '<td>Aetna</td><td></td>' in html
    assert '<img' not in html and '&lt;img' in html
    with pytest.raises(Exception):
        library_files.office_preview(b'not a zip', 'bad.docx')
    data = BytesIO(word_file())
    with ZipFile(data, 'a') as archive:
        archive.writestr('word/vbaProject.bin', b'macro')
    with pytest.raises(ValueError, match='macros'):
        library_files.office_preview(data.getvalue(), 'bad.docx')
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>')
    with pytest.raises(ValueError, match='XML'):
        library_files.office_preview(data.getvalue(), 'bad.docx')


def test_nmct_cash_source_link_requires_the_clinic_and_exact_file_digest():
    from unittest.mock import MagicMock
    db = MagicMock()
    db.__enter__.return_value = db
    db.execute.return_value.fetchall.return_value = []
    documents = [{'id': 'matching', 'sha256': nmct_cash.SOURCE_SHA256}, {'id': 'different', 'sha256': 'changed'}]
    with patch.object(commercial_store, '_connection', return_value=db), patch.object(commercial_store, 'list_connections', return_value=[]), patch.object(commercial_store, 'list_rate_versions', return_value=[]):
        own = library_files.estimator_links(nmct_cash.PRACTICE_ID, documents)['links']
        other = library_files.estimator_links('different-clinic', documents)['links']
    assert own['matching'][0]['status'] == 'active'
    assert own['different'] == [] and other['matching'] == []
