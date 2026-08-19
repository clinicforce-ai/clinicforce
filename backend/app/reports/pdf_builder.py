"""WeasyPrint rendering for doctor-facing, fact-level cited clinical summaries."""

from __future__ import annotations

from copy import deepcopy
from html import escape
from math import isfinite
from typing import Any

from jinja2 import Environment, select_autoescape
from weasyprint import HTML


_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
@page { margin: 16mm 15mm 18mm; }
body { font-family: Arial, sans-serif; color: #20252b; font-size: 9.5pt; line-height: 1.42; }
h1 { font-size: 18pt; margin: 0 0 4px; } h2 { color: #173a4d; font-size: 13pt; margin: 19px 0 7px; }
.patient-header { border-bottom: 2px solid #3b4045; padding-bottom: 8px; }
.narrative { margin: 16px 0 0; line-height: 1.55; }
table { border-collapse: collapse; width: 100%; margin: 7px 0 15px; table-layout: auto; }
th, td { border-bottom: 1px solid #b9c6cc; padding: 7px 8px; text-align: left; vertical-align: top; }
th { background: #e9f1f4; color: #173a4d; font-size: 8pt; font-weight: 700; letter-spacing: .02em; }
td { overflow-wrap: break-word; word-break: normal; }
td.fact-name { color: #173a4d; font-weight: 700; } .fact-name { width: 20%; }
.measure-value { width: 35%; } .source { width: 25%; }
td.source { font-size: 8.3pt; } a { color: #075985; font-weight: 700; text-decoration: underline; }
.patient-entered { color: #59636d; font-size: 8.3pt; font-style: italic; }
.trend-figure { break-inside: avoid; border: 1px solid #b9d5df; background: #f8fcfd; margin: 7px 0 8px; padding: 8px; }
.trend-figure svg { display: block; width: 100%; height: auto; }
.trend-caption { color: #59636d; font-size: 8.3pt; line-height: 1.35; margin: 5px 0 0; }
</style></head><body>
  <div class="patient-header"><h1>Clinical Summary</h1><div>{{ patient_label or 'Patient clinical record' }}</div></div>
  {% if narrative %}<p class="narrative">{{ narrative }}</p>{% endif %}
  {% if trend_chart_svg %}<h2>Documented trend</h2><div class="trend-figure">{{ trend_chart_svg | safe }}</div><p class="trend-caption">Trend visualization for {{ trend_chart_name }}. The cited measurements below are the clinical record.</p>{% endif %}
  {% for section in sections %}
    <h2>{{ section.title }}</h2>
    <table><thead><tr>{% for column in section.columns %}<th class="{{ column.css_class }}">{{ column.label }}</th>{% endfor %}</tr></thead><tbody>
      {% for row in section.rows %}<tr>{% for column in section.columns %}
        <td class="{{ column.css_class }}">{% if column.key == 'source' %}{% if row.source_url %}<a href="{{ row.source_url }}">{{ row.source_label }}</a>{% elif row.source_label %}{{ row.source_label }}{% else %}<span class="patient-entered">Patient-entered</span>{% endif %}{% else %}{{ row[column.key] }}{% endif %}</td>
      {% endfor %}</tr>{% endfor %}
    </tbody></table>
  {% endfor %}
</body></html>"""


def _source_label(citation: Any) -> str | None:
    if not isinstance(citation, dict):
        return None
    filename = citation.get("source_filename")
    page = citation.get("page_number")
    if isinstance(filename, str) and filename.strip() and isinstance(page, int) and page > 0:
        return f"{filename.strip()} · p. {page}"
    if citation.get("source_type") == "epic":
        organization = citation.get("organization_name")
        return f"Epic / MyChart · {organization.strip()}" if isinstance(organization, str) and organization.strip() else "Epic / MyChart"
    return None


def _number(value: Any) -> float | None:
    """Use only directly numeric values in a plotted series (never parse prose)."""
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
    """Create one compact chart when a single metric has four cited values."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in items:
        numeric = _number(item.get("value"))
        name = item.get("name")
        if numeric is None or not isinstance(name, str) or not name.strip():
            continue
        unit = item.get("unit") if isinstance(item.get("unit"), str) else ""
        groups.setdefault((name.strip(), unit.strip()), []).append({**item, "numeric_value": numeric})
    candidates = [
        ((name, unit), sorted(values, key=lambda item: str(item.get("date") or "")))
        for (name, unit), values in groups.items() if len(values) >= 4
    ]
    if not candidates:
        return None, None
    (name, unit), points = max(candidates, key=lambda item: len(item[1]))
    points = points[-8:]
    values = [point["numeric_value"] for point in points]
    low, high = min(values), max(values)
    if low == high:
        low -= 1
        high += 1
    padding, width, height = 36, 660, 220
    plot_width, plot_height = width - (padding * 2), height - 70
    x_at = lambda index: padding + (plot_width * index / max(len(points) - 1, 1))
    y_at = lambda value: 24 + ((high - value) / (high - low) * plot_height)
    polyline = " ".join(f"{x_at(index):.1f},{y_at(value):.1f}" for index, value in enumerate(values))
    dots, labels = [], []
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
        f'<polyline fill="none" stroke="#087ca7" stroke-width="3" points="{polyline}" />{("".join(dots))}{("".join(labels))}</svg>'
    )
    return svg, f"{name}{f' ({unit})' if unit else ''}"


def _display(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def _section(title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str, str]]) -> dict[str, Any] | None:
    """Create a table only when its clinical category actually has rows."""
    if not rows:
        return None
    visible_columns = []
    for key, label, css_class in columns:
        if key == "source":
            show = any(_source_label(row.get("citation")) for row in rows)
        elif key in {"name", "value", "facility"}:
            show = True
        else:
            show = any(row.get(key) not in (None, "") for row in rows)
        if show:
            visible_columns.append({"key": key, "label": label, "css_class": css_class})
    prepared = []
    for raw in rows:
        citation = raw.get("citation") if isinstance(raw.get("citation"), dict) else None
        prepared.append({
            **{key: _display(raw.get(key)) for key, _, _ in columns if key != "source"},
            "source_label": _source_label(citation),
            "source_url": citation.get("download_url") if citation else None,
        })
    return {"title": title, "columns": visible_columns, "rows": prepared}


def _report_sections(summary: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [
        _section("Clinical measurements", [*summary.get("vitals", []), *summary.get("labs", [])], [
            ("name", "Measurement", "fact-name"), ("value", "Result", "measure-value"),
            ("unit", "Unit", ""), ("date", "Date", ""), ("details", "Notes", ""), ("source", "Original source", "source"),
        ]),
        _section("Care visits", summary.get("care_events", []), [
            ("facility", "Facility", "fact-name"), ("date", "Date", ""), ("details", "Details", ""), ("source", "Original source", "source"),
        ]),
        _section("Conditions", summary.get("conditions", []), [
            ("name", "Condition", "fact-name"), ("details", "Details", ""), ("source", "Original source", "source"),
        ]),
        _section("Allergies", summary.get("allergies", []), [
            ("name", "Allergy or intolerance", "fact-name"), ("details", "Details", ""), ("source", "Original source", "source"),
        ]),
        _section("Medications", summary.get("medications", []), [
            ("name", "Medication", "fact-name"), ("dosage", "Dosage", ""), ("details", "Notes", ""), ("source", "Original source", "source"),
        ]),
    ]
    return [section for section in sections if section is not None]


def _render_data(summary_data: dict[str, Any]) -> dict[str, Any]:
    rendered = deepcopy(summary_data)
    trend_data = [*rendered.get("vitals", []), *rendered.get("labs", [])]
    rendered["sections"] = _report_sections(rendered)
    rendered["trend_chart_svg"], rendered["trend_chart_name"] = _trend_chart_svg(trend_data)
    return rendered


def generate_doctor_pdf(summary_data: dict[str, Any], output_path: str = "/tmp/clinical_summary.pdf") -> str:
    """Render a report with only populated sections and fact-level source links."""
    data = _render_data(summary_data)
    html = Environment(autoescape=select_autoescape(default_for_string=True)).from_string(_TEMPLATE).render(**data)
    HTML(string=html).write_pdf(output_path)
    return output_path


def render_doctor_pdf_bytes(
    summary_data: dict[str, Any], epic_bundle: dict[str, Any] | None = None,
    epic_source: dict[str, str] | None = None,
) -> bytes:
    """Render a report without creating a task-local file.

    ``epic_bundle`` and ``epic_source`` remain accepted for backwards-compatible
    callers, but Epic facts must now be included in ``summary_data`` alongside
    uploaded-document facts rather than rendered as a raw-resource appendix.
    """
    data = _render_data(summary_data)
    html = Environment(autoescape=select_autoescape(default_for_string=True)).from_string(_TEMPLATE).render(**data)
    result = HTML(string=html).write_pdf()
    if not isinstance(result, bytes):
        raise RuntimeError("WeasyPrint did not return PDF bytes")
    return result
