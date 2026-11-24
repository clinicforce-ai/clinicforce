"""Fast, content-bound classification for compact eCW card packets."""

import re

import fitz


def compact_card_packet(document: fitz.Document) -> bool:
    """Accept a short PDF only when its text and every page support a card scan."""
    if not 1 <= document.page_count <= 4:
        return False
    text = '\n'.join(page.get_text(sort=True) for page in document)
    member = re.search(r'\b(?:member\s*(?:id|number)|medicare\s*(?:number|no\.?))\b', text, re.I)
    plan = re.search(r'\b(?:health\s*plan|insurance|medicare|medicaid|pdp|rx\s*(?:bin|pcn))\b', text, re.I)
    return bool(member and plan and all(any(
        image['height'] > 0 and 1.2 <= image['width'] / image['height'] <= 2.5
        and fitz.Rect(image['bbox']).get_area() >= page.rect.get_area() * .03
        for image in page.get_image_info(xrefs=True)
    ) for page in document))


def generic_priority(mime: str, data: bytes) -> int:
    """Put compact card packets before OCR candidates and defer long PDFs."""
    if mime.startswith('image/'):
        return 2
    try:
        with fitz.open(stream=data, filetype='pdf') as document:
            if document.page_count > 4:
                return 0
            if compact_card_packet(document):
                return 3
            return 2 if any(
                1.2 <= image['width'] / image['height'] <= 2.5
                for page in document for image in page.get_image_info(xrefs=True)
                if image['height'] > 0
            ) else 1
    except (fitz.FileDataError, fitz.EmptyFileError, ValueError):
        return 1
