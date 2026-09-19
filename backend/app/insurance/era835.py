"""Small, defensive X12 835 reader for staff reconciliation.

It intentionally returns only adjudication totals and claim-control identifiers;
the encrypted original is retained in the practice document store.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _cents(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("The ERA contains an invalid payment amount.") from exc
    if not amount.is_finite() or amount < 0 or amount > 100_000_000 or amount.as_tuple().exponent < -2:
        raise ValueError("The ERA contains an invalid payment amount.")
    return int(amount * 100)


def _segments(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Upload the original ASCII X12 835 ERA file.") from exc
    text = text.lstrip("\ufeff\r\n \t")
    if not text.startswith("ISA") or len(text) < 4:
        raise ValueError("Upload an X12 835 ERA beginning with ISA.")
    element = text[3]
    terminator = text[105] if len(text) > 105 and text[105] not in "\r\n" else "~" if "~" in text else "\n"
    rows = [[item.strip() for item in segment.strip().split(element)] for segment in text.split(terminator) if segment.strip()]
    if not rows or rows[0][0] != "ISA" or not any(row and row[0] == "GS" and len(row) > 1 and row[1] == "HP" for row in rows):
        raise ValueError("Upload a valid X12 health-care payment (835) file.")
    if not any(row and row[0] == "ST" and len(row) > 1 and row[1] == "835" for row in rows):
        raise ValueError("The X12 file is not an 835 ERA.")
    return rows


def parse(content: bytes) -> dict:
    """Parse final claim totals from one X12 835 interchange without logging PHI."""
    rows = _segments(content)
    payment, payment_date, payer, payer_id, claims = None, None, "", "", []
    for row in rows:
        tag = row[0]
        if tag == "BPR" and len(row) > 2:
            payment, payment_date = _cents(row[2]), row[16] if len(row) > 16 else ""
        elif tag == "N1" and len(row) > 1 and row[1] == "PR":
            payer, payer_id = (row[2] if len(row) > 2 else ""), (row[4] if len(row) > 4 else "")
        elif tag == "CLP":
            if len(row) < 8:
                raise ValueError("The ERA contains an incomplete claim payment segment.")
            claims.append({"patient_control_number": row[1], "status_code": row[2], "billed_cents": _cents(row[3]),
                           "insurer_paid_cents": _cents(row[4]), "patient_responsibility_cents": _cents(row[5]),
                           "payer_claim_control_number": row[7]})
    if payment is None or not claims:
        raise ValueError("The ERA does not contain a payment total and at least one claim payment.")
    return {"payment_cents": payment, "payment_date": payment_date, "payer": payer, "payer_id": payer_id,
            "claim_count": len(claims), "claims": claims}
