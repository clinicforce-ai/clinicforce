"""S3-backed storage for PHI artifacts.

The database stores only object keys and metadata; PDF bytes stay in S3. All
callers run these synchronous boto3 operations in a FastAPI worker thread.
"""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import PurePath
from uuid import uuid4

import boto3


S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_PREFIX = os.getenv("S3_PREFIX", "clinicforce").strip("/")
S3_KMS_KEY_ID = os.getenv("S3_KMS_KEY_ID", "").strip()


def enabled() -> bool:
    return bool(S3_BUCKET)


def _safe_segment(value: str) -> str:
    """Keep user-controlled names out of S3 key path traversal and ambiguity."""
    name = PurePath(value).name
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in name) or "file.pdf"


def _key(patient_id: str, artifact_type: str, filename: str) -> str:
    # Do not disclose a patient-entered identifier in S3 paths, CloudTrail, or logs.
    patient_partition = sha256(patient_id.encode("utf-8")).hexdigest()
    return "/".join((S3_PREFIX, "patients", patient_partition, artifact_type, str(uuid4()), _safe_segment(filename)))


def put_patient_pdf(patient_id: str, filename: str, content: bytes, artifact_type: str) -> str:
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for S3 storage")
    key = _key(patient_id, artifact_type, filename)
    arguments = {
        "Bucket": S3_BUCKET,
        "Key": key,
        "Body": content,
        "ContentType": "application/pdf",
        "ServerSideEncryption": "aws:kms",
    }
    if S3_KMS_KEY_ID:
        arguments["SSEKMSKeyId"] = S3_KMS_KEY_ID
    boto3.client("s3").put_object(**arguments)
    return key


def put_insurance_pdf(case_id: str, filename: str, content: bytes) -> str:
    """Store a short-lived insurance evidence PDF under the lifecycle-managed case prefix."""
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for S3 storage")
    key = "/".join((S3_PREFIX, "insurance-cases", _safe_segment(case_id), str(uuid4()), _safe_segment(filename)))
    arguments = {
        "Bucket": S3_BUCKET, "Key": key, "Body": content, "ContentType": "application/pdf",
        "ServerSideEncryption": "aws:kms",
    }
    if S3_KMS_KEY_ID:
        arguments["SSEKMSKeyId"] = S3_KMS_KEY_ID
    boto3.client("s3").put_object(**arguments)
    return key


def get_pdf(key: str) -> bytes:
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for S3 storage")
    response = boto3.client("s3").get_object(Bucket=S3_BUCKET, Key=key)
    return response["Body"].read()


def get_object_bytes(key: str) -> bytes:
    """Read an opaque private object without assuming its content type."""
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for S3 storage")
    response = boto3.client("s3").get_object(Bucket=S3_BUCKET, Key=key)
    return response["Body"].read()


def delete_object(key: str) -> None:
    """Remove an orphaned upload when a later persistence step fails."""
    if S3_BUCKET:
        boto3.client("s3").delete_object(Bucket=S3_BUCKET, Key=key)


def put_shl_payload(manifest_id: str, encrypted_jwe: str) -> str:
    """Store an already-encrypted SHL file for a short-lived signed download."""
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for oversized SHL files")
    key = "/".join((S3_PREFIX, "shl", f"{manifest_id}.jwe"))
    arguments = {
        "Bucket": S3_BUCKET,
        "Key": key,
        "Body": encrypted_jwe.encode("ascii"),
        "ContentType": "application/jose",
        "ServerSideEncryption": "aws:kms",
    }
    if S3_KMS_KEY_ID:
        arguments["SSEKMSKeyId"] = S3_KMS_KEY_ID
    boto3.client("s3").put_object(**arguments)
    return key


def presign_shl_payload(key: str, expires_in: int = 3600) -> str:
    """Return an SHL file URL valid for no more than the one-hour spec limit."""
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET must be configured for oversized SHL files")
    return boto3.client("s3").generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key, "ResponseContentType": "application/jose"},
        ExpiresIn=min(max(expires_in, 1), 3600),
    )
