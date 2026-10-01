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


def test_contract_extraction_keeps_each_schedule_and_scanned_exhibit_with_page_evidence():
    import fitz
    from PIL import Image
    from app.core import object_store, staff_store
    from app.insurance import golden
    pdf = fitz.open()
    pdf.new_page().insert_text((40, 50), 'Commercial physician fee schedule\n99203 $100.36 <script>alert(1)</script>')
    pdf.new_page().insert_text((40, 50), 'Medicare Advantage physician fee schedule\n99205 $241.51')
    picture = BytesIO()
    Image.new('RGB', (500, 700), 'white').save(picture, format='PNG')
    page = pdf.new_page()
    page.insert_image(page.rect, stream=picture.getvalue())
    content = pdf.tobytes()
    document = {'id': 'doc', 'practice_id': 'clinic', 'object_key': 'private-source', 'label': '<img src=x>'}
    with patch.object(object_store, 'get_object_bytes', return_value=content), \
         patch.object(staff_store, 'save_library_extraction') as save, \
         patch.object(golden, '_image_text', return_value='Scanned exhibit: non-physician rates 85%') as ocr:
        result = library_files.extract_document(document)
    assert len(result['pages']) == 3 and result['pages'][2]['number'] == 3
    assert '$100.36' in result['pages'][0]['text'] and '$241.51' not in result['pages'][0]['text']
    assert '85%' in result['pages'][2]['text']
    ocr.assert_called_once()
    save.assert_called_once_with('doc', 'clinic', result)
    assert 'rates' not in result  # Source text is not an activated allowance.
    html = library_files.extraction_preview(document, result)
    assert '<script>' not in html and '<img' not in html and '&lt;script&gt;' in html
    assert 'document?preview=true#page=3' in html
    with patch.object(object_store, 'get_object_bytes') as read:
        assert library_files.extract_document({**document, 'sha256': result['sha256'], 'extraction': result}) == result
        read.assert_not_called()


def test_outlined_body_text_triggers_ocr_but_table_lines_do_not():
    import fitz
    from unittest.mock import Mock
    from app.insurance.v1_sources import outlined_text
    paths = [{'type': 'f', 'rect': fitz.Rect(40 + col * 8, 80 + line * 30, 46 + col * 8, 90 + line * 30),
              'items': [('c',)]} for line in range(3) for col in range(8)]
    page = Mock()
    page.get_drawings.return_value = paths
    assert outlined_text(page)
    page.get_drawings.return_value = [{**path, 'items': [('l',)]} for path in paths]
    assert not outlined_text(page)
    page.get_drawings.return_value = paths[:8]
    assert not outlined_text(page)
