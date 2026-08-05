"""WeasyPrint rendering for doctor-facing, citation-linked clinical summaries."""

from __future__ import annotations

from copy import deepcopy
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
table { border-collapse: collapse; width: 100%; margin: 8px 0 18px; table-layout: fixed; }
th, td { border-bottom: 1px solid #aeb5bb; padding: 7px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
th { background: #e9edf0; font-weight: 700; } .metric { width: 17%; font-weight: 700; }
.history { width: 15%; } .recent { width: 18%; } .status { width: 25%; } .source { width: 10%; }
.muted { color: #59636d; font-style: italic; } a { color: #075985; text-decoration: underline; }
ul { margin: 4px 0 0; padding-left: 20px; }
</style></head><body>
<div class="patient-header"><h1>AI Clinical Summary</h1><div>{{ patient_label or 'Patient clinical record' }}</div></div>
<p class="narrative">{{ narrative }}</p>
<h2>Key Trend Data</h2>
<table><thead><tr><th class="metric">Metric</th><th class="history">Historical Value</th><th class="history">Historical Value</th><th class="recent">Most Recent Value</th><th class="status">Direction of Change / Status</th><th class="source">Source</th></tr></thead>
<tbody>{% for item in trend_data %}<tr><td class="metric">{{ item.name }}</td><td>{{ item.historical_one or '—' }}</td><td>{{ item.historical_two or '—' }}</td><td>{{ item.value }}{% if item.unit %} {{ item.unit }}{% endif %}</td><td>{{ item.details or '—' }}</td><td>{% if item.source_href %}<a href="{{ item.source_href }}">Source</a>{% else %}<span class="muted">Unavailable</span>{% endif %}</td></tr>{% else %}<tr><td colspan="6" class="muted">No trend data available.</td></tr>{% endfor %}</tbody></table>
<h2>Encounter Details</h2>
<table><thead><tr><th>Encounter / Item</th><th>Date</th><th>Details</th><th>Source</th></tr></thead><tbody>
{% for item in care_events %}<tr><td>{{ item.facility }}</td><td>{{ item.date or '—' }}</td><td>{{ item.details or '—' }}</td><td>{% if item.source_href %}<a href="{{ item.source_href }}">Source</a>{% else %}<span class="muted">Unavailable</span>{% endif %}</td></tr>{% endfor %}
{% for item in conditions %}<tr><td>Condition: {{ item.name }}</td><td>—</td><td>{{ item.details or '—' }}</td><td>{% if item.source_href %}<a href="{{ item.source_href }}">Source</a>{% else %}<span class="muted">Unavailable</span>{% endif %}</td></tr>{% endfor %}
{% for item in allergies %}<tr><td>Allergy: {{ item.name }}</td><td>—</td><td>{{ item.details or '—' }}</td><td>{% if item.source_href %}<a href="{{ item.source_href }}">Source</a>{% else %}<span class="muted">Unavailable</span>{% endif %}</td></tr>{% endfor %}
{% for item in medications %}<tr><td>Medication: {{ item.name }}</td><td>—</td><td>{{ item.dosage or '—' }}{% if item.details %}; {{ item.details }}{% endif %}</td><td>{% if item.source_href %}<a href="{{ item.source_href }}">Source</a>{% else %}<span class="muted">Unavailable</span>{% endif %}</td></tr>{% endfor %}
</tbody></table></body></html>"""


def _with_source_links(summary_data: dict[str, Any]) -> dict[str, Any]:
    rendered = deepcopy(summary_data)
    for section in ("vitals", "labs", "medications", "conditions", "allergies", "care_events"):
        for item in rendered.get(section, []):
            if not isinstance(item, dict):
                continue
            citation = item.get("citation")
            if isinstance(citation, dict) and isinstance(citation.get("document_id"), int) and isinstance(citation.get("page_number"), int):
                item["source_href"] = f"/api/documents/{citation['document_id']}#page={citation['page_number']}"
    rendered["trend_data"] = [*rendered.get("vitals", []), *rendered.get("labs", [])]
    return rendered


def generate_doctor_pdf(summary_data: dict, output_path: str = "/tmp/clinical_summary.pdf") -> str:
    """Render the report. Call this function in a worker thread from FastAPI."""
    data = _with_source_links(summary_data)
    html = Environment(autoescape=select_autoescape(default_for_string=True)).from_string(_TEMPLATE).render(**data)
    HTML(string=html).write_pdf(output_path)
    return output_path
