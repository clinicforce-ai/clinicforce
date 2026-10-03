"""Standalone SMART-on-FHIR launch against a live Epic org, end to end.

Walks the full authorization-code + PKCE flow: opens the org's authorize page
in your browser, catches the redirect on localhost:5000, trades the code for an
access token, then pulls the signed-in patient's Patient resource and vital-sign
Observations.

    python "data flow v3/test_houston.py"

The FHIR base and client id come from the environment so the same script works
against the Epic sandbox and against a production org:

    $env:EPIC_CLIENT_ID = "<your production client id>"
    $env:EPIC_FHIR_BASE = "https://epicproxy.et0922.epichosted.com/FHIRProxy/HM/api/FHIR/R4"

Everything else (authorize URL, token URL) is discovered from the server's
.well-known/smart-configuration, so no endpoint is hardcoded per org.

Output goes to ./real_data/epic/<patient-id>/ as raw FHIR JSON.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import secrets
import ssl
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import requests

# Houston Methodist's FHIR proxy. Override for sandbox:
#   https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
FHIR_BASE = os.environ.get(
    "EPIC_FHIR_BASE",
    "https://epicproxy.et0922.epichosted.com/FHIRProxy/HM/api/FHIR/R4",
).rstrip("/")
 
# "Testapp" production id. This is the app Houston Methodist has actually
# downloaded, verified by probing HM's authorize endpoint: this id gets a login
# page, while test_v2's production id (4fec48d3-967e-4ea2-a582-7f741f1d997b)
# gets the same generic "OAuth2 Error" page as a deliberately invalid UUID.
# Sandbox non-production id for this app: f79cc71c-b8a4-4b64-839a-4c8076e7953e
CLIENT_ID = os.environ.get("EPIC_CLIENT_ID", "4fec48d3-967e-4ea2-a582-7f741f1d997b")

# Must match the Endpoint URI registered in open.epic as an exact string. The
# open.epic form pins a literal https:// prefix, so the registered value is
# https (not http) even for localhost -- hence the TLS listener below.
REDIRECT_URI = os.environ.get("EPIC_REDIRECT_URI", "https://localhost:8000/callback")

# Derived, never hardcoded: a listener bound to a different port than the one
# in the registered redirect URI hangs forever waiting for a callback that
# arrives somewhere else.
CALLBACK_PORT = urllib.parse.urlparse(REDIRECT_URI).port or 443
CERT_DIR = Path(__file__).parent / ".localhost_cert"

# Everything one patient's chart can contain. Epic silently grants only the
# subset the app is registered for and reports the rest as refused, so asking
# for more than is currently registered costs nothing and means this needs no
# edit once the missing .Search APIs are added in open.epic.
#
# offline_access is requested for the refresh token; HM refuses it today, which
# is why an expired run needs a fresh browser login.
SCOPES = os.environ.get(
    "EPIC_SCOPES",
    " ".join(
        [
            "openid",
            "fhirUser",
            "offline_access",
            "patient/Patient.read",
            "patient/AllergyIntolerance.read",
            "patient/Condition.read",
            "patient/Device.read",
            "patient/DocumentReference.read",
            "patient/Observation.read",
            "patient/MedicationRequest.read",
            "patient/Immunization.read",
            "patient/Procedure.read",
            "patient/DiagnosticReport.read",
            "patient/Encounter.read",
            "patient/CarePlan.read",
            "patient/Goal.read",
        ]
    ),
)

DEST = Path(__file__).parent / "real_data" / "epic"


def discover(fhir_base: str) -> dict:
    """Fetch the SMART configuration so endpoints are never hardcoded per org."""
    url = f"{fhir_base}/.well-known/smart-configuration"
    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def make_pkce() -> tuple[str, str]:
    """Return (verifier, S256 challenge). Epic requires PKCE for public clients."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-shot handler that captures ?code= off the redirect and stops."""

    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.result = {k: v[0] for k, v in query.items()}

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _CallbackHandler.result
        body = "Authorized. Return to the terminal." if ok else "Authorization failed."
        self.wfile.write(f"<html><body><h2>{body}</h2></body></html>".encode())

    def log_message(self, *args) -> None:
        """Silence the default stderr access log."""


def ensure_cert() -> tuple[Path, Path]:
    """Self-signed cert for localhost, generated once and reused.

    Epic's registered redirect is https, so the loopback listener has to speak
    TLS. The browser will warn that the cert is untrusted -- that is expected;
    click through. Nothing sensitive rides on this cert: it only ever protects a
    hop from the browser to a socket on the same machine.
    """
    cert_path, key_path = CERT_DIR / "cert.pem", CERT_DIR / "key.pem"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"Generated a self-signed localhost cert in {CERT_DIR}")
    return cert_path, key_path


