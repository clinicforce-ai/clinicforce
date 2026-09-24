"""Small practice-scoped persistence layer for the commercial estimator."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.patient_store import _connection
from app.insurance.commercial import CommercialError, RATE_SOURCE_TYPES, SERVICE_CATEGORIES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    value = dict(row)
    for key in ("aliases_json", "rules_json", "normalized_json"):
        if key in value:
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
    return value


def list_connections(practice_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute("SELECT * FROM commercial_payer_connections WHERE practice_id=%s ORDER BY display_name", (practice_id,)).fetchall()
    return [_row(row) for row in rows]


def connection(practice_id: str, connection_id: str) -> dict | None:
    with _connection() as db:
        row = db.execute("SELECT * FROM commercial_payer_connections WHERE id=%s AND practice_id=%s", (connection_id, practice_id)).fetchone()
    return _row(row)


def create_connection(practice_id: str, data: dict[str, Any]) -> dict:
    category = data.get("category", "commercial_medical")
    if category not in {"commercial_medical", "medicare", "medicaid_chip", "fep", "bluecard", "dental", "vision"}:
        raise CommercialError("Choose a supported payer category.")
    if category != "commercial_medical" and data.get("status") == "staff_guidance_enabled":
        raise CommercialError("Only commercial medical may be enabled in this release.")
    required = ("display_name", "stedi_payer_id")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise CommercialError("Payer name and immutable Stedi payer ID are required.")
    now, record_id = _now(), str(uuid4())
    with _connection() as db:
        db.execute("""INSERT INTO commercial_payer_connections
            (id,practice_id,display_name,stedi_payer_id,category,status,aliases_json,rules_json,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (record_id, practice_id, data["display_name"].strip(), data["stedi_payer_id"].strip(), category,
             data.get("status", "draft"), _json(data.get("aliases", [])), _json(data.get("rules", {})), now, now))
    return connection(practice_id, record_id) or {}


def list_networks(practice_id: str) -> list[dict]:
    with _connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM commercial_provider_networks WHERE practice_id=%s ORDER BY network_name", (practice_id,)).fetchall()]


def create_network(practice_id: str, data: dict[str, Any]) -> dict:
    required = ("payer_connection_id", "billing_npi", "rendering_npi", "organization_name", "tin", "location_id", "place_of_service", "network_name", "effective_from", "evidence_reference")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise CommercialError("Provider, location, network, effective date, and participation evidence are required.")
    if not (data["billing_npi"].isdigit() and len(data["billing_npi"]) == 10 and data["rendering_npi"].isdigit() and len(data["rendering_npi"]) == 10):
        raise CommercialError("Billing and rendering NPIs must each be 10 digits.")
    if not connection(practice_id, data["payer_connection_id"]):
        raise CommercialError("The selected payer connection is not in this practice.")
    now, record_id = _now(), str(uuid4())
    with _connection() as db:
        db.execute("""INSERT INTO commercial_provider_networks
          (id,practice_id,payer_connection_id,billing_npi,rendering_npi,organization_name,tin,location_id,place_of_service,plan_matcher,network_name,effective_from,effective_to,status,evidence_reference,created_at,updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (record_id, practice_id, data["payer_connection_id"], data["billing_npi"], data["rendering_npi"], data["organization_name"], data["tin"], data["location_id"], data["place_of_service"], data.get("plan_matcher", ""), data["network_name"], data["effective_from"], data.get("effective_to") or None, data.get("status", "draft"), data["evidence_reference"], now, now))
        row = db.execute("SELECT * FROM commercial_provider_networks WHERE id=%s AND practice_id=%s", (record_id, practice_id)).fetchone()
    return dict(row)


def list_profiles(practice_id: str) -> list[dict]:
    with _connection() as db:
        rows = db.execute("SELECT * FROM commercial_estimation_profiles WHERE practice_id=%s ORDER BY service_category", (practice_id,)).fetchall()
    return [_row(row) for row in rows]


def profile(practice_id: str, profile_id: str) -> dict | None:
    with _connection() as db:
        row = db.execute("SELECT * FROM commercial_estimation_profiles WHERE id=%s AND practice_id=%s", (profile_id, practice_id)).fetchone()
    return _row(row)


def create_profile(practice_id: str, data: dict[str, Any]) -> dict:
    if data.get("service_category") not in SERVICE_CATEGORIES:
        raise CommercialError("Only office and EMG/NCS profiles are enabled initially.")
    if not connection(practice_id, str(data.get("payer_connection_id") or "")):
        raise CommercialError("The payer connection is not in this practice.")
    with _connection() as db:
        network = db.execute("SELECT 1 FROM commercial_provider_networks WHERE id=%s AND practice_id=%s AND payer_connection_id=%s", (data.get("provider_network_id"), practice_id, data.get("payer_connection_id"))).fetchone()
    if not network:
        raise CommercialError("The provider network does not match the selected payer.")
    rules = data.get("rules") or {}
    if rules.get("cost_share") not in {"coinsurance", "copay", "cumulative", "payer_returned"}:
        raise CommercialError("Profile rules must select a cost-share method or payer_returned when only one applies.")
    now, record_id = _now(), str(uuid4())
    with _connection() as db:
        db.execute("""INSERT INTO commercial_estimation_profiles (id,practice_id,payer_connection_id,provider_network_id,service_category,plan_matcher,status,rules_json,created_at,updated_at)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (record_id, practice_id, data["payer_connection_id"], data["provider_network_id"], data["service_category"], data.get("plan_matcher", ""), data.get("status", "review_only"), _json(rules), now, now))
    return profile(practice_id, record_id) or {}


