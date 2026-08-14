"""Personal SMART-on-FHIR client for pulling your own Epic/MyChart record.

Runs against the Epic sandbox by default. Set EPIC_ENV=production plus the
production client ID and your health system's FHIR base URL to hit a real chart.

The point of this app is the export: after logging in, /export writes the full
record to a JSON file and stores the refresh token, so a downstream pipeline can
re-fetch later without a browser.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template_string, request, session, url_for

# Load the .env sitting next to this file, not one relative to the current
# working directory, so the app works no matter where it is launched from.
load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")

# --- Configuration ---------------------------------------------------------

EPIC_ENV = os.getenv("EPIC_ENV", "sandbox").lower()

SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

if EPIC_ENV == "production":
    CLIENT_ID = os.getenv("EPIC_PROD_CLIENT_ID")
    FHIR_BASE = os.getenv("EPIC_PROD_FHIR_BASE")
else:
    CLIENT_ID = os.getenv("EPIC_SANDBOX_CLIENT_ID")
    FHIR_BASE = os.getenv("EPIC_SANDBOX_FHIR_BASE", SANDBOX_FHIR_BASE)

REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/callback")

# Must match the Incoming APIs selected on your Epic app registration, or the
# authorize request is rejected.
SCOPES = [
    "openid",
    "fhirUser",
    "offline_access",
    "patient/Patient.read",
    "patient/Condition.read",
    "patient/AllergyIntolerance.read",
    "patient/Observation.read",
    "patient/MedicationRequest.read",
    "patient/Immunization.read",
]

# Resources pulled by /export, as (resource type, search params).
EXPORT_RESOURCES = [
    ("Condition", {"clinical-status": "active,inactive,resolved"}),
    ("AllergyIntolerance", {}),
    ("Observation", {"category": "laboratory"}),
    ("Observation", {"category": "vital-signs"}),
    ("MedicationRequest", {}),
    ("Immunization", {}),
]

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "export"))
TOKEN_FILE = DATA_DIR / "tokens.json"


# --- OAuth helpers ---------------------------------------------------------


def code_challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def discover_endpoints(fhir_base: str):
    """Resolve authorize/token URLs, preferring the org's SMART configuration."""
    try:
        res = requests.get(
            f"{fhir_base.rstrip('/')}/.well-known/smart-configuration", timeout=10
        )
        if res.ok:
            data = res.json()
            authorize = data.get("authorization_endpoint")
            token = data.get("token_endpoint")
            if authorize and token:
                return authorize, token
    except requests.RequestException as exc:
        print(f"SMART discovery failed, falling back to Epic URL pattern: {exc}")

    base = fhir_base.split("/api/FHIR")[0]
    return f"{base}/oauth2/authorize", f"{base}/oauth2/token"