def server_ssl_context() -> "ssl.SSLContext":
    """A genuine server-side SSLContext, even if truststore is injected.

    `pip_system_certs` ships a .pth that swaps ssl.SSLContext for truststore's
    client-oriented shim process-wide. That shim runs peer-certificate
    verification inside wrap_socket() regardless of server_side=True, so a
    server socket built from it dies with a confusing AttributeError. Recover
    the stdlib class truststore saved when it injected itself.
    """
    cls = ssl.SSLContext
    if cls.__module__.endswith("truststore._api"):
        module = sys.modules[cls.__module__]
        cls = getattr(module, "_original_SSLContext", cls)
    return cls(ssl.PROTOCOL_TLS_SERVER)


def wait_for_callback(expected_state: str) -> str:
    """Block until Epic redirects back, then return the authorization code."""
    server = HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)

    if REDIRECT_URI.startswith("https://"):
        cert_path, key_path = ensure_cert()
        ctx = server_ssl_context()
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Listening on {REDIRECT_URI} ...")
    while not _CallbackHandler.result:
        thread.join(0.5)
    server.shutdown()
    # shutdown() only stops the serve_forever loop; without server_close() the
    # socket stays bound and the port is still held after the script finishes,
    # so the next run cannot bind it.
    server.close_request(server.socket)
    server.server_close()

    result = _CallbackHandler.result
    if "error" in result:
        sys.exit(f"Epic returned an error: {result.get('error')} "
                 f"- {result.get('error_description', 'no description')}")
    if result.get("state") != expected_state:
        sys.exit("state mismatch - possible CSRF, aborting")
    return result["code"]


