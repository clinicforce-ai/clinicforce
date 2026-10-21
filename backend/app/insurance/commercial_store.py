"""Small practice-scoped persistence layer for the commercial estimator."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.patient_store import _connection
from app.insurance.commercial import CommercialError, RATE_SOURCE_TYPES, SERVICE_CATEGORIES, is_united_healthcare_payer


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
    if data.get('status') == 'staff_guidance_enabled':
        raise CommercialError('Create the payer connection as draft, then activate it after scoped network and profile review.')
    required = ("display_name", "stedi_payer_id")
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in required):
        raise CommercialError("Payer name and immutable Stedi payer ID are required.")
    now, record_id = _now(), str(uuid4())
    rules = dict(data.get('rules') or {})
    rules.pop('collection_policy', None)
    with _connection() as db:
        db.execute("""INSERT INTO commercial_payer_connections
            (id,practice_id,display_name,stedi_payer_id,category,status,aliases_json,rules_json,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (record_id, practice_id, data["display_name"].strip(), data["stedi_payer_id"].strip(), category,
             data.get("status", "draft"), _json(data.get("aliases", [])), _json(rules), now, now))
    return connection(practice_id, record_id) or {}


def _merged_aliases(db: Any, practice_id: str, connection_id: str,
                    payer: dict, requested: list[str]) -> list[str]:
    """Validate an eCW route name against every other enabled route in this clinic."""
    normalize = lambda value: re.sub(r'[^a-z0-9]', '', value.casefold())
    if any(not isinstance(alias, str) or not alias.strip() or len(alias.strip()) > 160
           or not normalize(alias) for alias in requested):
        raise CommercialError('Payer aliases must be eCW payer names with letters or numbers and at most 160 characters.')
    # Serialize changes across different connections so simultaneous approvals cannot claim one name.
    db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (practice_id,))
    unique = {normalize(payer['display_name'])}
    merged = []
    for alias in [*json.loads(payer.get('aliases_json') or '[]'), *requested]:
        key = normalize(alias.strip())
        if key and key not in unique:
            unique.add(key)
            merged.append(alias.strip())
    others = db.execute('''SELECT display_name,aliases_json FROM commercial_payer_connections
        WHERE practice_id=%s AND id<>%s AND status='staff_guidance_enabled' ''',
        (practice_id, connection_id)).fetchall()
    if any(unique & {normalize(value) for value in [row['display_name'],
            *json.loads(row['aliases_json'])]} for row in others):
        raise CommercialError('An eCW payer name or alias already belongs to another enabled payer route.')
    return merged


def activate_connection(practice_id: str, connection_id: str, service_type_code: str,
                        reference: str, actor_id: str, *, aliases: list[str] | None = None) -> dict:
    """Enable an existing route only after its verified product scopes are usable."""
    if not re.fullmatch(r'[A-Za-z0-9]{1,4}', service_type_code or '') or len(reference.strip()) < 10 or not actor_id:
        raise CommercialError('Stedi service type, approval reference and administrator are required.')
    now = _now()
    with _connection() as db:
        payer = db.execute('''SELECT status,category,display_name,aliases_json,rules_json
            FROM commercial_payer_connections WHERE id=%s AND practice_id=%s FOR UPDATE''',
            (connection_id, practice_id)).fetchone()
        if not payer or payer['category'] != 'commercial_medical' or payer['status'] not in {
                'draft', 'enrollment', 'fixture_validated', 'shadow'}:
            raise CommercialError('An existing draft commercial-medical payer connection is required.')
        links = db.execute('''SELECT n.plan_matcher,n.evidence_reference,p.plan_matcher AS profile_plan_matcher
            FROM commercial_provider_networks n JOIN commercial_estimation_profiles p
              ON p.provider_network_id=n.id AND p.practice_id=n.practice_id
            WHERE n.practice_id=%s AND n.payer_connection_id=%s AND n.status='verified'
              AND p.payer_connection_id=n.payer_connection_id AND p.status='staff_guidance_enabled' ''',
            (practice_id, connection_id)).fetchall()
        if not links or any(not str(link['evidence_reference'] or '').strip() for link in links):
            raise CommercialError('Link a verified provider-network record and enabled estimate profile with participation evidence first.')
        if is_united_healthcare_payer({'payer': payer['display_name']}):
            generic = {'', 'uhc', 'unitedhealthcare', 'ppo', 'hmo', 'commercial', 'medicareadvantage'}
            if any(re.sub(r'[^a-z0-9]', '', str(link['plan_matcher'] or '').casefold()) in generic
                   or link['plan_matcher'] != link['profile_plan_matcher'] for link in links):
                raise CommercialError('Scope each enabled UHC network and profile to the same exact payer product before activation.')
        merged_aliases = _merged_aliases(db, practice_id, connection_id, payer, aliases or [])
        rules = json.loads(payer['rules_json'])
        rules['service_type_code'] = service_type_code
        rules['activation'] = {'reference': reference.strip(), 'approved_by': actor_id, 'approved_at': now}
        db.execute('''UPDATE commercial_payer_connections SET status='staff_guidance_enabled',
            rules_json=%s,aliases_json=%s,updated_at=%s WHERE id=%s AND practice_id=%s''',
            (_json(rules), _json(merged_aliases), now, connection_id, practice_id))
    return connection(practice_id, connection_id) or {}


