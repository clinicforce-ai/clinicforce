"""Read PDF and image pages for cited V1 insurance evidence."""
import re

import fitz

from app.insurance import golden


def outlined_text(page) -> bool:
    """Spot repeated letter-sized filled curves that PDF text extraction cannot read."""
    shapes = []
    # Materialize the native drawing list before walking it. Some producer
    # generated PDFs have hundreds of vector glyph paths on a page.
    for path in list(page.get_drawings()):
        rect = path.get('rect')
        items = path.get('items') or []
        if (path.get('type') in {'f', 'fs'} and rect and 1 <= rect.height <= 20
                and 0 < rect.width < 30 and any(item and item[0] == 'c' for item in list(items))):
            shapes.append((float(rect.y0), float(rect.height), float(rect.width)))
    # ponytail: detect body text across several lines; a small isolated logo
    # stays digital. Original page links remain necessary for visual review.
    return len(shapes) >= 20 and len({int(y / 20) for y, _, _ in shapes}) >= 3


def read_pages(content: bytes, max_pages: int = 100, *, detect_outlined_text: bool = False,
               local_ocr_mode: str | None = None) -> list[tuple[int, str]]:
    def image_text(image: bytes) -> str:
        return golden._image_text(image) if local_ocr_mode is None else golden._image_text(image, local_mode=local_ocr_mode)

    if content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        pages = [(1, image_text(content))]
    elif content.startswith(b"%PDF"):
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass or not 1 <= document.page_count <= max_pages:
                    raise golden.GoldenCaseError(f"Upload an unlocked PDF with 1–{max_pages} pages.")
                pages = []
                for number, page in enumerate(document, 1):
                    text = page.get_text(sort=True).strip()
                    # Scanned cards can contain stale/invisible OCR text. Read the
                    # visible rendered page instead of merging two text sources.
                    # Contract PDFs can contain thousands of vector table
                    # elements. Their ordinary text is read directly, then the
                    # contract-specific outlined-text detector decides OCR.
                    # Avoid PyMuPDF's text-trace iterator for that layout.
                    scanned = False
                    if not detect_outlined_text:
                        for span in list(page.get_texttrace()):
                            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                                scanned = True
                                break
                    card_images = [item for item in page.get_image_info(xrefs=True)
                                   if fitz.Rect(item["bbox"]).get_area() >= page.rect.get_area() * .2]
                    has_card_image = bool(card_images)
                    scanned = scanned or (has_card_image and not re.search(r"(?:member|subscriber|name|\bid)\s*[:#]", text, re.I))
                    scanned = scanned or detect_outlined_text and outlined_text(page)
                    if not text or scanned:
                        # A portal export often places one card image on a
                        # mostly blank PDF page. OCR its original pixels, not
                        # the page header, whitespace, and adjacent columns.
                        image = card_images[0] if len(card_images) == 1 else None
                        if image and image.get("xref"):
                            text = image_text(document.extract_image(image["xref"])["image"])
                        else:
                            scale = min(2, golden.MAX_OCR_DIMENSION / max(page.rect.width, page.rect.height))
                            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                            text = image_text(pixmap.tobytes("png"))
                    pages.append((number, text))
        except (golden.GoldenCaseError, golden.OCRServiceError):
            raise
        except Exception as exc:
            raise golden.GoldenCaseError("This file could not be read. Upload a clear PDF, JPG or PNG.") from exc
    else:
        raise golden.GoldenCaseError("Upload a PDF, JPG or PNG file.")
    if not any(text.strip() for _, text in pages):
        raise golden.GoldenCaseError("No readable text was found. Upload a clearer scan or a searchable PDF.")
    return pages


def document_dates(pages: list[tuple[int, str]]) -> list[dict]:
    dates = []
    for page, text in pages:
        for line in text.splitlines():
            match = re.fullmatch(r"\s*(Date|Time Stamp|Transaction Date|Access Date|Effective Date|Contract Date|Coverage Start Date|Coverage End Date|Plan Start Date|Date of Service)\s*[:,-]\s*(.+)", line, re.I)
            if match and re.search(r"\d", match[2]) and len(match[2]) <= 100:
                dates.append({"label": match[1], "value": match[2].strip(), "page": page})
    return dates
