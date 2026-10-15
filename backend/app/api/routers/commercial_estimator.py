"""Staff-only Stedi commercial eligibility and estimate workflow.

This route is deliberately separate from the existing Availity workflow.
"""
from __future__ import annotations

from pathlib import Path

import hashlib
import ipaddress
import asyncio
import json
import time
import os
import re
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi.responses import JSONResponse
from fastapi import BackgroundTasks, APIRouter, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import StrictInt, BaseModel, ConfigDict, Field, ValidationError

from app.api.routers.insurance import _response, _scope, _session as insurance_session
from app.core import object_store, staff_store
from app.insurance import identity as insurance_identity
from app.insurance import commercial, commercial_store, cpt_rules, cpt_suggester, ecw_backend, golden, stedi_discovery, stedi_payers, store, visit_estimate, practice_prices
from app.insurance import stedi_financial, collection_guidance, encounter_summary, amount_explanation, contract_requirements, ecw_benefits, ecw_billing, library_pricing
from app.insurance import provider_specialty
from app.logs.collection_audit import collection_event
from app.logs.logger import log_info, log_warning


router = APIRouter(prefix="/api/doctor/insurance/commercial", tags=["doctor-commercial-estimator"])
STEDI_URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"


from app.api.schemas.commercial import (
    ConnectionInput,
    NetworkInput,
    ProfileInput,
    StartInput,
    EcwStartInput,
    PrevisitInput,
    ServiceLine,
    ServicesInput,
    VisitVerification,
    IdentityConfirmation,
    ProductMapping,
    VisitReviewInput,
    CoverageCandidateInput,
    PayerRouteInput,
    EvidenceInput,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _session(request: Request) -> dict:
    session = await insurance_session(request)
    if not session.get("practice_id"):
        raise HTTPException(403, "Open the commercial estimator from the staff workspace.")
    return session


def _admin(session: dict) -> None:
    if session["staff"].get("role") != "admin":
        raise HTTPException(403, "Only a clinic administrator can change commercial-estimator configuration.")


from app.api.routers.commercial_flow.commercial_practice import (
    _network,
    _public,
    _load,
    _save,
    _config,
    _ecw_practice,
    _ecw_practice_config,
    _practice_path,
    _previsit_practice_path,
    _appointment_readiness,
)


from app.api.routers.commercial_flow.commercial_previsit_pricing import (
    _ecw_case_data,
    _past_visit_with_saved_estimate,
    _commercial_slot,
    _previsit_service_types,
    _eligibility_timing,
    _previsit_codes,
    _office_service_types,
    _benefit_context,
    _previsit_range,
    _previsit_benefits,
    _network_contract_evidence,
    _requirement_candidates,
    _attach_previsit_pricing,
)


from app.api.routers.commercial_flow.commercial_previsit_finish import (
    _visit_context_token,
    _finish_previsit,
)


from app.api.routers.commercial_flow.commercial_ecw_evidence import (
    _ecw_coded_lines,
    _ecw_identity,
    _coverage_snapshot,
    _ecw_conflicts,
    _evidence_context,
    _assert_ecw_current,
    _stedi_address,
    stedi_request_body,
    _request_validation,
    _post_stedi_eligibility,
    _record_evidence,
    _subscriber_identity,
)


from app.api.routers.commercial_flow.commercial_previsit_status import (
    configuration,
    ecw_appointments,
    ecw_appointment_readiness,
    _recovery_binding,
    _case_benefits,
    _enrollment_failure,
    _insurance_diagnosis,
    _previsit_result,
    _recovery_event,
    _read_card_hints,
)


from app.api.routers.commercial_flow.commercial_recovery_cards import (
    _compare_card_hints,
    _recover_card,
    _recovery_stopped,
    _request_ip_chain,
    _recover_saved_policy,
)


from app.api.routers.commercial_flow.commercial_recovery_search import (
    _recover_known_payer,
    _recover_mbi,
    _recover_previsit,
    _recover_discovery,
)


from app.api.routers.commercial_flow.commercial_recovery_flow import (
    _run_coverage_recovery,
    _reconcile_saved_discovery,
    _discovered_coverage,
    _discovery_pricing_source,
    _accept_recovered_coverage,
    _recheck_discovery_candidate,
    _start_previsit_recovery,
)


from app.api.routers.commercial_flow.commercial_previsit_routes import (
    saved_previsit,
    saved_payer_response,
    select_payer_route,
    select_coverage_candidate,
    _begin_coverage_discovery,
)


from app.api.routers.commercial_flow.commercial_estimate_route import (
    previsit_estimate,
)


from app.api.routers.commercial_flow.commercial_review_routes import (
    update_visit_review,
    previsit_financials,
    saved_visit_financials,
    ecw_completed_appointments,
    start_from_ecw,
    read_ecw_appointment,
)


from app.api.routers.commercial_flow.commercial_configuration_routes import (
    saved_eligibility_probe,
    create_connection,
    create_network,
    create_profile,
    import_rates,
    activate_rates,
    start_case,
    get_case,
    set_services,
    record_evidence,
    stedi_eligibility,
    calculate,
    upload_cash_schedule,
    activate_cash_schedule,
)
