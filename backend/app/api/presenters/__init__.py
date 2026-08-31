"""HTTP response shaping with no persistence or transport side effects."""

from .workspace import record_payload, workspace_payload
from .summary import with_source_download_links
from .profile import patient_profile_payload

__all__ = ["patient_profile_payload", "record_payload", "with_source_download_links", "workspace_payload"]
