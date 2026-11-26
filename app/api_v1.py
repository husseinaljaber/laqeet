"""/api/v1: the mobile app's API (accounts, profile, saved jobs, personal feed).

Every request gets its own short-lived database connection, so accounts never share a transaction with the
collector or other requests. Errors come back as {"detail": "<Arabic message>", "field": "...", "code": "..."}
so the app can show the message next to the right input.
"""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from . import accounts as acc
from . import config, db

router = APIRouter(prefix="/api/v1")


def get_con() -> Iterator[sqlite3.Connection]:
    con = db.raw_connect()
    try:
        yield con
    finally:
        con.close()


def _device(request: Request) -> str:
    return (request.headers.get("x-device") or request.headers.get("user-agent") or "")[:80]


def current_user(authorization: str | None = Header(None), con: sqlite3.Connection = Depends(get_con)):
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = acc.user_from_access_token(con, token) if token else None
    if not user:
        raise acc.AccountError("سجّل دخول من جديد", status=401, code="unauthorized")
    return user


# rate limits (per IP, in memory; the same helper the rest of the API uses)
def _limits():
    from .api import _RateLimit
    return {
        "register": _RateLimit(5, 3600), "login": _RateLimit(10, 600), "login_email": _RateLimit(8, 600),
        "forgot": _RateLimit(5, 3600), "code": _RateLimit(20, 600), "tg_link": _RateLimit(10, 3600),
    }


_RL: dict | None = None


def _limit(kind: str, key: str) -> None:
    global _RL
    _RL = _RL or _limits()
    if not _RL[kind].allow(key):
        raise acc.AccountError("محاولات كتير، جرّب بعد شوي", status=429, code="rate_limited")


def _ip(request: Request) -> str:
    from .api import _client_ip
    return _client_ip(request)


# ---------------------------------------------------------------- auth
@router.post("/auth/register", status_code=201)
def register(request: Request, body: dict = Body(...), con=Depends(get_con)):
    _limit("register", _ip(request))
    return acc.register(con, first_name=body.get("first_name", ""), last_name=body.get("last_name", ""),
                        gender=body.get("gender", ""), email=body.get("email", ""), password=body.get("password", ""),
                        accepted_terms=bool(body.get("accepted_terms")), source=str(body.get("source") or "app"),
                        device=_device(request))


@router.post("/auth/login")
def login(request: Request, body: dict = Body(...), con=Depends(get_con)):
    _limit("login", _ip(request))
    _limit("login_email", str(body.get("email", "")).strip().lower()[:254])
    return acc.login(con, body.get("email", ""), body.get("password", ""), device=_device(request))


@router.post("/auth/refresh")
def refresh(request: Request, body: dict = Body(...), con=Depends(get_con)):
    return acc.refresh(con, body.get("refresh_token", ""), device=_device(request))


@router.post("/auth/logout")
def logout(body: dict = Body(default={}), con=Depends(get_con)):
    acc.logout(con, body.get("refresh_token", ""))
    return {"ok": True}


