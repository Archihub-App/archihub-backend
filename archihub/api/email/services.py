"""Sending mail.

Port of ``app/api/email/services.py``, with the connection handled properly.

The connection is a context manager with an explicit timeout. Closing it only on
the happy path leaks the socket on any exception in between, and an unresponsive
mail server with no timeout holds the calling thread indefinitely.

CALLERS MUST TREAT FAILURE AS NON-FATAL where the response would otherwise
reveal something. ``forgot_password`` is the case that matters: if a real account
whose mail fails to send returned an error while an invented one returned
success, the endpoint would report which usernames exist.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.example.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER") or None
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD") or None
EMAIL_USE_TLS = str(os.environ.get("EMAIL_USE_TLS", "")).strip().lower() in {
    "1", "true", "yes", "on",
}
EMAIL_TIMEOUT = float(os.environ.get("EMAIL_TIMEOUT", 15))

EMAIL_FROM = os.environ.get("EMAIL_ADDRESS") or EMAIL_HOST_USER or "no-reply@archihub"


def send_email(to_email: str, subject: str, body: str) -> None:
    """Send one HTML message. Raises on failure - see the module docstring."""
    message = EmailMessage()
    message["From"] = EMAIL_FROM
    message["To"] = to_email
    message["Subject"] = subject
    # A plain-text alternative first, so the message is not flagged as
    # HTML-only; the HTML part is what clients render.
    message.set_content("This message requires an HTML-capable email client.")
    message.add_alternative(body, subtype="html")

    # `with` guarantees the connection is closed even when send or login raises.
    with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=EMAIL_TIMEOUT) as server:
        if EMAIL_USE_TLS:
            server.starttls()
        if EMAIL_HOST_USER and EMAIL_HOST_PASSWORD:
            server.login(EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)
        server.send_message(message)

    logger.info("Sent %r message", subject)
