"""Safe Epic snapshots for patient-authorized exchange."""

from copy import deepcopy

from .tags import tag_resource_source


def epic_share_snapshot(bundle: dict, source: dict[str, str]) -> dict:
    """Keep Epic metadata while removing remote document attachment locations."""
    snapshot = deepcopy(bundle)
    organization = source.get("organization_name", "Epic / MyChart").strip() or "Epic / MyChart"
    for entry in snapshot.get("entry", []):
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict):
            continue
        tag_resource_source(resource, "epic-mychart", organization)
        if resource.get("resourceType") == "DocumentReference":
            for content in resource.get("content", []):
                attachment = content.get("attachment") if isinstance(content, dict) else None
                if isinstance(attachment, dict):
                    attachment.pop("data", None)
                    attachment.pop("url", None)
    return snapshot