@router.post("/auth/verify-email")
def verify_email(request: Request, body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    _limit("code", _ip(request))
    return acc.verify_email(con, user, body.get("code", ""))


@router.post("/auth/resend-verification")
def resend_verification(request: Request, user=Depends(current_user), con=Depends(get_con)):
    _limit("code", _ip(request))
    acc.resend_verification(con, user)
    return {"ok": True}


@router.post("/auth/forgot-password")
def forgot_password(request: Request, body: dict = Body(...), con=Depends(get_con)):
    _limit("forgot", _ip(request))
    acc.forgot_password(con, body.get("email", ""))
    return {"ok": True, "message": "إذا الإيميل مسجّل عنا، رح يوصلك رمز خلال دقيقة."}


@router.post("/auth/reset-password")
def reset_password(request: Request, body: dict = Body(...), con=Depends(get_con)):
    _limit("code", _ip(request))
    return acc.reset_password(con, body.get("email", ""), body.get("code", ""), body.get("new_password", ""))


# ---------------------------------------------------------------- me
@router.get("/me")
def me(user=Depends(current_user), con=Depends(get_con)):
    return acc.public_user(con, user)


@router.patch("/me")
def update_me(body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    return acc.update_user(con, user, body)


@router.post("/me/password")
def change_password(request: Request, body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    return acc.change_password(con, user, body.get("current_password", ""), body.get("new_password", ""),
                               device=_device(request))


@router.post("/me/delete")
def delete_me(body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    acc.delete_account(con, user, body.get("password", ""))
    return {"ok": True}


@router.get("/me/jobs")
def my_jobs(user=Depends(current_user), con=Depends(get_con)):
    return {"items": acc.list_jobs(con, user["id"])}


@router.put("/me/jobs/{job_id}")
def set_my_job(job_id: int, body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    return acc.set_job_stage(con, user["id"], job_id, str(body.get("stage") or "saved"), body.get("note"))


@router.delete("/me/jobs/{job_id}")
def remove_my_job(job_id: int, user=Depends(current_user), con=Depends(get_con)):
    acc.remove_job(con, user["id"], job_id)
    return {"ok": True}


@router.get("/me/feed")
def my_feed(limit: int = Query(30, le=100), offset: int = 0, user=Depends(current_user), con=Depends(get_con)):
    """Open jobs for this person's education, cities and country, newest first; ads that don't name a degree included."""
    from .api import jobs as list_jobs
    prof = acc.profile_dict(con.execute("SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone())
    return list_jobs(edu=prof["education"], city=",".join(prof["cities"]) or None, country=prof["country"],
                     field=prof["field"] or None, limit=limit, offset=offset)


# ---------------------------------------------------------------- alerts
@router.get("/me/alerts")
def my_alerts(user=Depends(current_user), con=Depends(get_con)):
    from . import alerts
    return alerts.get_settings(con, user["id"])


@router.put("/me/alerts")
def set_my_alerts(body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    from . import alerts
    hour = body.get("daily_hour")
    return alerts.save_settings(con, user["id"], mode=body.get("mode"),
                                daily_hour=int(hour) if str(hour or "").isdigit() else None)


@router.post("/me/devices")
def add_my_device(body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    """The phone's notification token (Firebase). Sent after login and whenever Firebase renews it."""
    from . import alerts
    alerts.register_device(con, user["id"], body.get("token", ""), body.get("platform", ""))
    return {"ok": True}


@router.post("/me/devices/remove")
def remove_my_device(body: dict = Body(...), user=Depends(current_user), con=Depends(get_con)):
    """On logout: this phone stops getting this account's notifications."""
    from . import alerts
    alerts.remove_device(con, user["id"], body.get("token", ""))
    return {"ok": True}


@router.post("/me/telegram/link")
def link_my_telegram(user=Depends(current_user), con=Depends(get_con)):
    from . import alerts
    _limit("tg_link", str(user["id"]))
    return alerts.telegram_link_url(con, user["id"])


@router.delete("/me/telegram")
def unlink_my_telegram(user=Depends(current_user), con=Depends(get_con)):
    from . import alerts
    alerts.unlink_telegram(con, user_id=user["id"])
    return {"ok": True}


@router.get("/config")
def app_config():
    """Lists the app shows in its pickers, so the app and the server always agree on the codes."""
    from . import taxonomy as tx
    return {
        "site_name": config.SITE_NAME, "public_url": config.PUBLIC_URL,
        "education": [{"code": c, "label": l} for c, l in tx.EDUCATION],
        "fields": [{"code": c, "label": l} for c, l in tx.FIELDS if c != "other"],
        "countries": [{"code": "sy", "label": "سوريا", "cities": tx.CITIES},
                      {"code": "tr", "label": "تركيا", "cities": tx.TR_CITIES}],
        "genders": [{"code": "male", "label": "ذكر"}, {"code": "female", "label": "أنثى"}],
        "stages": [{"code": "saved", "label": "محفوظة"}, {"code": "applied", "label": "قدّمت"},
                   {"code": "interview", "label": "مقابلة"}, {"code": "offered", "label": "عرض عمل"},
                   {"code": "rejected", "label": "ما زبطت"}],
        "min_app_version": "1.0.0",
    }


def account_error_handler(request: Request, exc: acc.AccountError):
    return JSONResponse({"detail": exc.message, "field": exc.field, "code": exc.code}, status_code=exc.status)
