"""Sending email (verification and password-reset codes).

Set SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / MAIL_FROM to send for real (any provider: Gmail app
password, Zoho, Brevo, Resend SMTP…). Without them the site still works in development: messages are written to
the log and kept in OUTBOX (tests read the codes from there). Never use the dev mode in production.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import config

log = logging.getLogger("mailer")
OUTBOX: list[dict] = []          # development and tests only
_OUTBOX_MAX = 200


def configured() -> bool:
    return bool(config.SMTP_HOST and config.MAIL_FROM)


def send(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Send one email. Returns False (and logs) on failure; callers never show SMTP errors to people."""
    if not configured():
        OUTBOX.append({"to": to, "subject": subject, "text": text})
        del OUTBOX[:-_OUTBOX_MAX]
        log.warning("DEV MAIL (SMTP not configured) to=%s subject=%s\n%s", to, subject, text)
        return True
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
    subject = f"رمز {what} على {config.SITE_NAME}: {code}"
    text = (f"أهلاً {first_name}،\n\nرمز {what} هو: {code}\n\nالرمز صالح لمدة 15 دقيقة. "
            f"إذا ما طلبت هالشي، تجاهل هالرسالة وما حدا رح يقدر يدخل على حسابك.\n\nفريق {config.SITE_NAME}")
    html = (f'<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;font-size:16px;line-height:1.7;color:#13201B">'
            f"<p>أهلاً {first_name}،</p><p>رمز {what}:</p>"
            f'<p style="font-size:30px;font-weight:bold;letter-spacing:6px;color:#0B6B53">{code}</p>'
            f"<p>الرمز صالح لمدة 15 دقيقة. إذا ما طلبت هالشي، تجاهل هالرسالة.</p>"
            f'<p style="color:#5B6B64">فريق {config.SITE_NAME}</p></div>')
    return subject, text, html