def exchange_code(token_url: str, code: str, verifier: str) -> dict:
    """Trade the one-time code for an access token (public client, no secret)."""
    resp = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if not resp.ok:
        sys.exit(f"token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()


# A resource the app is not permitted to search is an expected, permanent state
# of the current registration, not an incident. Printing a stack of 403s every
# run buries the one line that matters (what *did* come back), so failures are
# recorded quietly and summarised in the status table instead. Set EPIC_VERBOSE=1
# to see the raw HTTP detail while debugging.
VERBOSE = os.environ.get("EPIC_VERBOSE", "") not in ("", "0", "false")

# path -> short reason, for the end-of-run status table.
notes: dict[str, str] = {}


def fhir_get(path: str, token: str, **params) -> dict | None:
    """GET one FHIR resource. Returns None on failure; never raises, never exits.

    A 403 means the token was fine but the org declined this resource -- either
    a scope Epic did not grant, or an API not enabled for the app. Epic usually
    returns an empty body for those, so WWW-Authenticate carries what detail
    there is.
    """
    try:
        resp = requests.get(
            f"{FHIR_BASE}/{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params or None,
            timeout=30,
        )
    except requests.RequestException as exc:
        notes[path] = f"network error: {type(exc).__name__}"
        return None

    if resp.ok:
        return resp.json()

    if resp.status_code == 403:
        notes[path] = "not permitted for this app"
    elif resp.status_code == 400:
        notes[path] = "search not supported as issued"
    else:
        notes[path] = f"HTTP {resp.status_code}"

    if VERBOSE:
        print(f"  ! {path} {resp.status_code} {resp.reason}", file=sys.stderr)
        if "WWW-Authenticate" in resp.headers:
            print(f"    {resp.headers['WWW-Authenticate']}", file=sys.stderr)
        print(f"    body: {resp.text.strip()[:400] or '(empty)'}", file=sys.stderr)
    return None


def report_operation_outcome(path: str, outcome: dict) -> None:
    """Record Epic's explanation for a partial or empty search result."""
    for issue in outcome.get("issue", []):
        text = (issue.get("details") or {}).get("text", "")
        detail = issue.get("diagnostics") or text
        if not detail or "returns no results" in text:
            continue
        # "Unknown parameter: ORG" is Epic-internal proxy noise, not about us.
        if "Unknown parameter" in detail:
            continue
        if "not authorized" in detail:
            notes[path] = "partially restricted - some records withheld"
        if VERBOSE:
            print(f"    note ({path}): {detail}", file=sys.stderr)


def fetch_all_pages(path: str, token: str, **params) -> list[dict]:
    """Follow Bundle.link[rel=next] so we get the whole result set, not page 1."""
    entries: list[dict] = []
    bundle = fhir_get(path, token, **params)
    if bundle is None:
        return entries
    while True:
        for entry in bundle.get("entry", []):
            resource = entry.get("resource")
            if not resource:
                continue
            # Epic returns a 200 with an OperationOutcome inside the bundle to
            # explain a partial or empty result -- most importantly when the
            # client may Read a sub-resource but not Search it, in which case
            # those records are silently dropped from the results. Surface that
            # rather than counting the explanation as if it were a record.
            if resource.get("resourceType") == "OperationOutcome":
                report_operation_outcome(path, resource)
                continue
            entries.append(resource)
        nxt = next(
            (l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"),
            None,
        )
        if not nxt:
            return entries
        resp = requests.get(
            nxt,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if not resp.ok:
            print(f"  ! pagination stopped at {resp.status_code}", file=sys.stderr)
            return entries
        bundle = resp.json()


# Granted resources that a ?patient= search does not apply to. Location is a
# facility, not a patient record -- Epic rejects the search param outright, so
# these are reachable only by following references out of other resources.
NOT_PATIENT_SEARCHABLE = {"Location", "Organization", "Practitioner", "Medication"}

# Extra query params per resource, one dict per separate search to run. Epic
# rejects a bare Condition?patient= search: category is mandatory, and each
# category maps to a distinct registered API ("Problems" and "Outside Record
# Health Concern" here), so they have to be queried separately and combined.
RESOURCE_PARAMS: dict[str, list[dict]] = {
    "Condition": [
        {"category": "problem-list-item"},
        {"category": "health-concern"},
        {"category": "encounter-diagnosis"},
    ],
    # Observation has the same mandatory-category rule, and each category is a
    # separately registered Epic API -- vitals can be granted while labs are not.
    "Observation": [
        {"category": "vital-signs"},
        {"category": "laboratory"},
        {"category": "social-history"},
        {"category": "core-characteristics"},
    ],
}


def patient_scoped_resources(granted: str) -> list[str]:
    """Resource types this token can search by patient, from the granted scopes.

    Handles both SMART v1 (patient/Condition.read) and v2 (patient/Condition.rs)
    syntax, and drops Patient itself -- an instance read, not a patient search.
    """
    found = []
    for scope in granted.split():
        if not scope.startswith("patient/") or "." not in scope:
            continue
        resource = scope[len("patient/") :].split(".", 1)[0]
        if resource in ("Patient", "*") or resource in NOT_PATIENT_SEARCHABLE:
            continue
        if resource not in found:
            found.append(resource)
    return found


CACHE = DEST / ".token_cache.json"


def save_tokens(tokens: dict) -> None:
    """Cache the token with the wall-clock time it was issued.

    Epic reports expires_in (seconds from issue), not an absolute time, so the
    issue time has to be recorded or a cached token can never be aged.
    """
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps({**tokens, "_fetched_at": time.time()}, indent=2), encoding="utf-8"
    )


def load_cached_tokens(token_url: str) -> dict | None:
    """Reuse a cached token, refreshing it if possible. None if a login is due.

    Saves the browser round trip on every re-run within the token's lifetime,
    which matters because the interactive leg is the slow part of this script.
    """
    if not CACHE.exists():
        return None
    try:
        tokens = json.loads(CACHE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    age = time.time() - tokens.get("_fetched_at", 0)
    remaining = tokens.get("expires_in", 0) - age
    if remaining > 60:
        print(f"Reusing cached token ({remaining / 60:.0f} min left).")
        return tokens

    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Cached token expired and no refresh token - signing in again.")
        return None

    print("Cached token expired, refreshing ...")
    resp = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CLIENT_ID,
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"  refresh failed ({resp.status_code}) - signing in again.")
        return None
    refreshed = resp.json()
    # Epic may omit the refresh token on renewal; keep the existing one so the
    # chain does not break on the following run.
    refreshed.setdefault("refresh_token", refresh)
    refreshed.setdefault("patient", tokens.get("patient"))
    save_tokens(refreshed)
    return refreshed


def authorize_interactively(authorize_url: str, token_url: str) -> dict:
    """Full browser leg: authorize, catch the redirect, exchange the code."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)  # random per-run, not a fixed constant

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "aud": FHIR_BASE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{authorize_url}?{urllib.parse.urlencode(params)}"

    print("Opening your browser to sign in. If it does not open, paste this:\n")
    print(url + "\n")
    webbrowser.open(url)

    code = wait_for_callback(state)
    print("Got authorization code, exchanging for token ...")
    tokens = exchange_code(token_url, code, verifier)
    save_tokens(tokens)
    return tokens


def report_scopes(granted: str) -> None:
    """Show requested vs granted. A refused scope is a registration problem."""
    print("\nScopes requested vs granted:")
    for scope in SCOPES.split():
        mark = "ok " if scope in granted.split() else "NOT GRANTED"
        print(f"  [{mark:>11}] {scope}")
    extra = [s for s in granted.split() if s not in SCOPES.split()]
    if extra:
        print(f"  (also granted: {' '.join(extra)})")


def attempts_for(resource: str) -> list[dict]:
    """Query variants to try for a resource, most specific first.

    Epic makes category mandatory on some resources and rejects it on others,
    and which applies depends on the org's registration rather than the spec.
    Rather than encode a guess, try the categorised searches and always fall
    back to a bare patient search -- whichever the server accepts, wins.
    """
    variants = list(RESOURCE_PARAMS.get(resource, []))
    if {} not in variants:
        variants.append({})
    return variants


def collect(patient_id: str, token: str, granted: str) -> dict[str, list[dict]]:
    """Every resource this token can actually retrieve, keyed by resource type.

    Missing resources are normal here: the app is registered for a subset of
    APIs, so most requests are expected to come back refused or empty. Nothing
    in this function treats that as an error.
    """
    collected: dict[str, list[dict]] = {}

    # Patient is an instance read, not a search, so it is handled separately --
    # and its scope is distinct from the patient context carried in the token.
    if any(s.startswith("patient/Patient.") for s in granted.split()):
        patient = fhir_get(f"Patient/{patient_id}", token)
        if patient:
            collected["Patient"] = [patient]
        else:
            # Re-key the note from the instance path to the bare type so the
            # status table can find it alongside the searched resources.
            notes["Patient"] = notes.pop(f"Patient/{patient_id}", "no records")

    for resource in patient_scoped_resources(granted):
        records: list[dict] = []
        seen: set[str] = set()
        for extra in attempts_for(resource):
            for record in fetch_all_pages(
                resource, token, patient=patient_id, **extra
            ):
                # Categories overlap in Epic (a problem can also be an encounter
                # diagnosis), so the same resource can arrive from two searches.
                rid = record.get("id")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                records.append(record)
            if records:
                break  # a variant worked; no need to try the looser ones
        if records:
            collected[resource] = records
    return collected


def status_table(granted: str, collected: dict[str, list[dict]]) -> None:
    """One line per resource: what came back, or why nothing did."""
    rows = []
    if any(s.startswith("patient/Patient.") for s in granted.split()):
        rows.append("Patient")
    rows.extend(patient_scoped_resources(granted))

    print(f"\n  {'resource':<22} {'count':>6}   status")
    for resource in rows:
        records = collected.get(resource, [])
        if records:
            status = notes.get(resource, "ok")
        else:
            status = notes.get(resource, "no records")
        print(f"  {resource:<22} {len(records):>6}   {status}")


def write_record(out: Path, collected: dict[str, list[dict]], granted: str) -> None:
    """Write per-type files plus one searchable FHIR Bundle and a summary."""
    out.mkdir(parents=True, exist_ok=True)

    for resource, records in collected.items():
        (out / f"{resource}.json").write_text(
            json.dumps(records, indent=2), encoding="utf-8"
        )

    # A collection Bundle is the portable form: one file that any FHIR tool can
    # read, rather than a directory whose layout is private to this script.
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "entry": [
            {"resource": r} for records in collected.values() for r in records
        ],
    }
    (out / "bundle.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")

    # The counts alone cannot distinguish "patient has no allergies" from "we
    # were not allowed to look", so record what was authorized next to them.
    summary = {
        "retrieved_at": bundle["timestamp"],
        "fhir_base": FHIR_BASE,
        "client_id": CLIENT_ID,
        "granted_scopes": sorted(granted.split()),
        "refused_scopes": sorted(set(SCOPES.split()) - set(granted.split())),
        "counts": {k: len(v) for k, v in collected.items()},
        "total_resources": len(bundle["entry"]),
        # Kept out of the console but not discarded: this is what distinguishes
        # "the patient has none" from "we were not allowed to look".
        "notes": dict(notes),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> None:
    print(f"FHIR base : {FHIR_BASE}")
    print(f"Client id : {CLIENT_ID}\n")

    config = discover(FHIR_BASE)
    authorize_url = config["authorization_endpoint"]
    token_url = config["token_endpoint"]
    print(f"authorize : {authorize_url}")
    print(f"token     : {token_url}\n")

    tokens = load_cached_tokens(token_url) or authorize_interactively(
        authorize_url, token_url
    )

    access_token = tokens["access_token"]
    patient_id = tokens.get("patient")
    if not patient_id:
        sys.exit("no patient context in token response - was this a patient login?")
    print(f"Patient context: {patient_id}")

    granted = tokens.get("scope", "")
    if VERBOSE:
        report_scopes(granted)

    print("Fetching ...")
    collected = collect(patient_id, access_token, granted)
    status_table(granted, collected)

    out = DEST / patient_id
    write_record(out, collected, granted)

    total = sum(len(v) for v in collected.values())
    print(f"\n{total} resources across {len(collected)} types -> {out}")
    if not total:
        print(
            "Empty result. summary.json records which scopes were granted and"
            " refused.\nRun with EPIC_VERBOSE=1 for the underlying HTTP detail."
        )


if __name__ == "__main__":
    main()