def save_tokens(token_data: dict) -> None:
    """Persist tokens so a headless pipeline can refresh without a browser."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "env": EPIC_ENV,
        "fhir_base": FHIR_BASE,
        "client_id": CLIENT_ID,
        "token_url": session.get("token_url"),
        "patient_id": token_data.get("patient"),
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": time.time() + int(token_data.get("expires_in", 3600)),
        "scope": token_data.get("scope"),
    }
    TOKEN_FILE.write_text(json.dumps(record, indent=2), encoding="utf-8")


def refresh_access_token() -> bool:
    """Exchange the stored refresh token for a new access token."""
    refresh_token = session.get("refresh_token")
    token_url = session.get("token_url")
    if not refresh_token or not token_url:
        return False

    res = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if not res.ok:
        print(f"Refresh failed: {res.status_code} {res.text}")
        return False

    token_data = res.json()
    session["access_token"] = token_data["access_token"]
    session["expires_at"] = time.time() + int(token_data.get("expires_in", 3600))
    # Epic may rotate the refresh token; keep the newest one.
    if token_data.get("refresh_token"):
        session["refresh_token"] = token_data["refresh_token"]
    token_data.setdefault("patient", session.get("patient_id"))
    save_tokens(token_data)
    return True


def valid_access_token():
    """Return a usable access token, refreshing if it is close to expiring."""
    if not session.get("access_token"):
        return None
    if time.time() > session.get("expires_at", 0) - 60:
        if not refresh_access_token():
            return None
    return session["access_token"]


# --- FHIR helpers ----------------------------------------------------------


def fhir_get(path: str, token: str, params: dict = None):
    url = path if path.startswith("http") else f"{FHIR_BASE.rstrip('/')}/{path}"
    res = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
        params=params,
        timeout=30,
    )
    if not res.ok:
        print(f"FHIR {url} -> {res.status_code}: {res.text[:300]}")
        return None
    return res.json()


def fhir_search_all(resource_type: str, token: str, patient_id: str, params: dict):
    """Search a resource type, following pagination links to collect every entry."""
    query = {"patient": patient_id, **params}
    bundle = fhir_get(resource_type, token, query)
    resources = []

    while bundle:
        for entry in bundle.get("entry", []):
            if "resource" in entry:
                resources.append(entry["resource"])

        next_url = next(
            (l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"),
            None,
        )
        if not next_url:
            break
        bundle = fhir_get(next_url, token)

    return resources


# --- Templates -------------------------------------------------------------

INDEX_TEMPLATE = """
<!doctype html>
<title>Connect Health Record</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 40px; line-height: 1.6; }
  .card { border: 1px solid #ddd; padding: 24px; border-radius: 8px; max-width: 560px; }
  .env { background: #eef2ff; color: #3730a3; padding: 2px 8px; border-radius: 4px;
         font-size: 14px; }
  a.btn { display: inline-block; margin-top: 16px; background: #0066cc; color: #fff;
          padding: 10px 18px; border-radius: 4px; text-decoration: none; }
  code { background: #f4f4f4; padding: 1px 5px; border-radius: 3px; }
</style>
<div class="card">
  <h2>Connect Your Health Record</h2>
  <p>Environment: <span class="env">{{ env }}</span></p>
  <p>FHIR base: <code>{{ fhir_base }}</code></p>
  {% if not configured %}
    <p style="color:#b91c1c"><strong>Not configured.</strong>
       Set the client ID and FHIR base for this environment in <code>.env</code>.</p>
  {% else %}
    <a class="btn" href="{{ url_for('login') }}">Log in via MyChart</a>
  {% endif %}
</div>
"""

DASHBOARD_TEMPLATE = """
<!doctype html>
<title>Patient Dashboard</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 40px; line-height: 1.6; }
  pre { background: #f4f4f4; padding: 15px; border-radius: 5px; overflow-x: auto;
        max-height: 420px; }
  .badge { background: #e0f2fe; color: #0369a1; padding: 2px 8px; border-radius: 4px; }
  a.btn { display: inline-block; margin: 8px 8px 8px 0; background: #0066cc; color: #fff;
          padding: 8px 16px; border-radius: 4px; text-decoration: none; }
</style>
<h1>Connected Health Record</h1>
<p><strong>Patient ID:</strong> {{ patient_id }}</p>
<p><strong>Name:</strong> {{ name }}</p>
<p><strong>Gender:</strong> {{ gender }} &nbsp; <strong>Born:</strong> {{ birth_date }}</p>

<h2>Conditions</h2>
<ul>
{% for condition in conditions %}
  <li><span class="badge">Condition</span> {{ condition }}</li>
{% else %}
  <li>No conditions returned</li>
{% endfor %}
</ul>

<a class="btn" href="{{ url_for('export') }}">Export full record to JSON</a>
<a class="btn" href="{{ url_for('index') }}" style="background:#666">Disconnect</a>

<h3>Raw Patient resource</h3>
<pre>{{ patient_raw }}</pre>
"""

EXPORT_TEMPLATE = """
<!doctype html>
<title>Export Complete</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 40px; line-height: 1.6; }
  code { background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }
  li { margin: 4px 0; }
</style>
<h1>Export complete</h1>
<p>Written to <code>{{ path }}</code></p>
<ul>
{% for name, count in counts %}
  <li>{{ name }}: {{ count }}</li>
{% endfor %}
</ul>
<p><a href="{{ url_for('dashboard') }}">Back to dashboard</a></p>
"""


# --- Routes ----------------------------------------------------------------


@app.route("/")
def index():
    return render_template_string(
        INDEX_TEMPLATE,
        env=EPIC_ENV,
        fhir_base=FHIR_BASE or "(unset)",
        configured=bool(CLIENT_ID and FHIR_BASE),
    )


@app.route("/login")
def login():
    if not CLIENT_ID or not FHIR_BASE:
        return "Client ID or FHIR base URL is not configured for this environment", 500

    # The session cookie is scoped to the hostname in the address bar. If that
    # differs from the redirect URI's host (the classic localhost vs 127.0.0.1
    # trap), the cookie is not sent to /callback and the flow dies on a state
    # mismatch that looks like CSRF. Catch it here instead.
    expected_host = urlparse(REDIRECT_URI).netloc
    if request.host != expected_host:
        return (
            f"Host mismatch: you are browsing <code>{request.host}</code> but the "
            f"redirect URI is registered for <code>{expected_host}</code>. The session "
            f"cookie would be lost on the way back.<br><br>"
            f'Start over at <a href="http://{expected_host}/">http://{expected_host}/</a>.',
            400,
        )

    authorize_url, token_url = discover_endpoints(FHIR_BASE)

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    session["oauth_state"] = state
    session["code_verifier"] = verifier
    session["token_url"] = token_url

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "aud": FHIR_BASE,
        "code_challenge": code_challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    target = f"{authorize_url}?{urlencode(params)}"

    # Epic rejects the authorize request before any redirect back, so a failure
    # here leaves no trace in our logs at all -- the browser just lands on an
    # Epic error page. Print what we sent; `redirect_uri` and `client_id` must
    # match the open.epic registration byte for byte. Nothing here is secret:
    # the client_id is public and the PKCE challenge is single-use.
    print("\n[login] sending patient to Epic")
    print(f"[login]   authorize_url : {authorize_url}")
    print(f"[login]   client_id     : {CLIENT_ID}")
    print(f"[login]   redirect_uri  : {REDIRECT_URI}")
    print(f"[login]   aud           : {FHIR_BASE}")
    print(f"[login]   scope         : {' '.join(SCOPES)}")
    print(f"[login] full URL:\n{target}\n")
    return redirect(target)


@app.route("/callback")
def callback():
    if error := request.args.get("error"):
        return f"Authorization error: {request.args.get('error_description', error)}", 400

    if request.args.get("state") != session.get("oauth_state"):
        if not session.get("oauth_state"):
            return (
                "Session was empty at /callback, so the state could not be checked. "
                "This is almost always a cookie problem, not CSRF: make sure you "
                f"started the login at <code>{urlparse(REDIRECT_URI).netloc}</code> "
                "(not a different hostname), and that the server did not restart "
                "mid-login.",
                403,
            )
        return "State mismatch: the returned state does not match the session", 403

    # Every failure below happens BEFORE save_tokens(), so the symptom is always
    # the same -- "tokens.json was not created" -- while the cause differs. Echo
    # each one to the terminal so the reason is visible without reading the
    # browser page.
    def fail(where: str, detail: str, status: int = 400):
        print(f"\n[callback] FAILED at {where}\n           {detail}\n")
        return f"{where}: {detail}", status

    code = request.args.get("code")
    if not code:
        return fail("no authorization code",
                    f"Epic redirected back without ?code. Query string was: "
                    f"{dict(request.args)}")

    verifier = session.pop("code_verifier", None)
    if not verifier:
        return fail("missing PKCE verifier",
                    "The session cookie did not survive the round trip. Start at "
                    f"{REDIRECT_URI.rsplit('/', 1)[0]}/ and do not change hostname mid-flow.")

    print(f"[callback] got code, exchanging at {session.get('token_url')}")
    res = requests.post(
        session["token_url"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if not res.ok:
        return fail(f"token exchange rejected ({res.status_code})", res.text[:600])

    token_data = res.json()
    session["access_token"] = token_data.get("access_token")
    session["refresh_token"] = token_data.get("refresh_token")
    session["patient_id"] = token_data.get("patient")
    session["expires_at"] = time.time() + int(token_data.get("expires_in", 3600))

    # Token values are never printed; the shape of the response is what matters
    # when diagnosing a missing patient context or a missing refresh token.
    print(f"[callback] token OK. keys={sorted(token_data)}")
    print(f"[callback] patient={token_data.get('patient')!r}  "
          f"refresh_token={'yes' if token_data.get('refresh_token') else 'NO'}")
    print(f"[callback] granted scope: {token_data.get('scope')}")

    if not session["patient_id"]:
        return fail(
            "no patient context",
            "Epic returned a token with no `patient` field, so we do not know whose "
            "record this is. That means the app registration is not patient-facing: "
            "in open.epic set Application Audience to Patients and request the "
            "launch/patient scope. Response keys: " + ", ".join(sorted(token_data)),
        )

    save_tokens(token_data)
    print(f"[callback] wrote {TOKEN_FILE}\n")
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    token = valid_access_token()
    patient_id = session.get("patient_id")
    if not token or not patient_id:
        return redirect(url_for("index"))

    patient = fhir_get(f"Patient/{patient_id}", token) or {}
    conditions = fhir_search_all("Condition", token, patient_id, {})

    name = (patient.get("name") or [{}])[0]
    display_name = name.get("text") or " ".join(
        [*name.get("given", []), name.get("family", "")]
    ).strip()

    condition_names = []
    for resource in conditions:
        code = resource.get("code", {})
        condition_names.append(
            code.get("text")
            or (code.get("coding") or [{}])[0].get("display")
            or "Unknown condition"
        )

    return render_template_string(
        DASHBOARD_TEMPLATE,
        patient_id=patient_id,
        name=display_name or "Unknown",
        gender=patient.get("gender", "N/A"),
        birth_date=patient.get("birthDate", "N/A"),
        conditions=condition_names,
        patient_raw=json.dumps(patient, indent=2),
    )


@app.route("/export")
def export():
    """Pull every configured resource type and write one JSON file for the pipeline."""
    token = valid_access_token()
    patient_id = session.get("patient_id")
    if not token or not patient_id:
        return redirect(url_for("index"))

    record = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "env": EPIC_ENV,
        "fhir_base": FHIR_BASE,
        "patient_id": patient_id,
        "resources": {"Patient": [fhir_get(f"Patient/{patient_id}", token) or {}]},
    }

    for resource_type, params in EXPORT_RESOURCES:
        found = fhir_search_all(resource_type, token, patient_id, params)
        record["resources"].setdefault(resource_type, []).extend(found)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"record_{patient_id}.json"
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    counts = sorted((name, len(items)) for name, items in record["resources"].items())
    return render_template_string(EXPORT_TEMPLATE, path=out_path, counts=counts)


if __name__ == "__main__":
    print(f"Epic environment : {EPIC_ENV}")
    print(f"Client ID        : {CLIENT_ID or 'MISSING - check .env'}")
    print(f"FHIR base        : {FHIR_BASE or 'MISSING - check .env'}")
    print(f"Redirect URI     : {REDIRECT_URI}")

    # Derive the listening port from REDIRECT_URI instead of hardcoding it.
    # Epic matches the redirect URI exactly, so if the two ever disagree the
    # browser gets ERR_CONNECTION_REFUSED on one port while the server sits on
    # the other -- which looks like an Epic problem and is not.
    _parsed = urlparse(REDIRECT_URI)
    _port = _parsed.port or (443 if _parsed.scheme == "https" else 80)
    _host = _parsed.hostname or "localhost"

    # Epic matches redirect_uri byte for byte, scheme included. If the
    # registration says https, serving plain HTTP gets the request bounced to
    # /OAuth/Start?error=4 -- indistinguishable from an unregistered URI, and
    # it never reaches /callback so nothing appears in our logs. Serve TLS with
    # a self-signed cert so the scheme matches.
    _ssl = None
    if _parsed.scheme == "https":
        _ssl = "adhoc"
        print("TLS              : self-signed (ssl_context='adhoc')")
        print("                   the browser will warn once -- click through it")

    print(f"Listening on     : {_parsed.scheme}://{_host}:{_port}\n")
    app.run(host=_host, port=_port, debug=True, ssl_context=_ssl)
