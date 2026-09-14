"""Small transactional link transport; call from a worker thread after saving a link.

No patient details or attachments belong in these messages. A ``sent`` result
means SES accepted the message, not that it reached the recipient's inbox.
"""

from __future__ import annotations

import os
from email.headerregistry import Address
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def _address(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 254 or not value.isascii() or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Enter a valid email address.")
    try:
        address = Address(addr_spec=value)
    except (ValueError, IndexError):
        raise ValueError("Enter a valid email address.") from None
    if not address.username or not address.domain or address.addr_spec != value:
        raise ValueError("Enter a valid email address.")
    return value


def configured() -> bool:
    """Configuration is not proof that SES has verified the sender or recipient."""
    try:
        _address(os.getenv("CLINIC_EMAIL_SENDER", ""))
    except ValueError:
        return False
    return True


def configuration() -> dict:
    available = configured()
    return {"available": available, "provider": "ses" if available else None,
            "reason": None if available else "Clinic email delivery is not configured. Open your email app or copy the link."}


def link_message(*, clinic_name: str, link_url: str, purpose: str = "patient_upload") -> dict[str, str]:
    """Use the same concise message for SES and the user's email-app fallback."""
    if not clinic_name.strip() or len(clinic_name) > 200 or any(ord(char) < 32 for char in clinic_name):
        raise ValueError("A clinic name is required.")
    try:
        target = urlsplit(link_url)
        base = urlsplit(os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    except ValueError:
        raise ValueError("Invalid clinic link.") from None
    if (len(link_url) > 2048 or any(char.isspace() or ord(char) < 32 for char in link_url)
            or target.scheme not in {"http", "https"} or not target.netloc
            or target.username or target.password or (target.scheme, target.netloc) != (base.scheme, base.netloc)
            or (target.scheme == "http" and target.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Invalid clinic link.")
    clinic_name = clinic_name.strip()
    if purpose == "patient_upload":
        subject = f"Upload your insurance card for {clinic_name}"
        text = (f"{clinic_name} invites you to upload your insurance card using this private link:\n\n"
                f"{link_url}\n\nNo ClinicForce account is required. Please do not forward this link "
                "or reply with insurance information. Contact the clinic if you were not expecting this request.")
    elif purpose == "staff_invite":
        subject = f"Join {clinic_name} on ClinicForce"
        text = (f"You have been invited to join {clinic_name}'s staff workspace. "
                f"Create your staff account using this private link:\n\n{link_url}\n\n"
                "Please do not forward this link. Contact the clinic if you were not expecting this invitation.")
    else:
        raise ValueError("Unsupported clinic email.")
    return {"subject": subject, "body": text}


def send_link(recipient: str, *, clinic_name: str, link_url: str, purpose: str = "patient_upload") -> dict[str, str]:
    """Send one saved link to one recipient; never log addresses, tokens, or SDK errors."""
    recipient = _address(recipient)
    message = link_message(clinic_name=clinic_name, link_url=link_url, purpose=purpose)
    if not configured():
        return {"status": "unavailable", "detail": "Clinic email delivery is not configured. Open your email app or copy the link."}
    try:
        # Disable automatic retries: a timeout can occur after SES accepted a message.
        client = boto3.client("sesv2", region_name=os.getenv("CLINIC_EMAIL_REGION") or os.getenv("AWS_REGION", "us-east-1"),
                              config=Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1}))
        response = client.send_email(
            FromEmailAddress=_address(os.environ["CLINIC_EMAIL_SENDER"]),
            Destination={"ToAddresses": [recipient]},
            Content={"Simple": {"Subject": {"Data": message["subject"], "Charset": "UTF-8"},
                                "Body": {"Text": {"Data": message["body"], "Charset": "UTF-8"}}}},
        )
    except (BotoCoreError, ClientError):
        return {"status": "failed", "detail": "Email delivery could not be confirmed. Open your email app or copy the link."}
    message_id = response.get("MessageId")
    if not isinstance(message_id, str) or not message_id:
        return {"status": "failed", "detail": "Email delivery could not be confirmed. Open your email app or copy the link."}
    return {"status": "sent", "message_id": message_id, "detail": "Email accepted for sending."}
