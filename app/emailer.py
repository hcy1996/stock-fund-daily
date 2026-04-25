from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import SMTPConfig


def send_html_email(
    smtp_config: SMTPConfig,
    recipients: list[str],
    subject: str,
    html: str,
) -> None:
    if not recipients:
        raise ValueError("缺少收件人。")

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = smtp_config.sender
    message["To"] = ", ".join(recipients)
    message.attach(MIMEText(html, "html", "utf-8"))

    if smtp_config.use_ssl:
        with smtplib.SMTP_SSL(smtp_config.host, smtp_config.port, timeout=30) as server:
            server.login(smtp_config.username, smtp_config.password)
            server.sendmail(smtp_config.sender, recipients, message.as_string())
        return

    with smtplib.SMTP(smtp_config.host, smtp_config.port, timeout=30) as server:
        if smtp_config.starttls:
            server.starttls()
        server.login(smtp_config.username, smtp_config.password)
        server.sendmail(smtp_config.sender, recipients, message.as_string())