def add_connection_aliases(practice_id: str, connection_id: str, aliases: list[str],
                           reference: str, actor_id: str) -> dict:
    """Add later eCW payer labels without changing participation or activation."""
    if not aliases or len(reference.strip()) < 10 or not actor_id:
        raise CommercialError('Payer aliases, approval reference and administrator are required.')
    now = _now()
    with _connection() as db:
        payer = db.execute('''SELECT status,category,display_name,aliases_json,rules_json
            FROM commercial_payer_connections WHERE id=%s AND practice_id=%s FOR UPDATE''',
            (connection_id, practice_id)).fetchone()
        if not payer or payer['category'] != 'commercial_medical' or payer['status'] != 'staff_guidance_enabled':
            raise CommercialError('An enabled commercial-medical payer connection is required.')
        merged = _merged_aliases(db, practice_id, connection_id, payer, aliases)
        rules = json.loads(payer['rules_json'])
        rules['alias_update'] = {'reference': reference.strip(), 'approved_by': actor_id,
                                 'approved_at': now}
        db.execute('''UPDATE commercial_payer_connections SET aliases_json=%s,
            rules_json=%s,updated_at=%s WHERE id=%s AND practice_id=%s''',
            (_json(merged), _json(rules), now, connection_id, practice_id))
    return connection(practice_id, connection_id) or {}


def set_collection_policy(practice_id: str, connection_id: str, mode: str,
                          reference: str, actor_id: str, *, allow_supported_oon_advance: bool = False,
                          allow_cms_based_advance: bool = False) -> dict:
    """Set a practice-scoped payer policy with an administrator and source reference."""
    if mode not in {'defer_to_claim', 'collect_verified_copay', 'disabled'} or not reference.strip() or not actor_id:
        raise CommercialError('A collection policy mode, approval reference and administrator are required.')
    now = _now()
    with _connection() as db:
        row = db.execute('SELECT rules_json FROM commercial_payer_connections WHERE id=%s AND practice_id=%s FOR UPDATE',
                         (connection_id, practice_id)).fetchone()
        if not row:
            raise CommercialError('The payer connection is not in this practice.')
        rules = json.loads(row['rules_json'])
        if mode == 'disabled':
            rules.pop('collection_policy', None)
        else:
            rules['collection_policy'] = {'mode': mode, 'reference': reference.strip(),
                                          'approved_by': actor_id, 'approved_at': now,
                                          'allow_supported_oon_advance': allow_supported_oon_advance,
                                          'allow_cms_based_advance': allow_cms_based_advance}
        db.execute('UPDATE commercial_payer_connections SET rules_json=%s,updated_at=%s WHERE id=%s AND practice_id=%s',
                   (_json(rules), now, connection_id, practice_id))
    return connection(practice_id, connection_id) or {}


def set_agreement_inventory(practice_id: str, connection_id: str, mode: str,
                            reference: str, scopes: list[dict], actor_id: str) -> dict:
    """Record or revoke a practice-scoped UHC inventory attestation."""
    from app.insurance.inventory_certification import agreement_hashes

    if mode not in {'certify', 'revoke'} or not reference.strip() or not actor_id:
        raise CommercialError('An inventory decision, reference and administrator are required.')
    now = _now()
    with _connection() as db:
        row = db.execute('''SELECT display_name,rules_json FROM commercial_payer_connections
            WHERE id=%s AND practice_id=%s FOR UPDATE''', (connection_id, practice_id)).fetchone()
        if not row or not is_united_healthcare_payer({'payer': row['display_name']}):
            raise CommercialError('Select the UnitedHealthcare payer route in this practice.')
        rules = json.loads(row['rules_json'])
        if mode == 'revoke':
            rules.pop('agreement_inventory', None)
        else:
            hashes = agreement_hashes(practice_id, row['display_name'])
            if not hashes or not scopes:
                raise CommercialError('Upload the payer agreements and provide at least one covered provider and location scope.')
            if any(scope.get('effective_to') and scope['effective_to'] < scope['effective_from'] for scope in scopes):
                raise CommercialError('An inventory scope ends before its effective date.')
            if any(any(scope.get(key) for key in ('cms_locality', 'cms_qp_status', 'cms_payment_reference'))
                   and not all(scope.get(key) for key in ('cms_locality', 'cms_qp_status', 'cms_payment_reference'))
                   for scope in scopes):
                raise CommercialError('CMS pricing requires locality, QP status and a payment-scope evidence reference together.')
            rules['agreement_inventory'] = {'reference': reference.strip(),
                'approved_by': actor_id, 'approved_at': now,
                'document_hashes': hashes, 'scopes': scopes}
        db.execute('UPDATE commercial_payer_connections SET rules_json=%s,updated_at=%s WHERE id=%s AND practice_id=%s',
                   (_json(rules), now, connection_id, practice_id))
    return connection(practice_id, connection_id) or {}


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