def list_rate_versions(practice_id: str) -> list[dict]:
    with _connection() as db:
        return [dict(row) for row in db.execute("SELECT * FROM commercial_rate_versions WHERE practice_id=%s ORDER BY created_at DESC", (practice_id,)).fetchall()]


def import_rates(practice_id: str, payer_connection_id: str, source_type: str, source_reference: str, digest: str, effective_from: str, effective_to: str | None, change_ceiling_bps: int, rows: list[dict]) -> dict:
    if source_type not in RATE_SOURCE_TYPES or not connection(practice_id, payer_connection_id):
        raise CommercialError("Rate source or payer connection is invalid.")
    if not source_reference.strip() or not 0 <= change_ceiling_bps <= 10000:
        raise CommercialError("Provide a rate-source reference and change ceiling between 0 and 10,000 basis points.")
    now, version_id = _now(), str(uuid4())
    with _connection() as db:
        db.execute("""INSERT INTO commercial_rate_versions (id,practice_id,payer_connection_id,source_type,status,source_reference,sha256,effective_from,effective_to,change_ceiling_bps,created_at)
           VALUES (%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s)""", (version_id, practice_id, payer_connection_id, source_type, source_reference.strip(), digest, effective_from, effective_to, change_ceiling_bps, now))
        db.cursor().executemany("""INSERT INTO commercial_rate_lines (version_id,row_number,code,description,modifier,unit_rule,allowed_cents,cash_cents,network_name,plan_matcher,place_of_service,provider_npi,tin,effective_from,effective_to)
          VALUES (%(version_id)s,%(row_number)s,%(code)s,%(description)s,%(modifier)s,%(unit_rule)s,%(allowed_cents)s,%(cash_cents)s,%(network)s,%(plan_matcher)s,%(pos)s,%(provider_npi)s,%(tin)s,%(effective_from)s,%(effective_to)s)""", [{**row, "version_id": version_id} for row in rows])
        row = db.execute("SELECT * FROM commercial_rate_versions WHERE id=%s AND practice_id=%s", (version_id, practice_id)).fetchone()
    return dict(row)


