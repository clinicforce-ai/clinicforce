"""Headless retrieval of a complete patient record from an Epic FHIR R4 server.

`test_epic.py` is a Flask app whose job is the interactive OAuth dance: it opens
a browser, the patient logs in at Epic, and it writes tokens to disk. This file
is the other half — no browser, no Flask. It picks up those saved tokens,
refreshes them when stale, and pulls every resource type the server will give us.

    # one-time, interactive: get tokens
    python test_epic.py

    # then, repeatable and headless:
    python retrieve_patient.py
    python retrieve_patient.py --out "../../../data flow v3/synthetic/out/live01/epic"
    python retrieve_patient.py --patient erXuFYUfucBZaryVksYEcMg3 --dry-run

Output is written in the same shape the merge pipeline already consumes
(`<out>/fhir/fhir_<ResourceType>.json`), so a real Epic pull can be dropped in
beside the synthetic sources and merged without changing anything downstream.

Credentials are read from `.env` and `export/tokens.json`; neither is ever
printed. See the SECURITY note at the bottom of this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    sys.exit("needs `requests` and `python-dotenv`: pip install requests python-dotenv")

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

def _find_token_file() -> Path:
    """Locate tokens.json.

    test_epic.py resolves DATA_DIR relative to the *current working directory*
    when DATA_DIR is set, so the file lands wherever the Flask app happened to
    be launched from -- commonly one level up from this script. Rather than
    guess a single path and fail with "no tokens" while the file sits two
    directories away, search the plausible locations and say which one was used.
    """
    canonical = Path(os.getenv("DATA_DIR") or (HERE / "export")) / "tokens.json"
    if canonical.exists():
        return canonical

    # Do NOT silently fall back to a token found elsewhere. An old file left in
    # a sibling directory (and repeatedly restored by OneDrive) will be picked
    # up ahead of a login that never happened, producing warnings about a dead
    # client instead of the truth: there is no current token. Point at the
    # stray file, but refuse to use it.
    for stray in (Path.cwd() / "export" / "tokens.json",
                  HERE.parent / "export" / "tokens.json"):
        if stray.exists() and stray != canonical:
            print(f"note: ignoring a token file outside the expected location\n"
                  f"      {stray}\n"
                  f"      delete it to avoid confusion; the login writes to\n"
                  f"      {canonical}\n")
            break
    return canonical


TOKEN_FILE = _find_token_file()
DATA_DIR = TOKEN_FILE.parent

SANDBOX_FHIR_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

# What to ask for, and with which parameters. Epic rejects an Observation
# search without a category, so each category is a separate query -- this is the
# single most common reason a naive "fetch everything" returns empty.
RESOURCE_QUERIES: list[tuple[str, str, dict]] = [
    ("Patient",             "Patient",            {}),
    ("AllergyIntolerance",  "AllergyIntolerance", {}),
    ("Condition",           "Condition",          {}),
    ("MedicationRequest",   "MedicationRequest",  {}),
    ("Immunization",        "Immunization",       {}),
    ("Procedure",           "Procedure",          {}),
    ("Encounter",           "Encounter",          {}),
    ("DocumentReference",   "DocumentReference",  {}),
    ("DiagnosticReport",    "DiagnosticReport",   {}),
    ("CarePlan",            "CarePlan",           {"category": "assess-plan"}),
    ("CareTeam",            "CareTeam",           {"status": "active"}),
    ("Goal",                "Goal",               {}),
    ("Device",              "Device",             {}),
    ("Coverage",            "Coverage",           {}),
    ("Observation-laboratory",    "Observation", {"category": "laboratory"}),
    ("Observation-vital-signs",   "Observation", {"category": "vital-signs"}),
    ("Observation-social-history", "Observation", {"category": "social-history"}),
    ("Observation-core-characteristics", "Observation",
     {"category": "core-characteristics"}),
]

MAX_PAGES = 50          # a runaway paginator is a bug, not a big patient
TIMEOUT = 45
RETRIES = 3


# --------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------


def load_tokens() -> dict:
    if not TOKEN_FILE.exists():
        sys.exit(
            f"no tokens at {TOKEN_FILE}\n"
            "Run `python test_epic.py`, complete the Epic login in the browser, "
            "then re-run this script."
        )
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


def save_tokens(tokens: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")


def discover(fhir_base: str) -> tuple[str, str]:
    """SMART configuration tells us where to authorise and refresh."""
    url = fhir_base.rstrip("/") + "/.well-known/smart-configuration"
    r = requests.get(url, headers={"Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    cfg = r.json()
    return cfg["authorization_endpoint"], cfg["token_endpoint"]


def is_expired(tokens: dict) -> bool:
    """True if the access token is gone or about to be.

    test_epic.py writes `expires_at` as a float epoch; this script writes ISO
    after a refresh. Accept both rather than treating an unparseable value as
    expired -- that silently forces a refresh that may be impossible.
    """
    exp = tokens.get("expires_at")
    if not exp:
        return True
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=60)
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(float(exp), timezone.utc) <= cutoff
    try:
        parsed = datetime.fromisoformat(str(exp))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= cutoff
    except ValueError:
        try:
            return datetime.fromtimestamp(float(exp), timezone.utc) <= cutoff
        except (TypeError, ValueError):
            return True


def refresh(tokens: dict) -> dict:
    """Exchange the refresh token for a new access token.

    Epic patient-facing apps are PKCE *public* clients: there is no client
    secret, so client_id alone identifies us here.
    """
    rt = tokens.get("refresh_token")
    if not rt or rt in ("None", "null"):
        sys.exit(
            "This token file has no refresh_token, so it cannot be renewed.\n"
            "The granted scope did not include `offline_access` - Epic only issues a\n"
            "refresh token when that scope is both requested AND approved.\n"
            "Re-run test_epic.py and approve the offline-access prompt."
        )

    _, token_url = discover(tokens["fhir_base"])
    r = requests.post(
        token_url,
        data={"grant_type": "refresh_token", "refresh_token": rt,
              "client_id": tokens["client_id"]},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        sys.exit(f"refresh failed ({r.status_code}). Re-run test_epic.py to re-authorise.")

    new = r.json()
    tokens["access_token"] = new["access_token"]
    tokens["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(new.get("expires_in", 3600)))
    ).isoformat()
    # Epic may rotate the refresh token; keep the old one if it doesn't.
    if new.get("refresh_token"):
        tokens["refresh_token"] = new["refresh_token"]
    save_tokens(tokens)
    return tokens


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------


class Forbidden(Exception):
    """Epic returned 403: the app registration lacks this API, not the server."""


class Session:
    def __init__(self, tokens: dict):
        self.tokens = tokens
        self.http = requests.Session()
        self.calls = 0

    @property
    def base(self) -> str:
        return self.tokens["fhir_base"].rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens['access_token']}",
                "Accept": "application/fhir+json"}

    def get(self, url: str, params: dict | None = None,
            resource_hint: str | None = None) -> dict | None:
        """One GET with refresh-on-401, backoff on 429/5xx.

        Returns None for 403/404 — Epic answers that way for resource types a
        given endpoint does not expose, which is a normal outcome, not an error.
        """
        for attempt in range(RETRIES):
            self.calls += 1
            r = self.http.get(url, headers=self._headers(), params=params, timeout=TIMEOUT)

            if r.status_code == 200:
                return r.json()

            if r.status_code == 401 and attempt == 0:
                self.tokens = refresh(self.tokens)
                continue

            # 403 and 404 mean different things and must not be collapsed.
            # 404 = this server does not expose the resource type at all.
            # 403 = it does, but THIS app is not authorised for it -- a
            # registration problem the operator can actually fix. Reporting
            # both as "not-supported" hides the actionable one.
            if r.status_code == 403:
                raise Forbidden(resource_hint or url)
            if r.status_code == 404:
                return None

            if r.status_code == 429 or r.status_code >= 500:
                wait = int(r.headers.get("Retry-After") or (2 ** attempt) * 2)
                time.sleep(min(wait, 30))
                continue

            # 400 usually means an unsupported search parameter. Surface Epic's
            # own OperationOutcome text rather than a bare status code.
            detail = ""
            try:
                oo = r.json()
                detail = "; ".join(
                    i.get("diagnostics", "") for i in oo.get("issue", []))[:180]
            except Exception:
                detail = r.text[:180]
            raise RuntimeError(f"{r.status_code} on {url} — {detail}")
        raise RuntimeError(f"gave up after {RETRIES} attempts: {url}")

    def search(self, resource_type: str, params: dict) -> tuple[list[dict], str]:
        """Search one resource type, following Bundle.link[next] to the end."""
        url = f"{self.base}/{resource_type}"
        first = self.get(url, params, resource_hint=resource_type)
        if first is None:
            return [], "not-supported"

        resources, warnings, pages = [], [], 0
        bundle = first
        while bundle and pages < MAX_PAGES:
            for entry in bundle.get("entry", []):
                res = entry.get("resource")
                if not res:
                    continue
                if res.get("resourceType") == "OperationOutcome":
                    # Epic returns these inline to explain a partial or empty
                    # result ("not authorized for this sub-resource"). Counting
                    # them as resources reports data we did not get.
                    for issue in res.get("issue", []):
                        note = issue.get("details", {}).get("text") or                                issue.get("diagnostics", "")
                        if note:
                            warnings.append(note)
                    continue
                resources.append(res)
            nxt = next((l["url"] for l in bundle.get("link", [])
                        if l.get("relation") == "next"), None)
            if not nxt:
                break
            pages += 1
            bundle = self.get(nxt)

        status = "ok" if resources else "empty"
        if pages >= MAX_PAGES:
            status = "truncated"
        return resources, status, warnings


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--patient", help="FHIR Patient id (default: from token file)")
    ap.add_argument("--out", help="output directory (default: export/<patient-id>/epic)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    tokens = load_tokens()
    print(f"tokens   : {TOKEN_FILE}")

    # A token minted by a different client_id than the one now configured is a
    # leftover from an earlier registration. It will fail with a confusing 401
    # rather than an obvious "wrong app", so say so up front.
    configured = os.getenv("EPIC_SANDBOX_CLIENT_ID") or os.getenv("EPIC_PROD_CLIENT_ID")
    if configured and tokens.get("client_id") and tokens["client_id"] != configured:
        print(
            f"\nWARNING: this token was issued to client {tokens['client_id']}\n"
            f"         but .env now configures  {configured}\n"
            f"         Re-run test_epic.py to mint a token for the current app.\n"
        )

    granted = tokens.get("scope", "")
    if granted and "offline_access" not in granted:
        print("WARNING: granted scope has no offline_access - no refresh token, "
              "so this access token cannot be renewed when it expires.\n")

    if is_expired(tokens):
        print("access token expired - refreshing")
        tokens = refresh(tokens)

    pid = args.patient or tokens.get("patient") or tokens.get("patient_id")
    if not pid:
        sys.exit("no patient id in the token file; pass --patient")

    sess = Session(tokens)
    print(f"endpoint : {sess.base}")
    print(f"patient  : {pid}\n")

    collected: dict[str, list[dict]] = {}
    receipts: dict[str, dict] = {}
    warned: set[str] = set()

    for label, rtype, extra in RESOURCE_QUERIES:
        params = dict(extra)
        # Patient is fetched by id; everything else is searched by patient ref.
        if rtype == "Patient":
            try:
                res = sess.get(f"{sess.base}/Patient/{pid}", resource_hint="Patient")
                resources = [res] if res else []
                status = "ok" if res else "empty"
            except Forbidden:
                resources, status = [], "FORBIDDEN"
        else:
            params["patient"] = pid
            try:
                resources, status, notes = sess.search(rtype, params)
                warned.update(notes)
            except Forbidden:
                resources, status = [], "FORBIDDEN"
            except RuntimeError as exc:
                resources, status = [], f"error: {exc}"

        collected[label] = resources
        receipts[label] = {"resourceType": rtype, "params": extra,
                           "count": len(resources), "status": status}
        flag = {"ok": " ok ", "empty": "  - ", "not-supported": " n/a",
                "truncated": " !! ", "FORBIDDEN": "403 "}.get(status, " ERR")
        print(f"  [{flag}] {label:<34} {len(resources):>5}"
              + ("" if status in ("ok", "empty", "not-supported", "truncated")
                 else f"  {status}"))

    total = sum(len(v) for v in collected.values())
    print(f"\n  {total} resources in {sess.calls} HTTP calls")

    forbidden = [k for k, v in receipts.items() if v["status"] == "FORBIDDEN"]
    if forbidden:
        print(f"\n  {len(forbidden)} resource types returned 403 FORBIDDEN:")
        print(f"    {', '.join(forbidden)}")
        print("\n  403 is a REGISTRATION problem, not a data problem. Granting the")
        print("  OAuth scope is NOT sufficient on its own -- in open.epic, open the")
        print("  app and enable each of these under 'Incoming APIs'. Epic returns")
        print("  the scope as granted but still refuses the call until the API")
        print("  itself is selected on the registration.")
    for w in sorted(warned):
        print(f"\n  Epic says: {w}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    dest = Path(args.out) if args.out else DATA_DIR / pid / "epic"
    fhir_dir = dest / "fhir"
    fhir_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for label, resources in collected.items():
        if not resources:
            # Skip empties, but they stay in the receipt. "Epic returned
            # nothing" and "we never asked" must remain distinguishable
            # downstream -- a doctor reading a short med list needs to know
            # which one it was.
            continue
        bundle = {
            "resourceType": "Bundle", "type": "searchset",
            "total": len(resources),
            "entry": [{"resource": r} for r in resources],
        }
        (fhir_dir / f"fhir_{label}.json").write_text(
            json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        written += 1

    (dest / "fetch_receipt.json").write_text(json.dumps({
        "patient": pid,
        "fhir_base": sess.base,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "http_calls": sess.calls,
        "total_resources": total,
        "by_query": receipts,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"\n  {written} bundles + fetch_receipt.json")
    print(f"\nFINAL:  {dest}")
    print(f"\nMerge alongside the synthetic sources with:")
    print(f'  python "data flow v3/synthetic/merge_pipeline.py" {dest.parent.name}')
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# SECURITY
#
# What is secret here, and what is not:
#
#   client_id      NOT secret. Epic patient apps are PKCE public clients — there
#                  is no client secret to leak. It identifies the app, nothing more.
#   access_token   Short-lived (~1 h). Bearer credential for one patient's record.
#   refresh_token  THE sensitive one. Long-lived, and exchangeable for access
#                  tokens until the patient revokes it in MyChart.
#
# Today `export/tokens.json` holds the refresh token in plaintext on disk,
# protected only by `.gitignore`. That is acceptable for a sandbox account with
# synthetic patients. It is NOT acceptable for real patient data: per
# SPIKE_patient_data_flow.md §3.5, production refresh tokens belong in RDS,
# encrypted with a per-patient KMS key, never on a developer's filesystem.
#
# This script never prints token values, and `--dry-run` lets you verify a pull
# without writing anything to disk.
# ---------------------------------------------------------------------------
