"""Read V1 uploads and retain the exact page text behind every extracted field."""

import re
from datetime import date, datetime
from decimal import Decimal

import fitz

from app.insurance import golden


EXTRACTION_VERSION = "v1-upload-evidence-4"
MONEY = r"(?<![-\w])\$\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?(?![\d,.])"


from app.insurance.v1_pages import outlined_text, read_pages, document_dates


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
        "member_name": r"(?:member|subscriber|patient|insured)\s+name|(?:member|subscriber|patient|insured)(?=\s*[:#-])|name",
        "payer": r"insurance(?: company)?|insurer|payer",
        "birth_date": r"(?:(?:member|subscriber|patient)\s+)?(?:date\s+of\s+birth|birth\s+date|dob)",
        "member_id": r"(?:member|subscriber|insured|enrollee)\s*(?:id|number|no\.?)|identification\s*(?:number|no\.?)|id",
        "group_number": r"group(?:\s*(?:number|no\.?))?",
        "plan_type": r"plan(?:\s*(?:type|/\s*product))?|product",
        "coverage_date": r"(?:coverage|effective)\s*date",
        "coinsurance": r"coinsurance",
        "specialist_copay": r"specialist(?:\s+(?:office|visit))?(?:\s+copay)?",
    }
    for page, text in pages:
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        section = section_heading = ""
        for index, line in enumerate(lines):
            if re.fullmatch(r"(?:Sample )?Member ID Cards?", line, re.I):
                continue
            # OCR often flattens a card's three-column header into one label
            # row followed by one value row. Accept only the labeled layout.
            following = lines[index + 1] if index + 1 < len(lines) else ""
            if re.search(r"\bPlan Type:.*\bMember ID:.*\bGroup #:", line, re.I):
                match = re.fullmatch(r"(Medical|Dental|Vision|Pharmacy)\s+([A-Z0-9-]{3,40})\s+([A-Z0-9-]{3,40})", following, re.I)
                if match:
                    for key, value in zip(("plan_type", "member_id", "group_number"), match.groups()):
                        add(key, value, page, line + "\n" + following)
                    continue
            elif re.fullmatch(r"Name:\s*[^A-Za-z0-9]*\s*Effective Dates?:", line, re.I):
                match = re.fullmatch(r"([A-Z][A-Z .,'-]{2,100})\s+\d{1,2}[/-]\d{1,2}[/-]\d{4}\s*[-–]\s*\d{1,2}[/-]\d{1,2}[/-]\d{4}", following, re.I)
                if match:
                    add("member_name", match[1], page, line + "\n" + following)
            elif re.fullmatch(r"Date of Birth:\s*Policy Status:", line, re.I):
                match = re.fullmatch(r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s+(?:Active|Inactive|Pending)", following, re.I)
                if match:
                    try:
                        birth = datetime.strptime(match[1].replace("-", "/"), "%m/%d/%Y").date()
                    except ValueError:
                        birth = None
                    if birth and date(1850, 1, 1) <= birth <= date.today():
                        add("birth_date", birth.isoformat(), page, line + "\n" + following)
            for key, label in labels.items():
                match = re.fullmatch(rf"(?:{label})(?:\s*[:#-](?:\s*[:#-])?\s*|\s+)(.*)", line, re.I)
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
                if key == "specialist_copay" and not re.fullmatch(MONEY, value):
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
        hpi = re.search(r"Health\s+Plans,?\s+Inc\.?\s*\(HPI\)", joined, re.I)
        if hpi:
            add("payer", "Health Plans Incorporated", page, hpi[0])
            for line in lines:
                if match := re.fullmatch(r"([A-Z][A-Z'-]+):\s*([A-Z][A-Z'-]+)", line, re.I):
                    add("member_name", f"{match[2]} {match[1]}", page, line)
        if not hpi:
            blue = re.search(r"Blue\s*Cross\s*(?:and\s*)?Blue\s*Shield\s*(?:of\s+)?([A-Za-z]+)", joined, re.I)
            if blue and blue[1].casefold() in {"texas", "tx"}:
                add("payer", "Blue Cross Blue Shield of Texas", page, blue[0])
            elif match := re.search(r"\bBCBSTX\b", joined, re.I):
                add("payer", "Blue Cross Blue Shield of Texas", page, match[0])
            elif match := re.search(r"\bUnited\s*Healthcare\b", joined, re.I):
                add("payer", "UnitedHealthcare", page, match[0])
            elif match := re.search(r"\b(Aetna|Cigna|Humana)\b", joined, re.I):
                add("payer", match[0], page, match[0])
        # Routing/phone fields only: require the captured value in the same page.
        for key, pattern in golden.CARD_DETAIL_PATTERNS.items():
            if not (key.endswith("_phone") or key.startswith("rx_")):
                continue
            for line in lines:
                for match in re.finditer(pattern, line):
                    add(key, match[1], page, match[0])
    return fields, evidence


def read_card_fields(contents: list[bytes]) -> tuple[dict, dict, list[int]]:
    """Read card pages and fill fields missed by the primary OCR layout."""
    pages, counts, offset = [], [], 0
    for content in contents:
        extracted = read_pages(content, 2)
        pages.extend((number + offset, text) for number, text in extracted)
        counts.append(len(extracted))
        offset += len(extracted)
    fields, evidence = card_fields(pages)
    has_birth_label = any(re.search(r"\b(?:DOB|Date of Birth|Birth Date)\b", text, re.I) for _, text in pages)
    for mode in ("6", "11"):
        if all(fields.get(key) for key in ("member_name", "member_id", "group_number", "payer")) and (fields.get("birth_date") or not has_birth_label):
            break
        try:
            alternate, offset = [], 0
            for content, count in zip(contents, counts):
                extracted = read_pages(content, 2, local_ocr_mode=mode)
                if len(extracted) != count:
                    raise golden.OCRServiceError("Card page count changed during OCR")
                alternate.extend((number + offset, text) for number, text in extracted)
                offset += count
            proposed, citations = card_fields(alternate)
            for key, value in proposed.items():
                if key not in fields:
                    fields[key], evidence[key] = value, citations[key]
        except (golden.GoldenCaseError, golden.OCRServiceError):
            continue  # The primary OCR evidence remains available for review.
    return fields, evidence, counts


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
