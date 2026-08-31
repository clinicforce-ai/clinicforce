"""Merging of independently extracted clinical facts."""

import json


def merge_pdf_extractions(extractions: list[dict]) -> dict[str, list[dict]]:
    """Combine per-file results without duplicate facts."""
    merged: dict[str, list[dict]] = {"conditions": [], "allergies": [], "medications": []}
    seen: dict[str, set[str]] = {key: set() for key in merged}
    for extraction in extractions:
        for key, items in merged.items():
            values = extraction.get(key, [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                marker = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if marker not in seen[key]:
                    seen[key].add(marker)
                    items.append(item)
    return merged
