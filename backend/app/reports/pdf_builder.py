"""WeasyPrint rendering for doctor-facing, citation-linked clinical summaries."""

from __future__ import annotations

from copy import deepcopy
from html import escape
from math import isfinite
from typing import Any

from jinja2 import Environment, select_autoescape
from weasyprint import HTML


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
@page { margin: 18mm; }
body { font-family: Arial, sans-serif; color: #20252b; font-size: 10pt; }
h1 { font-size: 18pt; margin: 0 0 8px; } h2 { font-size: 13pt; margin: 20px 0 8px; }
.patient-header { border-bottom: 2px solid #3b4045; padding-bottom: 8px; }
.narrative { line-height: 1.4; }
.narrative-provenance { color: #075985; font-size: 8.5pt; font-weight: 700; line-height: 1.35; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 18px; table-layout: fixed; }
th, td { border-bottom: 1px solid #aeb5bb; padding: 7px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #e9edf0; font-weight: 700; } .metric { width: 17%; font-weight: 700; }
.history { width: 15%; } .recent { width: 18%; } .status { width: 25%; } .source { width: 10%; }
.muted { color: #59636d; font-style: italic; } a { color: #075985; text-decoration: underline; }
ul { margin: 4px 0 0; padding-left: 20px; }
.trend-figure { border: 1px solid #b9d5df; background: #f8fcfd; margin: 8px 0 10px; padding: 8px; }
.trend-figure svg { display: block; width: 100%; height: auto; }
.trend-caption { margin: 6px 0 0; color: #59636d; font-size: 8.5pt; line-height: 1.35; }
</style></head><body>
    <div class="patient-header"><h1>Clinical Summary</h1><div>{{ patient_label or 'Patient clinical record' }}</div></div>
<p class="narrative">{{ narrative }}</p>
{% if narrative_sources %}<p class="narrative-provenance">Clinical overview sources: {{ narrative_sources | join('; ') }}</p>{% endif %}
<h2>Key Trend Data</h2>
{% if trend_chart_svg %}<div class="trend-figure">{{ trend_chart_svg | safe }}</div><p class="trend-caption">Trend visualization for {{ trend_chart_name }}. Individual values and their original-document citations are listed in the table below.</p>{% endif %}
<table><thead><tr><th class="metric">Metric</th><th class="history">Historical Value</th><th class="history">Historical Value</th><th class="recent">Most Recent Value</th><th class="status">Direction of Change / Status</th><th class="source">Source</th></tr></thead>
<tbody>{% for item in trend_data %}<tr><td class="metric">{{ item.name }}</td><td>{{ item.historical_one or '—' }}</td><td>{{ item.historical_two or '—' }}</td><td>{{ item.value }}{% if item.unit %} {{ item.unit }}{% endif %}</td><td>{{ item.details or '—' }}</td><td>{{ item.source_label or 'Patient-entered' }}</td></tr>{% else %}<tr><td colspan="6" class="muted">No trend data available.</td></tr>{% endfor %}</tbody></table>
<h2>Encounter Details</h2>
<table><thead><tr><th>Encounter / Item</th><th>Date</th><th>Details</th><th>Source</th></tr></thead><tbody>
{% for item in care_events %}<tr><td>{{ item.facility }}</td><td>{{ item.date or '—' }}</td><td>{{ item.details or '—' }}</td><td>{{ item.source_label or 'Patient-entered' }}</td></tr>{% endfor %}
{% for item in conditions %}<tr><td>Condition: {{ item.name }}</td><td>—</td><td>{{ item.details or '—' }}</td><td>{{ item.source_label or 'Patient-entered' }}</td></tr>{% endfor %}
{% for item in allergies %}<tr><td>Allergy: {{ item.name }}</td><td>—</td><td>{{ item.details or '—' }}</td><td>{{ item.source_label or 'Patient-entered' }}</td></tr>{% endfor %}
{% for item in medications %}<tr><td>Medication: {{ item.name }}</td><td>—</td><td>{{ item.dosage or '—' }}{% if item.details %}; {{ item.details }}{% endif %}</td><td>{{ item.source_label or 'Patient-entered' }}</td></tr>{% endfor %}
</tbody></table>
{% if source_register %}<h2>Original Source Register</h2>
<table><thead><tr><th>Original PDF</th><th>Pages cited in this summary</th></tr></thead><tbody>
{% for source in source_register %}<tr><td>{{ source.filename }}</td><td>{{ source.pages }}</td></tr>{% endfor %}
</tbody></table>{% endif %}</body></html>"""


def _source_label(citation: Any) -> str | None:
    if not isinstance(citation, dict):
        return None
    filename = citation.get("source_filename")
    page = citation.get("page_number")
    if isinstance(filename, str) and filename.strip() and isinstance(page, int) and page > 0:
        return f"{filename.strip()} · p. {page}"
    return None


def _number(value: Any) -> float | None:
    """Use only directly numeric values in a plotted series (never parse clinical prose)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return numeric if isfinite(numeric) else None


def _trend_chart_svg(items: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Create a compact, deterministic SVG chart for the strongest numeric series.

    WeasyPrint renders inline SVG reliably, so a PDF chart does not need a browser,
    JavaScript runtime, or a second client-side charting dependency.  The cited table
    remains the source of truth; this chart is an aid for visual scanning only.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        numeric = _number(item.get("value"))
        name = item.get("name")
        if numeric is None or not isinstance(name, str) or not name.strip():
            continue
        unit = item.get("unit") if isinstance(item.get("unit"), str) else ""
        prepared = {**item, "numeric_value": numeric}
        groups.setdefault((name.strip(), unit.strip()), []).append(prepared)

    candidates = [
        ((name, unit), sorted(values, key=lambda item: str(item.get("date") or "")))
        for (name, unit), values in groups.items()
        if len(values) >= 4
    ]
    if not candidates:
        return None, None
    (name, unit), points = max(candidates, key=lambda item: len(item[1]))
    # Limit one report chart to the newest eight values.  The full cited history stays
    # in the table and source register below it.
    points = points[-8:]
    values = [point["numeric_value"] for point in points]
    low, high = min(values), max(values)
    if low == high:
        low -= 1
        high += 1
    padding = 36
    width, height = 660, 220
    plot_width, plot_height = width - (padding * 2), height - 70
    def x_at(index: int) -> float:
        return padding + (plot_width * index / max(len(points) - 1, 1))
    def y_at(value: float) -> float:
        return 24 + ((high - value) / (high - low) * plot_height)

    polyline = " ".join(f"{x_at(index):.1f},{y_at(value):.1f}" for index, value in enumerate(values))
    labels = []
    dots = []
    for index, point in enumerate(points):
        date = escape(str(point.get("date") or f"Record {index + 1}"))
        value = escape(str(point.get("value")))
        source = escape(_source_label(point.get("citation")) or "Patient-entered")
        x, y = x_at(index), y_at(point["numeric_value"])
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#178563"><title>{date}: {value} ({source})</title></circle>')
        labels.append(f'<text x="{x:.1f}" y="{height - 18}" text-anchor="middle" font-size="10" fill="#547084">{date}</text>')
    metric = escape(f"{name}{f' ({unit})' if unit else ''}")
    svg = (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{metric} trend with {len(points)} values">'
        f'<text x="{padding}" y="14" font-size="12" font-weight="700" fill="#173a4d">{metric}</text>'
        f'<line x1="{padding}" y1="{24 + plot_height:.1f}" x2="{width - padding}" y2="{24 + plot_height:.1f}" stroke="#aeb5bb" />'
        f'<line x1="{padding}" y1="24" x2="{padding}" y2="{24 + plot_height:.1f}" stroke="#aeb5bb" />'
        f'<text x="4" y="30" font-size="10" fill="#547084">{high:g}</text>'
        f'<text x="4" y="{24 + plot_height:.1f}" font-size="10" fill="#547084">{low:g}</text>'
        f'<polyline fill="none" stroke="#087ca7" stroke-width="3" points="{polyline}" />'
        f"{''.join(dots)}{''.join(labels)}</svg>"
    )
    return svg, f"{name}{f' ({unit})' if unit else ''}"


def _with_source_links(summary_data: dict[str, Any]) -> dict[str, Any]:
    rendered = deepcopy(summary_data)
    citations: dict[str, set[int]] = {}
    for section in ("vitals", "labs", "medications", "conditions", "allergies", "care_events"):
        for item in rendered.get(section, []):
            if not isinstance(item, dict):
                continue
            source_label = _source_label(item.get("citation"))
            if source_label:
                item["source_label"] = source_label
                filename, page = source_label.rsplit(" · p. ", 1)
                citations.setdefault(filename, set()).add(int(page))
    rendered["trend_data"] = [*rendered.get("vitals", []), *rendered.get("labs", [])]
    rendered["narrative_sources"] = [
        f"{filename} · p. {page}"
        for filename, pages in sorted(citations.items(), key=lambda item: item[0].casefold())
        for page in sorted(pages)
    ]
    rendered["source_register"] = [
        {"filename": filename, "pages": ", ".join(str(page) for page in sorted(pages))}
        for filename, pages in sorted(citations.items(), key=lambda item: item[0].casefold())
    ]
    rendered["trend_chart_svg"], rendered["trend_chart_name"] = _trend_chart_svg(rendered["trend_data"])
    return rendered


def generate_doctor_pdf(summary_data: dict, output_path: str = "/tmp/clinical_summary.pdf") -> str:
    """Render the report. Call this function in a worker thread from FastAPI."""
    data = _with_source_links(summary_data)
    html = Environment(autoescape=select_autoescape(default_for_string=True)).from_string(_TEMPLATE).render(**data)
    HTML(string=html).write_pdf(output_path)
    return output_path
