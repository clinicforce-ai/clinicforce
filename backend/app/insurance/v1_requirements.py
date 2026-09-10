"""Source-bound authorization/referral results; no payer-wide defaults."""
import re


def _status(line: str, kind: str) -> str | None:
    word = r"(?:prior\s+)?auth(?:orization)?|pre[- ]?authorization" if kind == "authorization" else r"referrals?"
    word = rf"(?:{word})"
    if re.search(rf"{word}\s+(?:required\s*)?[:=-]\s*(?:unknown|not available|not determined|pending)\b", line, re.I):
        return None
    negative = rf"\bno\s+{word}\s+(?:is\s+)?(?:required|needed)|\b{word}\s+(?:is\s+)?not\s+(?:required|needed)|\b{word}\s+required\s*[:=-]\s*no\b"
    positive = rf"\b{word}\s+(?:is\s+)?(?:required|needed)\b|\brequires?\s+(?:a\s+)?{word}\b"
    if re.search(negative, line, re.I):
        return "conditional" if re.search(r"\b(?:unless|except|if|when)\b", line, re.I) else "not_required"
    if re.search(positive, line, re.I):
        return "conditional" if re.search(r"\b(?:unless|except|if|when|where|generally|as applicable)\b", line, re.I) else "required"
    return None


def code_requirements(pages: list[tuple[int, str]]) -> list[dict]:
    """A result belongs only to its code block, including page continuations."""
    results, code, header, start_page, network = [], None, "", None, "Not specified"
    for page, text in pages:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line or line.startswith("http"):
                continue
            match = re.match(r"^(?:CPT(?:\s+code)?\s*[:#]?\s*)?(\d{5}|[A-Z]\d{4}|\d{4}[A-Z])\s*[-:]\s*(.+)", line, re.I)
            if match:
                code, header, start_page, network = match[1].upper(), line, page, "Not specified"
            elif re.match(r"^(?:CPT|HCPCS|\d{5}\b|[A-Z]\d{4}\b)", line, re.I):
                code = None  # An unparsed new code block must not inherit the previous code.
            elif re.match(r"^(?:Benefit Disclaimer|Benefit Descriptions|Disclaimer|\d+\.\d+\s)", line, re.I):
                code = None
            if not code:
                continue
            if re.search(r"\bout[- ]of[- ]network\b", line, re.I):
                network = "Out of network"
            elif re.search(r"\bin[- ]network\b", line, re.I):
                network = "In network"
            for kind in ("authorization", "referral"):
                status = _status(line, kind)
                if status:
                    reference = f"page {page}" if page == start_page else f"pages {start_page}–{page}"
                    results.append({"code": code, "kind": kind, "status": status, "network": network,
                                    "quote": header if line == header else header + "\n" + line,
                                    "reference": reference, "page": page})
    return results


def contract_terms(pages: list[tuple[int, str]]) -> list[dict]:
    """Display relevant clauses as clauses, not patient-specific determinations."""
    terms = []
    for page, text in pages:
        paragraphs = re.split(r"\n\s*\n|(?m:^\s*(?=\d+(?:\.\d+){1,4}\s))", text)
        for paragraph in paragraphs:
            quote = " ".join(paragraph.split())
            if not re.search(r"\b(?:referrals?|preauthorization|prior authorization)\b", quote, re.I):
                continue
            if not re.search(r"\b(?:required|requires?|must|only upon|shall|as applicable)\b", quote, re.I):
                continue
            terms.append({"quote": quote, "reference": f"page {page}", "page": page})
    return terms
