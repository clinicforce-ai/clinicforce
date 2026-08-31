"""Shared request-model conventions."""

from pydantic import BaseModel, ConfigDict


class RequestModel(BaseModel):
    """Strict JSON contract shared by browser-facing endpoints."""

    model_config = ConfigDict(extra="forbid")