def activate_rates(practice_id: str, version_id: str) -> dict:
    now = _now()
    with _connection() as db:
        row = db.execute("SELECT * FROM commercial_rate_versions WHERE id=%s AND practice_id=%s FOR UPDATE", (version_id, practice_id)).fetchone()
        if not row or not db.execute("SELECT 1 FROM commercial_rate_lines WHERE version_id=%s", (version_id,)).fetchone():
            raise CommercialError("This rate version has no importable rows.")
        db.execute("UPDATE commercial_rate_versions SET status='superseded' WHERE practice_id=%s AND payer_connection_id=%s AND status='active'", (practice_id, row["payer_connection_id"]))
        db.execute("UPDATE commercial_rate_versions SET status='active', activated_at=%s WHERE id=%s", (now, version_id))
        return dict(db.execute("SELECT * FROM commercial_rate_versions WHERE id=%s", (version_id,)).fetchone())


def matching_rates(practice_id: str, profile: dict, network: dict, service_date: str, lines: list[dict] | None) -> list[dict]:
    """Match selected lines, or list the same scoped contract for a previsit catalog."""
    codes = [str(line.get("code") or "").upper() for line in lines] if lines is not None else None
    with _connection() as db:
        rows = db.execute("""SELECT l.*, v.source_reference, v.activated_at,
          (CASE WHEN l.provider_npi=%s THEN 1 ELSE 0 END + CASE WHEN l.tin=%s THEN 1 ELSE 0 END + CASE WHEN l.plan_matcher=%s THEN 1 ELSE 0 END) AS match_rank
          FROM commercial_rate_lines l JOIN commercial_rate_versions v ON v.id=l.version_id
          WHERE v.practice_id=%s AND v.payer_connection_id=%s AND v.status='active' AND (%s::text[] IS NULL OR l.code = ANY(%s))
          AND v.effective_from<=%s AND (v.effective_to IS NULL OR v.effective_to>=%s)
          AND l.network_name=%s AND l.place_of_service=%s AND l.effective_from<=%s AND (l.effective_to IS NULL OR l.effective_to>=%s)
          AND l.unit_rule='per_unit' AND (l.provider_npi='' OR l.provider_npi=%s) AND (l.tin='' OR l.tin=%s)
          AND (l.plan_matcher='' OR l.plan_matcher=%s)
          ORDER BY l.code, match_rank DESC, l.id DESC""", (network["rendering_npi"], network["tin"], profile.get("plan_matcher", ""),
                                                               practice_id, profile["payer_connection_id"], codes, codes, service_date, service_date, network["network_name"], network["place_of_service"], service_date, service_date,
                                                               network["rendering_npi"], network["tin"], profile.get("plan_matcher", ""))).fetchall()
    requested = {(str(line.get("code") or "").upper(), ",".join(line.get("modifiers") or [])) for line in lines or []}
    selected: dict[tuple[str, str], dict] = {}
    for row in rows:
        value = dict(row)
        key = (value['code'], value.get('modifier') or '')
        if lines is not None and key not in requested:
            continue
        if key in selected and value['match_rank'] == selected[key]['match_rank']:
            raise CommercialError(f"More than one active rate matches {value['code']}; resolve the rate version first.")
        if key not in selected:
            selected[key] = value
    return list(selected.values())


def save_evidence(practice_id: str, case_id: str, context_hash: str, data: dict[str, Any]) -> dict:
    now, evidence_id = _now(), str(uuid4())
    with _connection() as db:
        db.execute("""INSERT INTO commercial_eligibility_evidence (id,practice_id,case_id,context_hash,source,status,response_reference,requested_dos,payer_checked_at,normalized_json,raw_object_key,expires_at,created_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (evidence_id, practice_id, case_id, context_hash, data["source"], data.get("status", "complete"), data["response_reference"], data["requested_dos"], data["payer_checked_at"], _json(data["benefits"]), data.get("raw_object_key"), data.get("expires_at"), now))
        row = db.execute("SELECT * FROM commercial_eligibility_evidence WHERE id=%s AND practice_id=%s", (evidence_id, practice_id)).fetchone()
    return _row(row) or {}


def evidence(practice_id: str, evidence_id: str) -> dict | None:
    with _connection() as db:
        row = db.execute("SELECT * FROM commercial_eligibility_evidence WHERE id=%s AND practice_id=%s", (evidence_id, practice_id)).fetchone()
    return _row(row)
