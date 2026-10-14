"""Keep backend source files small enough to navigate in an editor."""

from pathlib import Path


BACKEND_APP = Path(__file__).resolve().parents[1] / "backend" / "app"
SOURCE_SUFFIXES = {".py", ".html", ".js", ".css"}
MAX_LINES = 500


def test_backend_application_source_files_stay_under_size_limit() -> None:
    oversized = []
    for path in BACKEND_APP.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > MAX_LINES:
            oversized.append(f"{path.relative_to(BACKEND_APP)}: {line_count} lines")
    assert not oversized, "Split large backend sources by responsibility:\n" + "\n".join(oversized)
