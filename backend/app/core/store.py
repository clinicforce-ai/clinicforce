"""Temporary in-memory storage for encrypted SHL manifests."""

from uuid import UUID, uuid4

from pydantic import BaseModel


class StoredManifest(BaseModel):
    encrypted_jwe: str
    hashed_pin: str


manifests: dict[UUID, StoredManifest] = {}


def create_manifest(encrypted_jwe: str, hashed_pin: str) -> UUID:
    """Store an encrypted payload and return its unguessable manifest identifier."""
    manifest_id = uuid4()
    manifests[manifest_id] = StoredManifest(
        encrypted_jwe=encrypted_jwe, hashed_pin=hashed_pin
    )
    return manifest_id


def get_manifest(manifest_id: UUID) -> StoredManifest | None:
    return manifests.get(manifest_id)
