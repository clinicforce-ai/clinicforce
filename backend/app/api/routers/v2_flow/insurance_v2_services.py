"""Insurance v2 services routes and operations."""

from __future__ import annotations


import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.routers.insurance import _response
from app.api.settings import MAX_UPLOAD_BYTES
from app.core import object_store
from app.insurance import availity_intake as intake, availity_results as results, cpt_rules, golden
from app.insurance.v2_decisions import refresh_missing, build_service_suggestions

from app.api.routers import insurance_v2 as _v2

router = _v2.router
ConfirmServices = _v2.ConfirmServices

@router.post("/cases/{case_id}/documents/{kind}")
async def upload(case_id: str, kind: str, request: Request, file: UploadFile = File(...)):
    session = await _v2._session(request)
    settings, practice, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    if settings.environment == "demo" and kind != "fees":
        raise HTTPException(409, "Demo uses Availity's synthetic inputs and does not accept patient documents.")
    if case.get("input_mode") == "ecw" and kind != "fees":
        raise HTTPException(409, "This case reads its inputs directly from eClinicalWorks. Start another case to refresh the appointment.")
    _v2.idle(case)
    if any(r != "coverage" for r in case["transactions"]) or (kind in {"card", "provider"} and case["transactions"]):
        raise HTTPException(409, "These inputs belong to a submitted transaction. Start a new case to replace them.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "V2 input documents must be 15 MB or smaller")
    try:
        filename = Path(file.filename or "document").name
        if kind == "fees":
            source = await run_in_threadpool(intake.read_fee_schedule, content, filename)
            object_key = None
            if session.get("practice_id"):
                if not object_store.enabled():
                    raise HTTPException(503, "Document storage is unavailable. The fee file has not been saved.")
                try:
                    object_key = await run_in_threadpool(object_store.put_staff_document, session["practice_id"], case_id,
                                                        filename, content, "text/csv")
                except Exception:
                    raise HTTPException(503, "The fee file could not be saved. Please try again.") from None
                source.update(object_key=object_key, content_type="text/csv")
            try:
                _v2.apply_fee_schedule(case, source)
                case.pop("suggestions", None)
                saved = await _v2.save(case, session, "fee-schedule-imported")
            except Exception:
                if object_key:
                    try:
                        await run_in_threadpool(object_store.delete_object, object_key)
                    except Exception:
                        pass
                raise
            return _response(_v2.public(saved))
        source = await run_in_threadpool(intake.read_source, kind, content, filename, practice.get("payer_aliases", {}))
    except golden.GoldenCaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    except golden.OCRServiceError:
        raise HTTPException(503, "Card OCR is unavailable. Check the server OCR installation or upload a text-based card PDF.") from None
    case["sources"][kind] = source
    inputs, missing = intake.assemble(case["sources"], case["profile"], practice)
    if case["transactions"] and any(inputs.get(key) != case["inputs"].get(key) for key in
                                    ("patient", "subscriber", "relationship", "member_id", "payer_id", "group_number", "provider", "submitter_id", "service_date")):
        raise HTTPException(409, "The new source changes the patient, subscriber, payer, provider or coverage date. Start a new case for those changes.")
    case["inputs"], case["missing"] = inputs, missing
    if not case["transactions"]:
        case["discovery"] = {}
    return _response(_v2.public(await _v2.save(case, session, f"{kind}-imported")))

@router.post("/cases/{case_id}/suggestions")
async def suggest_services(case_id: str, request: Request):
    session = await _v2._session(request)
    _, _, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.idle(case)
    if any(key in case["transactions"] for key in ("estimate", "procedure_estimate", "authorization", "referral")):
        raise HTTPException(409, "Start a new case before changing services that have already been submitted.")
    rates = case.get("sources", {}).get("fees", {}).get("lines", [])
    reason = str(case.get("inputs", {}).get("reason") or "").strip()
    if not reason and not case.get("inputs", {}).get("lines"):
        raise HTTPException(422, "eClinicalWorks must provide a reason for visit or a clinician-coded service before ClinicForce can show CPT/HCPCS choices.")
    try:
        lines, groups, notes = await run_in_threadpool(build_service_suggestions, case)
    except (ValueError, golden.GoldenCaseError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        raise HTTPException(503, "Service suggestions are temporarily unavailable. Staff can use clinician-coded services from eClinicalWorks.") from None
    case["suggestions"] = {"groups": groups, "lines": lines, "notes": notes,
                           "requires_fee_schedule": not bool(rates)}
    return _response(_v2.public(await _v2.save(case, session, "service-suggestions-generated")))

@router.post("/cases/{case_id}/services")
async def confirm_services(case_id: str, body: ConfirmServices, request: Request):
    session = await _v2._session(request)
    _, _, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.idle(case)
    if any(key in case["transactions"] for key in ("estimate", "procedure_estimate", "authorization", "referral")):
        raise HTTPException(409, "Start a new case before changing services that have already been submitted.")
    suggestions = case.get("suggestions") or {}
    choices = body.lines
    codes = [choice.code.upper() for choice in choices]
    quantities = {choice.code.upper(): choice.quantity for choice in choices}
    try:
        cpt_rules.validate_selection(codes, suggestions.get("groups", []), suggestions.get("lines", []), quantities)
    except golden.GoldenCaseError as exc:
        raise HTTPException(422, str(exc)) from exc
    candidates = {line["code"]: line for line in suggestions.get("lines", [])}
    unknown = set(codes) - set(candidates)
    if unknown:
        raise HTTPException(422, "Choose only services returned for this visit and clinic fee CSV.")
    diagnosis = case["inputs"].get("diagnoses", [])
    case["inputs"]["lines"] = [{"code": code, "description": candidates[code].get("description"),
                                  "units": str(quantities[code]), "charge": candidates[code].get("charge"),
                                  "charge_source": "fee_schedule" if candidates[code].get("charge") is not None else "unpriced_suggestion",
                                  "cash_cents": candidates[code].get("cash_cents"), "allowed_cents": candidates[code].get("allowed_cents"),
                                  "service_status": next(choice.service_status for choice in choices if choice.code.upper() == code),
                                  "modifiers": [], "diagnosis": diagnosis[0] if len(diagnosis) == 1 else None}
                                 for code in codes]
    refresh_missing(case)
    return _response(_v2.public(await _v2.save(case, session, "services-confirmed-by-staff")))

@router.post("/cases/{case_id}/requirements")
async def requirements(case_id: str, request: Request):
    session = await _v2._session(request)
    settings, practice, binding = _v2.configuration()
    case = await _v2.load(case_id, session, binding)
    _v2.require_payer_requests(case)
    _v2.idle(case)
    if case.get("source_mode") == "stedi":
        case["discovery"] = {"checked_at": datetime.now(timezone.utc).isoformat(), "outcome": "complete",
                             "message": "Stedi temporary mode uses the configured payer map and clinic contract matrix; it does not query Availity metadata.",
                             "requirements": {resource: {"available": resource in {"coverage", "estimate", "procedure_estimate"}}
                                              for resource in ("coverage", "estimate", "procedure_estimate", "authorization", "referral")}}
        return _response(_v2.public(await _v2.save(case, session, "stedi-configuration-checked")))
    payer = case["inputs"].get("payer_id")
    if not payer:
        raise HTTPException(422, "The eClinicalWorks payer must be mapped to its verified Availity payer ID first.")
    client = _v2.gateway(settings)
    # No member information is sent during payer discovery. Independent metadata calls.
    queries = [("coverage", "270", None), ("estimate", "professional-claims", "PRE_DETERMINATION"),
               ("authorization", "service-reviews", "HS"), ("referral", "service-reviews", "SC")]
    async def find(kind, type_name, subtype):
        params = {"type": type_name, "payerId": payer}
        if subtype:
            params["subtypeId"] = subtype
        response = await client.request("GET", "/v1/configurations", params=params)
        configs = results.rows(results.obj(response.get("payload")).get("configurations"))
        config = next((c for c in configs if str(c.get("payerId")) == payer and c.get("type") == type_name and
                       (not subtype or c.get("subtypeId", c.get("subTypeId", subtype)) == subtype)), {})
        return kind, {"outcome": response["outcome"], "message": response.get("message"), "configuration": config,
                      "available": response["outcome"] == "complete" and isinstance(config.get("elements"), dict) and bool(config.get("elements"))}
    found = await asyncio.gather(*(find(*q) for q in queries))
    routes = await client.request("GET", "/v1/availity-payer-list", params={"payerId": payer, "submissionMode": "API"})
    payer_payload = routes.get("payload", [])
    payers = payer_payload.get("payers", []) if isinstance(payer_payload, dict) else payer_payload
    requirements_by_kind = dict(found)
    requirements_by_kind["procedure_estimate"] = requirements_by_kind["estimate"]
    case["discovery"] = {"checked_at": datetime.now(timezone.utc).isoformat(), "requirements": requirements_by_kind,
                         "payers": payers if routes["outcome"] == "complete" else [],
                         "message": routes.get("message"), "outcome": routes["outcome"]}
    return _response(_v2.public(await _v2.save(case, session, "payer-requirements-checked")))
