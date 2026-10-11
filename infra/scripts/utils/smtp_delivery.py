"""Bounded TLS SMTP transport; callers own approval and recipient allowlists."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage


def send_smtp_message(
    body: str, *, recipient: str, subject: str, host: str, port: int,
    username: str, password: str, timeout: int = 30,
) -> None:
    if not username or not password:
        raise ValueError("SMTP credentials are not configured")
    if any(char in recipient + subject + username for char in "\r\n"):
        raise ValueError("SMTP headers must not contain newlines")
    message = EmailMessage()
    message["From"] = username
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=timeout) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username, password)
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
