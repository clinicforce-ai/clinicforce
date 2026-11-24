"""Inspect a long generic patient PDF for card pages without OCRing plain text pages."""

from __future__ import annotations

import re
from collections.abc import Callable

import fitz

from app.insurance import golden


def verified_packet_cards(
    document: fitz.Document,
    card_text: Callable[[str], bool],
    card_images: Callable[[fitz.Page, str], list[dict]],
) -> list[tuple[int, bytes]]:
    ranked = []
    for number, page in enumerate(document):
        text = page.get_text(sort=True)
        images = page.get_image_info(xrefs=True)
        text_is_card = card_text(text)
        card_shape = any(
            item['height'] > 0 and 1.2 <= item['width'] / item['height'] <= 2.5
            for item in images
        )
        heading = bool(re.search(r'\b(?:member\s+id|insurance)\s+cards?\b', text, re.I))
        ranked.append((3 if text_is_card else 2 if heading else 1 if card_shape else 0,
                       number, text_is_card, bool(images)))

    cards = []
    for _, number, text_is_card, has_images in sorted(ranked, key=lambda row: (-row[0], row[1])):
        if not (text_is_card or has_images):
            continue
        page = document[number]
        with fitz.open() as page_pdf:
            page_pdf.insert_pdf(document, from_page=number, to_page=number)
            page_bytes = page_pdf.tobytes()
        if text_is_card:
            cards.append((number, page_bytes))
            continue
        page_text = golden._pdf_text(page_bytes)
        if card_text(page_text):
            cards.append((number, page_bytes))
            continue
        images = card_images(page, page_text)
        if images:
            with fitz.open() as card_pdf:
                for item in images:
                    card_page = card_pdf.new_page(width=item['width'], height=item['height'])
                    card_page.insert_image(card_page.rect,
                                           stream=document.extract_image(item['xref'])['image'])
                cards.append((number, card_pdf.tobytes()))
    return cards
