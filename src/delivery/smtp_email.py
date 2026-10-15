"""SMTP email delivery. Works with Gmail (app password), Microsoft 365, or any SMTP relay."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ..config import env
from ..render import render
from .base import Deliverer, DeliveryError


class EmailDeliverer(Deliverer):
    name = "email"

    def send(self, ctx: dict, html: str, text: str, brief_path: str) -> None:
        msg = self._message(self.cfg.get("subject", "CTI Brief {date}").format(
            date=ctx["date"], act=len(ctx["act"]), signal=len(ctx["signal"])))
        msg.set_content(text)
        # Email-specific template (tables + inline styles); `html` is the browser version written to briefs/.
        msg.add_alternative(render(ctx, "html", "email"), subtype="html")
        self._send(msg)

    def send_alert(self, subject: str, text: str) -> None:
        msg = self._message(subject)
        msg.set_content(text)
        self._send(msg)

    def _message(self, subject: str) -> EmailMessage:
        c = self.cfg
        recipients = c.get("to") or []
        if not recipients:
            raise DeliveryError("delivery.email.to is empty")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = c.get("from") or c.get("username")
        msg["To"] = ", ".join(recipients)
        return msg

    def _send(self, msg: EmailMessage) -> None:
        c = self.cfg
        password = env(c.get("password_env", "SMTP_PASSWORD"))
        if c.get("username") and not password:
            raise DeliveryError(f"{c.get('password_env', 'SMTP_PASSWORD')} is not set")

        host, port, security = c["smtp_host"], int(c.get("smtp_port", 587)), c.get("security", "starttls")
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
            else:
                server = smtplib.SMTP(host, port, timeout=30)
            with server:
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if c.get("username"):
                    server.login(c["username"], password)
                server.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise DeliveryError(f"SMTP failure: {exc}") from exc
