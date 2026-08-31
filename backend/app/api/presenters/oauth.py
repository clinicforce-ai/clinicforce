"""Safe same-origin OAuth completion responses."""

import json
from html import escape

from fastapi.responses import HTMLResponse


def popup_result(integration: str, connected: bool, public_base_url: str) -> HTMLResponse:
    """Notify an opener without exposing OAuth data in markup or JavaScript."""
    event = f"clinicforce:{integration}-{'connected' if connected else 'not-connected'}"
    label = "Epic" if integration == "epic" else "eClinicalWorks"
    detail = f"{label} is connected. This window can close." if connected else f"{label} authorization was not completed. This window can close."
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>ClinicForce</title></head>"
        f"<body><p>{escape(detail)}</p><script>"
        f"if (window.opener && !window.opener.closed) {{ window.opener.postMessage({json.dumps({'type': event})}, {json.dumps(public_base_url)}); window.close(); }}"
        "</script></body></html>"
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})