def save_product_mapping(practice_id: str, profile_id: str, products: list[str], evidence: dict) -> None:
    with _connection() as db:
        row = db.execute('SELECT rules_json FROM commercial_estimation_profiles WHERE id=%s AND practice_id=%s FOR UPDATE',
                         (profile_id, practice_id)).fetchone()
        if not row:
            raise CommercialError('The estimate profile is no longer available.')
        rules = json.loads(row['rules_json'])
        rules.setdefault('product_mappings', []).append({'products': products, **evidence})
        db.execute('UPDATE commercial_estimation_profiles SET rules_json=%s,updated_at=%s WHERE id=%s AND practice_id=%s',
                   (_json(rules), _now(), profile_id, practice_id))


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
        # A payer may have separate active schedules for products, networks or
        # provider types. Replace only a revision of this same source.
        db.execute("UPDATE commercial_rate_versions SET status='superseded' WHERE practice_id=%s AND payer_connection_id=%s AND source_reference=%s AND id<>%s AND status='active'",
                   (practice_id, row["payer_connection_id"], row["source_reference"], version_id))
        db.execute("UPDATE commercial_rate_versions SET status='active', activated_at=%s WHERE id=%s", (now, version_id))
        return dict(db.execute("SELECT * FROM commercial_rate_versions WHERE id=%s", (version_id,)).fetchone())


def retire_rates(practice_id: str, version_id: str) -> dict:
    """Stop an outdated allowance version from pricing future visits."""
    with _connection() as db:
        row = db.execute("""UPDATE commercial_rate_versions SET status='retired'
            WHERE id=%s AND practice_id=%s AND status='active' RETURNING *""",
            (version_id, practice_id)).fetchone()
    if not row:
        raise CommercialError("Only an active rate version in this practice can be retired.")
    return dict(row)


def matching_rates(practice_id: str, profile: dict, network: dict, service_date: str, lines: list[dict] | None) -> list[dict]:
    """Match selected lines, or list the same scoped contract for a previsit catalog."""
    codes = [str(line.get("code") or "").upper() for line in lines] if lines is not None else None
    rate_plan = profile.get('rate_plan_matcher', profile.get('plan_matcher', ''))
    exact_product = bool(profile.get('require_exact_rate_plan'))
    with _connection() as db:
        rows = db.execute("""SELECT l.*, v.source_reference, v.activated_at,
          (CASE WHEN l.provider_npi=%s THEN 1 ELSE 0 END + CASE WHEN l.tin=%s THEN 1 ELSE 0 END + CASE WHEN lower(l.plan_matcher)=lower(%s) THEN 1 ELSE 0 END) AS match_rank
          FROM commercial_rate_lines l JOIN commercial_rate_versions v ON v.id=l.version_id
          WHERE v.practice_id=%s AND v.payer_connection_id=%s AND v.status='active' AND (%s::text[] IS NULL OR l.code = ANY(%s))
          AND v.effective_from<=%s AND (v.effective_to IS NULL OR v.effective_to>=%s)
          AND l.network_name=%s AND l.place_of_service=%s AND l.effective_from<=%s AND (l.effective_to IS NULL OR l.effective_to>=%s)
          AND l.unit_rule='per_unit' AND (l.provider_npi='' OR l.provider_npi=%s) AND (l.tin='' OR l.tin=%s)
          AND (l.plan_matcher='' OR lower(l.plan_matcher)=lower(%s)) AND (NOT %s OR l.plan_matcher<>'')
          ORDER BY l.code, match_rank DESC, l.id DESC""", (network["rendering_npi"], network["tin"], rate_plan,
                                                               practice_id, profile["payer_connection_id"], codes, codes, service_date, service_date, network["network_name"], network["place_of_service"], service_date, service_date,
                                                               network["rendering_npi"], network["tin"], rate_plan, exact_product)).fetchall()
    requested = {(str(line.get("code") or "").upper(), ",".join(line.get("modifiers") or [])) for line in lines or []}
    selected: dict[tuple[str, str], dict] = {}
    for row in rows:
        value = dict(row)
        key = (value['code'], value.get('modifier') or '')
        if lines is not None and key not in requested:
            continue
        if key in selected and value['match_rank'] == selected[key]['match_rank']:
            if all(value.get(field) == selected[key].get(field) for field in ('allowed_cents', 'cash_cents', 'unit_rule')):
                selected[key].setdefault('equivalent_sources', [selected[key]['source_reference']]).append(value['source_reference'])
                continue
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
