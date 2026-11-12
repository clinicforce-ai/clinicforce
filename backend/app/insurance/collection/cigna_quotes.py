"""Apply the supported Cigna refundable office quote rules in order."""

from app.insurance.cigna_clinic_quote import apply_collection
from app.insurance.collection.cigna_ppo_deductible_quote import apply as apply_ppo_quote


def apply(result: dict, guidance: dict, review: dict | None, assumptions: list[str], *, safe: bool) -> None:
    apply_ppo_quote(result, guidance, review, assumptions, safe=safe)
    apply_collection(result, guidance, review, assumptions, safe=safe)
