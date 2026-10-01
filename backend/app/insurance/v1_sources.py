"""Read V1 uploads and retain the exact page text behind every extracted field."""

import re
from datetime import date, datetime
from decimal import Decimal

import fitz

from app.insurance import golden


EXTRACTION_VERSION = "v1-upload-evidence-4"
MONEY = r"(?<![-\w])\$\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?(?![\d,.])"


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


def read_pages(content: bytes, max_pages: int = 100, *, detect_outlined_text: bool = False) -> list[tuple[int, str]]:
    if content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        pages = [(1, golden._image_text(content))]
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
                    has_card_image = False
                    for item in list(page.get_image_info()):
                        if fitz.Rect(item["bbox"]).get_area() >= page.rect.get_area() * .2:
                            has_card_image = True
                            break
                    scanned = scanned or (has_card_image and not re.search(r"(?:member|subscriber|name|\bid)\s*[:#]", text, re.I))
                    scanned = scanned or detect_outlined_text and outlined_text(page)
                    if not text or scanned:
                        scale = min(2, golden.MAX_OCR_DIMENSION / max(page.rect.width, page.rect.height))
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
                        text = golden._image_text(pixmap.tobytes("png"))
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


def card_fields(pages: list[tuple[int, str]]) -> tuple[dict, dict]:
    fields, evidence, conflicts = {}, {}, set()

    def add(key: str, value: str, page: int, quote: str):
        value = value.strip()
        if not value or key in conflicts:
            return
        if key in fields and fields[key] != value:
            fields.pop(key, None)
            evidence.pop(key, None)
            conflicts.add(key)
            return
        fields[key] = value
        evidence[key] = {"page": page, "quote": quote, "value": value}

    labels = {
        "member_name": r"(?:member|subscriber|patient|insured)\s+name|name",
        "payer": r"insurance(?: company)?|insurer|payer",
        "birth_date": r"(?:(?:member|subscriber|patient)\s+)?(?:date\s+of\s+birth|birth\s+date|dob)",
        "member_id": r"(?:member|subscriber|insured)\s*(?:id|number|no\.?)|identification\s+number|id",
        "group_number": r"group(?:\s*(?:number|no\.?))?",
        "plan_type": r"plan(?:\s*(?:type|/\s*product))?|product",
        "coverage_date": r"(?:coverage|effective)\s*date",
        "coinsurance": r"coinsurance",
    }
    for page, text in pages:
        lines = [" ".join(line.split()) for line in text.splitlines()]
        section = section_heading = ""
        for index, line in enumerate(lines):
            for key, label in labels.items():
                match = re.fullmatch(rf"(?:{label})(?:\s*[:#-]\s*|\s+)(.*)", line, re.I)
                label_only = re.fullmatch(rf"(?:{label})\s*[:#-]?", line, re.I)
                if not match and not label_only:
                    continue
                value, quote = match[1] if match else "", line
                if not value and index + 1 < len(lines) and ":" not in lines[index + 1]:
                    value = lines[index + 1]
                    if any(re.fullmatch(rf"(?:{candidate})", value, re.I) for candidate in labels.values()):
                        continue
                    quote += "\n" + value
                if key in {"member_id", "group_number"} and not re.fullmatch(r"[A-Z0-9-]{3,40}", value, re.I):
                    continue
                if key == "member_name" and not re.fullmatch(r"[A-Z][A-Z .,'-]{2,100}", value, re.I):
                    continue
                if key == "birth_date":
                    candidate = re.match(r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b", value)
                    if not candidate:
                        continue
                    for date_format in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                        try:
                            parsed = datetime.strptime(candidate[0], date_format).date()
                            break
                        except ValueError:
                            parsed = None
                    if not parsed or not date(1850, 1, 1) <= parsed <= date.today():
                        continue
                    value = parsed.isoformat()
                if key == "coinsurance" and not re.search(r"\d+\s*%", value):
                    continue
                if key == "coverage_date":
                    date_match = re.match(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", value)
                    if not date_match:
                        continue
                    value = date_match[0]
                add(key, value, page, quote)
            heading = re.search(r"\b(deductibles?|out[- ]of[- ]pocket(?: maximum| limit)?|oop(?: maximum| limit)?)(?: information)?\s*:?$", line, re.I)
            if heading:
                section = "deductible" if heading[1].lower().startswith("ded") else "oop_maximum"
                section_heading = line
                continue
            benefit = re.fullmatch(r"(in[- ]network|out[- ]of[- ]network)\s*(?:(deductible|out[- ]of[- ]pocket(?: maximum| limit)?|oop(?: maximum| limit)?)\s*)?[:#]\s*(.+)", line, re.I)
            if benefit:
                network = "in_network" if benefit[1].lower().startswith("in") else "out_of_network"
                kind = ("deductible" if benefit[2].lower().startswith("ded") else "oop_maximum") if benefit[2] else section
                value = benefit[3]
                # Keep the source wording. Reject partial numbers such as "$2, family".
                amounts = list(re.finditer(MONEY, value))
                if kind and amounts and len(amounts) == value.count("$"):
                    quote = line if benefit[2] else section_heading + "\n" + line
                    add(f"{network}_{kind}", value, page, quote)
            elif section and (pair := re.search(r"\bInd\s*/\s*Fam\s+(In\s+Network|Out\s+of\s+Network)\s+(\$[^\n]+)$", line, re.I)):
                value = pair[0]
                amounts = re.findall(MONEY, value)
                if len(amounts) == value.count("$") == 2:
                    network = "in_network" if pair[1].lower().startswith("in") else "out_of_network"
                    add(f"{network}_{section}", value, page, section_heading + "\n" + line)
            elif line:
                section = ""
        # Recognize printed insurer wordmarks, retaining the source quote.
        joined = " ".join(lines)
        blue = re.search(r"Blue\s*Cross\s*(?:and\s*)?Blue\s*Shield\s*(?:of\s+)?([A-Za-z]+)", joined, re.I)
        if blue and blue[1].casefold() in {"texas", "tx"}:
            add("payer", "Blue Cross Blue Shield of Texas", page, blue[0])
        elif match := re.search(r"\b(Aetna|Cigna|Humana|UnitedHealthcare)\b", joined, re.I):
            add("payer", match[0], page, match[0])
        # Routing/phone fields only: require the captured value in the same page.
        for key, pattern in golden.CARD_DETAIL_PATTERNS.items():
            if not (key.endswith("_phone") or key.startswith("rx_")):
                continue
            for line in lines:
                for match in re.finditer(pattern, line):
                    add(key, match[1], page, match[0])
    return fields, evidence


def individual_balance_cells(content: bytes) -> list[tuple[str, int, dict]]:
    """Read a labeled Individual table column without flattening Family into it."""
    if not content.startswith(b"%PDF"):
        return []
    results = []
    with fitz.open(stream=content, filetype="pdf") as document:
        for number, page in enumerate(document, 1):
            if any(span["type"] == 3 or span.get("opacity", 1) == 0 for span in page.get_texttrace()):
                continue  # Visible OCR, not an underlying text layer, is authoritative.
            for table in page.find_tables().tables:
                header = None
                for row, texts in zip(table.rows, table.extract()):
                    normalized = [str(value or "").strip().casefold() for value in texts]
                    if "individual" in normalized and "family" in normalized:
                        header = row.cells[normalized.index("individual")]
                        continue
                    if header is None:
                        continue
                    row_text = " ".join(str(value or "") for value in texts)
                    deductible = bool(re.search(r"\bdeductible\b", row_text, re.I))
                    oop = bool(re.search(r"out\s+of\s+pocket|\boop\b", row_text, re.I))
                    if deductible == oop:
                        continue
                    individual = page.get_text(clip=fitz.Rect(header[0], row.bbox[1], header[2], row.bbox[3]), sort=True).strip()
                    amounts = re.findall(rf"({MONEY})\s+Remaining\b", individual, re.I)
                    if len(amounts) != 1:
                        continue
                    value = int(Decimal(re.sub(r"[$,\s]", "", amounts[0])) * 100)
                    # Both pieces are literal text from the same row/column.
                    row_label = page.get_text(clip=fitz.Rect(row.bbox[0], row.bbox[1], header[0], row.bbox[3]), sort=True).strip()
                    results.append(("deductible_remaining_cents" if deductible else "oop_remaining_cents", value,
                                    {"page": number, "quote": row_label + "\nIndividual\n" + individual}))
    return results
