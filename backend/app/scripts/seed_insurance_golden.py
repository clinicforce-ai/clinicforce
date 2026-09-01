"""Build and upload the reviewed golden evidence bundle without committing PHI."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import boto3


FILES = {
    "card": "Availity Essentials.pdf",
    "eligibility": "Availity Essentials - Cov.pdf",
    "agreement": "Medical Group - NEUROLOGY AND MOVEMENT CENTER OF - TX ALL AGMNT NEXT AVL EFF DAT_Original_Profession (1) (1).pdf",
    "matrix": "NMCT_Payer_Plan_Matrix (1).xlsx",
    "rates": "fee-schedule-results.csv",
    "rate_setup": "fee schedule.png",
    "appointment": "shmso.com Mail - (no subject).pdf",
}


def _digest(value: str) -> str:
    normalized = "".join(character for character in value.casefold() if character.isalnum())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _citation(source: dict, reference: str) -> dict:
    return {
        "source_filename": source["filename"], "sha256": source["sha256"],
        "reference": reference, "observed_at": source["observed_at"],
    }


def build_bundle(evidence_dir: Path, identity_path: Path, reviewed_at: str) -> bytes:
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    required_identity = {"given_name", "family_name", "birth_date", "member_id", "group_number"}
    if not required_identity.issubset(identity):
        raise ValueError("Identity JSON must contain given_name, family_name, birth_date, member_id, and group_number")
    contents = {key: (evidence_dir / filename).read_bytes() for key, filename in FILES.items()}
    observed = {key: "2026-08-24" for key in FILES}
    sources = {
        key: {"id": key, "filename": FILES[key], "sha256": hashlib.sha256(content).hexdigest(), "observed_at": observed[key]}
        for key, content in contents.items()
    }
    rates = {}
    for row in csv.DictReader(StringIO(contents["rates"].decode("utf-8-sig"))):
        if row["Procedure Code"] in {"99204", "99205"}:
            rates[row["Procedure Code"]] = int(row["Amount"].replace("$", "").replace(".", ""))
    if rates != {"99204": 15160, "99205": 20023}:
        raise ValueError("The fee schedule does not contain the reviewed 99204 and 99205 rates")
    manifest = {
        "version": 1, "reviewed_at": reviewed_at,
        "identity": {f"{name}_sha256": _digest(identity[name]) for name in required_identity},
        "appointment": {
            "fhir_r4_resource_type": "Appointment", "status": "booked", "start": None, "end": None,
            "date": "2026-08-24", "service_type": "Neurology office visit", "appointment_type": "New patient",
            "reason": "Neuropathy", "participants": ["Patient", "Neurology clinician"],
            "provider": "Neurology and Movement Center of Texas", "location": "Office",
            "source": _citation(sources["appointment"], "page 1"),
        },
        "coverage": {
            "status": "active", "product": "BCBSTX Preferred Provider Option Medical", "effective_date": "2026-01-01",
            "coinsurance_percent": 20, "individual_deductible_cents": 60000,
            "individual_deductible_remaining_cents": 0, "individual_oop_max_cents": 300000,
            "individual_oop_remaining_cents": 0, "transaction_reference": "Availity 270/271 · DOS 2026-08-24",
            "source": _citation(sources["eligibility"], "page 2 · medical benefit / individual columns"),
        },
        "card_source": _citation(sources["card"], "page 1 · Choice PPO member card"),
        "network": {
            "status": "in_network", "display": "NMCT provider and office · Blue Choice PPO · in network",
            "source": _citation(sources["eligibility"], "page 2 · provider information / in-network response"),
            "agreement_source": _citation(sources["agreement"], "pages 38, 40, and 44"),
            "matrix_source": _citation(sources["matrix"], "Payer Plan Matrix · row 17"),
        },
        "authorization": {
            "referral": "not_required", "prior_authorization": "not_required",
            "referral_source": _citation(sources["matrix"], "Payer Plan Matrix · row 17 · BCBSTX PPO/Open Access"),
            "prior_auth_source": _citation(sources["eligibility"], "procedure check 99204 · No Auth Required"),
        },
        "rates": rates,
        "rates_source": _citation(sources["rates"], "99204 and 99205 rows · Blue Choice PPO"),
        "rejected_source": _citation(sources["appointment"], "page 1 · $241.51 collection instruction"),
        "sources": list(sources.values()),
    }
    manifest["manifest_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        for key, content in contents.items():
            bundle.writestr(f"sources/{FILES[key]}", content)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--identity-file", required=True, type=Path, help="Uncommitted local JSON containing the five matching fields")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--kms-key-id", required=True)
    args = parser.parse_args()
    reviewed_at = datetime.now(timezone.utc).isoformat()
    content = build_bundle(args.evidence_dir, args.identity_file, reviewed_at)
    boto3.client("s3").put_object(
        Bucket=args.bucket, Key=args.key, Body=content, ContentType="application/zip",
        ServerSideEncryption="aws:kms", SSEKMSKeyId=args.kms_key_id,
        Metadata={"sha256": hashlib.sha256(content).hexdigest(), "reviewed-at": reviewed_at},
    )
    print(json.dumps({"key": args.key, "sha256": hashlib.sha256(content).hexdigest()}))


if __name__ == "__main__":
    main()
