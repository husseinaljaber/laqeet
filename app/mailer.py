"""Sending email (verification and password-reset codes).

Two ways to send for real:
- BREVO_API_KEY + MAIL_FROM: Brevo's HTTPS API (free 300/day). Works where SMTP ports are blocked (free Render).
- SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / MAIL_FROM: any SMTP provider (Gmail app password, Zoho…). Without them the site still works in development: messages are written to
the log and kept in OUTBOX (tests read the codes from there). Never use the dev mode in production.
"""
from __future__ import annotations

import html as _html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config

log = logging.getLogger("mailer")
OUTBOX: list[dict] = []          # development and tests only
_OUTBOX_MAX = 200


BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def configured() -> bool:
    return bool(config.MAIL_FROM and (config.BREVO_API_KEY or config.SMTP_HOST))


def _send_brevo(to: str, subject: str, text: str, html: str | None) -> bool:
    import httpx
    body = {"sender": {"name": config.SITE_NAME, "email": config.MAIL_FROM}, "to": [{"email": to}],
            "subject": subject, "textContent": text}
    if html:
        body["htmlContent"] = html
    try:
        r = httpx.post(BREVO_URL, json=body, timeout=15,
                       headers={"api-key": config.BREVO_API_KEY, "accept": "application/json"})
    except httpx.HTTPError as e:
        log.error("mail to %s failed (Brevo): %s", to, e)
        return False
    if r.status_code >= 300:
        log.error("mail to %s failed (Brevo %s): %s", to, r.status_code, r.text[:300])
        return False
    return True


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one email. Returns False (and logs) on failure; callers never show SMTP errors to people."""
    if not configured():
        OUTBOX.append({"to": to, "subject": subject, "text": text})
        del OUTBOX[:-_OUTBOX_MAX]
        log.warning("DEV MAIL (SMTP not configured) to=%s subject=%s\n%s", to, subject, text)
        return True
    if config.BREVO_API_KEY:
        return _send_brevo(to, subject, text, html)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((config.SITE_NAME, config.MAIL_FROM))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if config.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(), timeout=15) as s:
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        else:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                if config.SMTP_USER:
                    s.login(config.SMTP_USER, config.SMTP_PASSWORD)
                s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.error("mail to %s failed: %s", to, e)
        return False


def code_email(first_name: str, code: str, purpose: str) -> tuple[str, str, str]:
    """(subject, text, html) for a 6-digit code, in Arabic."""
    what = {"verify": "تأكيد إيميلك", "reset": "تغيير كلمة السر"}[purpose]
    safe_name = _html.escape(first_name)  # a name is typed by the person: never raw HTML in the email
    subject = f"رمز {what} على {config.SITE_NAME}: {code}"
    text = (f"أهلاً {first_name}،\n\nرمز {what} هو: {code}\n\nالرمز صالح لمدة 15 دقيقة. "
            f"إذا ما طلبت هالشي، تجاهل هالرسالة وما حدا رح يقدر يدخل على حسابك.\n\nفريق {config.SITE_NAME}")
    html = (f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;font-size:16px;line-height:1.7;color:#13201B">'
            f"<p>أهلاً {safe_name}،</p><p>رمز {what}:</p>"
            f'<p style="font-size:30px;font-weight:bold;letter-spacing:6px;color:#0B6B53">{code}</p>'
            f"<p>الرمز صالح لمدة 15 دقيقة. إذا ما طلبت هالشي، تجاهل هالرسالة.</p>"
            f'<p style="color:#5B6B64">فريق {config.SITE_NAME}</p></div>')
    return subject, text, html
