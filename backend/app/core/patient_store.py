"""Local persistent storage for patient accounts and uploaded source documents."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATABASE_PATH = Path(
    os.getenv("CLINICFORCE_PATIENT_DB", Path(__file__).resolve().parents[2] / "data" / "patients.sqlite3")
)


@dataclass(frozen=True)
class StoredDocument:
    id: int
    filename: str
    uploaded_at: str
    fhir_bundle: dict[str, Any]
    pdf_bytes: bytes | None = None


@dataclass(frozen=True)
class StoredRecord:
    id: int
    category: str
    label: str
    created_at: str
    fhir_bundle: dict[str, Any]


def _connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS patient_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            category TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            fhir_bundle_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS patient_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL REFERENCES patients(patient_id),
            filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            pdf_bytes BLOB NOT NULL,
            fhir_bundle_json TEXT NOT NULL
        )"""
    )
    return connection


def create_patient(patient_id: str, password_hash: str) -> bool:
    with _connection() as connection:
        try:
            connection.execute(
                "INSERT INTO patients (patient_id, password_hash, created_at) VALUES (?, ?, ?)",
                (patient_id, password_hash, datetime.now(timezone.utc).isoformat()),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def get_password_hash(patient_id: str) -> str | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT password_hash FROM patients WHERE patient_id = ?", (patient_id,)
        ).fetchone()
    return row["password_hash"] if row else None


def _attach_document_id(fhir_bundle: dict[str, Any], document_id: int) -> None:
    """Attach the persisted artifact ID to each citation extension in the bundle."""
    for entry in fhir_bundle.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        for extension in resource.get("extension", []):
            if not isinstance(extension, dict) or extension.get("url") != "https://clinicforce.example/fhir/StructureDefinition/source-citation":
                continue
            children = extension.setdefault("extension", [])
            if isinstance(children, list) and not any(
                isinstance(child, dict) and child.get("url") == "document_id" for child in children
            ):
                children.append({"url": "document_id", "valuePositiveInt": document_id})


def add_document(patient_id: str, filename: str, pdf_bytes: bytes, fhir_bundle: dict[str, Any]) -> int:
    with _connection() as connection:
        cursor = connection.execute(
            """INSERT INTO patient_documents
               (patient_id, filename, uploaded_at, pdf_bytes, fhir_bundle_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                patient_id,
                filename,
                datetime.now(timezone.utc).isoformat(),
                pdf_bytes,
                "{}",
            ),
        )
        document_id = int(cursor.lastrowid)
        _attach_document_id(fhir_bundle, document_id)
        connection.execute(
            "UPDATE patient_documents SET fhir_bundle_json = ? WHERE id = ?",
            (json.dumps(fhir_bundle, separators=(",", ":")), document_id),
        )
    return document_id


def list_documents(patient_id: str) -> list[StoredDocument]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json
               FROM patient_documents WHERE patient_id = ? ORDER BY id DESC""",
            (patient_id,),
        ).fetchall()
    return [
        StoredDocument(
            id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
            fhir_bundle=json.loads(row["fhir_bundle_json"]),
        )
        for row in rows
    ]


def get_document(patient_id: str, document_id: int) -> StoredDocument | None:
    with _connection() as connection:
        row = connection.execute(
            """SELECT id, filename, uploaded_at, fhir_bundle_json, pdf_bytes
               FROM patient_documents WHERE patient_id = ? AND id = ?""",
            (patient_id, document_id),
        ).fetchone()
    if row is None:
        return None
    return StoredDocument(
        id=row["id"], filename=row["filename"], uploaded_at=row["uploaded_at"],
        fhir_bundle=json.loads(row["fhir_bundle_json"]), pdf_bytes=row["pdf_bytes"],
    )


def add_record(patient_id: str, category: str, label: str, fhir_bundle: dict[str, Any]) -> None:
    with _connection() as connection:
        connection.execute(
            """INSERT INTO patient_records
               (patient_id, category, label, created_at, fhir_bundle_json)
               VALUES (?, ?, ?, ?, ?)""",
            (patient_id, category, label, datetime.now(timezone.utc).isoformat(),
             json.dumps(fhir_bundle, separators=(",", ":"))),
        )


def list_records(patient_id: str) -> list[StoredRecord]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, category, label, created_at, fhir_bundle_json
               FROM patient_records WHERE patient_id = ? ORDER BY id DESC""",
            (patient_id,),
        ).fetchall()
    return [
        StoredRecord(
            id=row["id"], category=row["category"], label=row["label"],
            created_at=row["created_at"], fhir_bundle=json.loads(row["fhir_bundle_json"]),
        )
        for row in rows
    ]
