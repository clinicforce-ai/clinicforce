"""Safe download and archive filenames."""

from pathlib import Path
from urllib.parse import quote


def safe_pdf_filename(filename: str) -> str:
    """Normalize an untrusted filename to a simple PDF filename."""
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in Path(filename).name)
    safe_name = safe_name or "source.pdf"
    return safe_name if safe_name.lower().endswith(".pdf") else f"{safe_name}.pdf"


def download_headers(filename: str) -> dict[str, str]:
    """Prevent header injection and force PDF download."""
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_pdf_filename(filename))}",
        "Cache-Control": "no-store, private",
        "X-Content-Type-Options": "nosniff",
    }


def zip_download_headers() -> dict[str, str]:
    return {
        "Content-Disposition": "attachment; filename*=UTF-8''clinicforce-reports.zip",
        "Cache-Control": "no-store, private",
        "X-Content-Type-Options": "nosniff",
    }


def zip_entry_name(filename: str, used_names: set[str]) -> str:
    """Produce a unique safe PDF filename inside a ZIP archive."""
    safe_name = safe_pdf_filename(filename)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    candidate, counter = safe_name, 2
    while candidate.lower() in used_names:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    used_names.add(candidate.lower())
    return candidate
