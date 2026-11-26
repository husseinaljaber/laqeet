"""Accounts: sign-up, email verification, login, password reset, profile, saved jobs.

Security choices (keep them):
- passwords: scrypt (stdlib), random 16-byte salt per user, constant-time compare; never stored or logged in clear
- access token: HS256 JWT, 15 minutes, carries the user's token version (bumped on password change → all old
  tokens die); refresh token: random 32 bytes, only its SHA-256 is stored, rotated on every use, and reusing a
  rotated one revokes every session of that user (a stolen token gets noticed)
- email codes: 6 digits, hashed, 15 minutes, 5 tries, 60 s between sends
- login and «forgot password» never reveal whether an email has an account
Data choices: every field is validated here (not only in the app) against fixed lists, so the data stays clean
enough to build statistics on.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

from . import config, db
from . import taxonomy as tx

log = logging.getLogger("accounts")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,          -- lowercase, trimmed
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    gender TEXT NOT NULL CHECK (gender IN ('male', 'female')),
    password_hash TEXT NOT NULL,
    email_verified_at TEXT,
    token_version INTEGER NOT NULL DEFAULT 1,
    terms_accepted_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    signup_source TEXT                   -- app | web
);
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    education TEXT,                      -- taxonomy.EDUCATION code
    field TEXT,                          -- taxonomy.FIELDS code
    country TEXT NOT NULL DEFAULT 'sy',  -- sy | tr
    cities TEXT NOT NULL DEFAULT '',     -- |دمشق|حلب|
    experience_years INTEGER,
    birth_year INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_jobs (    -- saved jobs and where the application stands
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('saved', 'applied', 'interview', 'offered', 'rejected')),
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT UNIQUE NOT NULL,
    family TEXT NOT NULL,                -- one login = one family; rotation keeps the family
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    device TEXT
);
CREATE INDEX IF NOT EXISTS refresh_tokens_user ON refresh_tokens(user_id);
CREATE TABLE IF NOT EXISTS email_codes (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('verify', 'reset')),
    code_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    used_at TEXT
);
CREATE INDEX IF NOT EXISTS email_codes_user ON email_codes(user_id, purpose);
"""

STAGES = ("saved", "applied", "interview", "offered", "rejected")
GENDERS = ("male", "female")
CODE_MINUTES = 15
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SECONDS = 60
CODE_MAX_PER_HOUR = 5

# A short list of the passwords people pick most; anything here is refused.
_COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "qwertyui", "qwerty123", "11111111",
    "00000000", "abcd1234", "iloveyou", "aaaaaaaa", "12341234", "87654321", "123123123", "1q2w3e4r",
    "password123", "admin123", "welcome1", "syria123", "laqeet123", "11223344", "asdfghjk", "zxcvbnm1",
}


class AccountError(Exception):
    """A problem the person can fix; `message` is shown as is (Arabic), `field` says which input."""

    def __init__(self, message: str, status: int = 400, field: str | None = None, code: str = "invalid"):
        super().__init__(message)
        self.message, self.status, self.field, self.code = message, status, field, code


# ---------------------------------------------------------------- storage helpers
def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    _load_secret(con)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


_SECRET: bytes | None = None


def _load_secret(con: sqlite3.Connection) -> None:
    """Prepared once at startup (outside any request transaction): SECRET_KEY from the environment, or in
    development a random key kept in the database so logins survive restarts."""
    global _SECRET
    if config.SECRET_KEY:
        _SECRET = config.SECRET_KEY.encode("utf-8")
        return
    key = db.get_meta(con, "dev_secret_key")
    if not key:
        key = secrets.token_urlsafe(48)
        db.set_meta(con, "dev_secret_key", key)
        con.commit()
        log.warning("SECRET_KEY is not set: using a generated development key. Set SECRET_KEY in production.")
    _SECRET = key.encode("utf-8")


def _secret() -> bytes:
    if _SECRET is None:
        con = db.raw_connect()
        try:
            _load_secret(con)
        finally:
            con.close()
    return _SECRET  # type: ignore[return-value]


# ---------------------------------------------------------------- validation
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
_NAME_RE = re.compile(r"^[\w؀-ۿ' \-]+$")


def clean_email(email: str) -> str:
    e = unicodedata.normalize("NFKC", str(email or "")).strip().lower()
    if not _EMAIL_RE.match(e) or ".." in e or len(e) > 254:
        raise AccountError("اكتب إيميل صحيح، متل name@gmail.com", field="email")
    return e


def clean_name(name: str, field: str) -> str:
    n = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(name or ""))).strip()
    label = "الاسم" if field == "first_name" else "الكنية"
    if not 2 <= len(n) <= 40:
        raise AccountError(f"{label} لازم يكون بين حرفين و40 حرف", field=field)
    if not _NAME_RE.match(n) or re.search(r"\d|_", n):
        raise AccountError(f"{label} لازم يكون أحرف بس", field=field)
    return n


def check_password(password: str, email: str = "") -> str:
    p = str(password or "")
    if len(p) < 8:
        raise AccountError("كلمة السر لازم تكون 8 أحرف أو أكتر", field="password")
    if len(p) > 128:
        raise AccountError("كلمة السر طويلة كتير", field="password")
    if p.lower() in _COMMON_PASSWORDS or len(set(p)) < 4:
        raise AccountError("كلمة السر هي سهلة كتير، اختار وحدة أصعب", field="password")
    if email and p.lower() == email.split("@")[0].lower():
        raise AccountError("لا تستعمل اسم الإيميل ككلمة سر", field="password")
    return p


def clean_gender(g: str) -> str:
    g = str(g or "").strip().lower()
    if g not in GENDERS:
        raise AccountError("اختار الجنس: ذكر أو أنثى", field="gender")
    return g


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 14, 8, 1
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, h_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(str(password).encode("utf-8"), salt=base64.b64decode(salt_b64),
                           n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = None


def _burn_time(password: str) -> None:
    """Same work as a real check, so a wrong email and a wrong password take the same time."""
    global _DUMMY_HASH
    _DUMMY_HASH = _DUMMY_HASH or hash_password(secrets.token_urlsafe(12))
    verify_password(password, _DUMMY_HASH)


# ---------------------------------------------------------------- tokens
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_access_token(user_id: int, token_version: int, now: int | None = None) -> str:
    now = int(now or time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"sub": str(user_id), "ver": token_version, "iat": now,
                                  "exp": now + config.ACCESS_TOKEN_MINUTES * 60}, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def read_access_token(token: str) -> dict | None:
    """The token's claims if the signature and expiry are good, else None."""
    try:
        header, payload, sig = token.split(".")
        if json.loads(_b64url_decode(header)).get("alg") != "HS256":
            return None
        good = hmac.new(_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _b64url_decode(sig)):
            return None
        claims = json.loads(_b64url_decode(payload))
        if int(claims.get("exp", 0)) < time.time():
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _new_refresh(con, user_id: int, family: str | None = None, device: str | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    now = _now_dt()
    con.execute("INSERT INTO refresh_tokens (user_id, token_hash, family, created_at, expires_at, device) VALUES (?,?,?,?,?,?)",
                (user_id, _sha256(raw), family or secrets.token_hex(8), _iso(now),
                 _iso(now + timedelta(days=config.REFRESH_TOKEN_DAYS)), (device or "")[:80] or None))
    return raw


def _session(con, user: sqlite3.Row, device: str | None = None, family: str | None = None) -> dict:
    return {
        "access_token": make_access_token(user["id"], user["token_version"]),
        "refresh_token": _new_refresh(con, user["id"], family, device),
        "token_type": "bearer",
        "expires_in": config.ACCESS_TOKEN_MINUTES * 60,
        "user": public_user(con, user),
    }


def public_user(con, user: sqlite3.Row) -> dict:
    prof = con.execute("SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)).fetchone()
    return {
        "id": user["id"], "email": user["email"], "first_name": user["first_name"], "last_name": user["last_name"],
        "gender": user["gender"], "email_verified": bool(user["email_verified_at"]), "created_at": user["created_at"],
        "profile": profile_dict(prof),
    }


def profile_dict(prof: sqlite3.Row | None) -> dict:
    if not prof:
        return {"education": None, "field": None, "country": "sy", "cities": [], "experience_years": None,
                "birth_year": None, "complete": False}
    return {"education": prof["education"], "field": prof["field"], "country": prof["country"],
            "cities": [c for c in (prof["cities"] or "").split("|") if c],
            "experience_years": prof["experience_years"], "birth_year": prof["birth_year"],
            "complete": bool(prof["education"])}


# ---------------------------------------------------------------- email codes
def _send_code(con, user: sqlite3.Row, purpose: str) -> None:
    now = _now_dt()
    last = con.execute("SELECT created_at FROM email_codes WHERE user_id=? AND purpose=? ORDER BY id DESC LIMIT 1",
                       (user["id"], purpose)).fetchone()
    if last and (now - datetime.fromisoformat(last["created_at"])).total_seconds() < CODE_RESEND_SECONDS:
        raise AccountError("استنى دقيقة قبل ما تطلب رمز جديد", status=429, code="too_soon")
    hour_ago = _iso(now - timedelta(hours=1))
    n = con.execute("SELECT COUNT(*) FROM email_codes WHERE user_id=? AND purpose=? AND created_at>=?",
                    (user["id"], purpose, hour_ago)).fetchone()[0]
    if n >= CODE_MAX_PER_HOUR:
        raise AccountError("طلبت رموز كتير، جرّب بعد ساعة", status=429, code="too_many")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    con.execute("UPDATE email_codes SET used_at=? WHERE user_id=? AND purpose=? AND used_at IS NULL",
                (_iso(now), user["id"], purpose))  # only the newest code works
    con.execute("INSERT INTO email_codes (user_id, purpose, code_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
                (user["id"], purpose, _sha256(f"{user['id']}:{purpose}:{code}"), _iso(now),
                 _iso(now + timedelta(minutes=CODE_MINUTES))))
    con.commit()
    from . import mailer

    subject, text, html = mailer.code_email(user["first_name"], code, purpose)
    mailer.send(user["email"], subject, text, html)


def _use_code(con, user: sqlite3.Row, purpose: str, code: str) -> None:
    code = re.sub(r"\D", "", str(code or ""))
    row = con.execute("SELECT * FROM email_codes WHERE user_id=? AND purpose=? AND used_at IS NULL ORDER BY id DESC LIMIT 1",
                      (user["id"], purpose)).fetchone()
    now = _now_dt()
    if not row or datetime.fromisoformat(row["expires_at"]) < now:
        raise AccountError("الرمز انتهى، اطلب رمز جديد", field="code", code="code_expired")
    if row["attempts"] >= CODE_MAX_ATTEMPTS:
        raise AccountError("جرّبت كتير، اطلب رمز جديد", status=429, field="code", code="code_locked")
    if not hmac.compare_digest(row["code_hash"], _sha256(f"{user['id']}:{purpose}:{code}")):
        con.execute("UPDATE email_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        con.commit()
        raise AccountError("الرمز غلط، تأكد منه", field="code", code="code_wrong")
    con.execute("UPDATE email_codes SET used_at=? WHERE id=?", (_iso(now), row["id"]))


# ---------------------------------------------------------------- account actions
def _user_by_email(con, email: str):
    return con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()


def _user(con, user_id: int):
    return con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def register(con, *, first_name: str, last_name: str, gender: str, email: str, password: str,
             accepted_terms: bool, source: str = "app", device: str | None = None) -> dict:
    first, last = clean_name(first_name, "first_name"), clean_name(last_name, "last_name")
    g, e = clean_gender(gender), clean_email(email)
    p = check_password(password, e)
    if not accepted_terms:
        raise AccountError("لازم توافق على الشروط وسياسة الخصوصية", field="accepted_terms")
    if _user_by_email(con, e):
        raise AccountError("هالإيميل مسجّل عنا. سجّل دخول، أو استعمل «نسيت كلمة السر».", status=409, field="email",
                           code="email_taken")
    now = db.now()
    cur = con.execute(
        "INSERT INTO users (email, first_name, last_name, gender, password_hash, terms_accepted_at, created_at, "
        "updated_at, signup_source) VALUES (?,?,?,?,?,?,?,?,?)",
        (e, first, last, g, hash_password(p), now, now, now, source if source in ("app", "web") else "app"))
    user = _user(con, cur.lastrowid)
    session = _session(con, user, device)
    con.commit()
    try:
        _send_code(con, user, "verify")
    except AccountError:
        pass
    return session


def login(con, email: str, password: str, device: str | None = None) -> dict:
    try:
        e = clean_email(email)
    except AccountError:
        e = ""
    user = _user_by_email(con, e) if e else None
    if not user:
        _burn_time(str(password or ""))
        raise AccountError("الإيميل أو كلمة السر غلط", status=401, code="bad_login")
    if not verify_password(str(password or ""), user["password_hash"]):
        raise AccountError("الإيميل أو كلمة السر غلط", status=401, code="bad_login")
    con.execute("UPDATE users SET last_login_at=? WHERE id=?", (db.now(), user["id"]))
    session = _session(con, user, device)
    con.commit()
    return session


def refresh(con, refresh_token: str, device: str | None = None) -> dict:
    row = con.execute("SELECT * FROM refresh_tokens WHERE token_hash=?", (_sha256(str(refresh_token or "")),)).fetchone()
    now = _now_dt()
    if not row:
        raise AccountError("انتهت الجلسة، سجّل دخول من جديد", status=401, code="session_expired")
    if row["revoked_at"]:
        # a rotated token came back: someone else may hold it, so end every session of this person
        con.execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (_iso(now), row["user_id"]))
        con.commit()
        log.warning("refresh token reuse for user %s: all sessions revoked", row["user_id"])
        raise AccountError("انتهت الجلسة، سجّل دخول من جديد", status=401, code="session_expired")
    if datetime.fromisoformat(row["expires_at"]) < now:
        raise AccountError("انتهت الجلسة، سجّل دخول من جديد", status=401, code="session_expired")
    user = _user(con, row["user_id"])
    con.execute("UPDATE refresh_tokens SET revoked_at=? WHERE id=?", (_iso(now), row["id"]))
    session = _session(con, user, device or row["device"], family=row["family"])
    con.commit()
    return session


def logout(con, refresh_token: str) -> None:
    con.execute("UPDATE refresh_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (db.now(), _sha256(str(refresh_token or ""))))
    con.commit()


def user_from_access_token(con, token: str):
    claims = read_access_token(token or "")
    if not claims:
        return None
    user = _user(con, int(claims["sub"]))
    if not user or user["token_version"] != claims.get("ver"):
        return None
    return user


def resend_verification(con, user: sqlite3.Row) -> None:
    if user["email_verified_at"]:
        raise AccountError("إيميلك متأكد منه من قبل", code="already_verified")
    _send_code(con, user, "verify")


def verify_email(con, user: sqlite3.Row, code: str) -> dict:
    if not user["email_verified_at"]:
        _use_code(con, user, "verify", code)
        con.execute("UPDATE users SET email_verified_at=?, updated_at=? WHERE id=?", (db.now(), db.now(), user["id"]))
        con.commit()
    return public_user(con, _user(con, user["id"]))


def forgot_password(con, email: str) -> None:
    """Always looks the same from outside, whether the email exists or not."""
    try:
        user = _user_by_email(con, clean_email(email))
    except AccountError:
        return
    if user:
        try:
            _send_code(con, user, "reset")
        except AccountError:
            pass


def reset_password(con, email: str, code: str, new_password: str) -> dict:
    user = _user_by_email(con, clean_email(email))
    if not user:
        raise AccountError("الرمز غلط، تأكد منه", field="code", code="code_wrong")
    p = check_password(new_password, user["email"])
    _use_code(con, user, "reset", code)
    now = db.now()
    con.execute("UPDATE users SET password_hash=?, token_version=token_version+1, updated_at=?, "
                "email_verified_at=COALESCE(email_verified_at, ?) WHERE id=?", (hash_password(p), now, now, user["id"]))
    con.execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user["id"]))
    session = _session(con, _user(con, user["id"]))
    con.commit()
    return session


def change_password(con, user: sqlite3.Row, current: str, new_password: str, device: str | None = None) -> dict:
    if not verify_password(str(current or ""), user["password_hash"]):
        raise AccountError("كلمة السر الحالية غلط", status=401, field="current_password", code="bad_password")
    p = check_password(new_password, user["email"])
    now = db.now()
    con.execute("UPDATE users SET password_hash=?, token_version=token_version+1, updated_at=? WHERE id=?",
                (hash_password(p), now, user["id"]))
    con.execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user["id"]))
    session = _session(con, _user(con, user["id"]), device)
    con.commit()
    return session


def update_user(con, user: sqlite3.Row, data: dict) -> dict:
    fields = {}
    if "first_name" in data:
        fields["first_name"] = clean_name(data["first_name"], "first_name")
    if "last_name" in data:
        fields["last_name"] = clean_name(data["last_name"], "last_name")
    if "gender" in data:
        fields["gender"] = clean_gender(data["gender"])
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        con.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), db.now(), user["id"]))
    if "profile" in data:
        save_profile(con, user["id"], data["profile"] or {})
    con.commit()
    return public_user(con, _user(con, user["id"]))


def save_profile(con, user_id: int, p: dict) -> None:
    edu = p.get("education") or None
    if edu is not None and edu not in tx.EDU_RANK:
        raise AccountError("اختار الشهادة من القائمة", field="education")
    field = p.get("field") or None
    if field is not None and field not in tx.FIELD_LABEL:
        raise AccountError("اختار المجال من القائمة", field="field")
    country = "tr" if p.get("country") == "tr" else "sy"
    allowed = tx.TR_CITIES if country == "tr" else tx.CITIES
    cities = [c for c in (p.get("cities") or []) if isinstance(c, str)]
    if any(c not in allowed for c in cities):
        raise AccountError("اختار المدينة من القائمة", field="cities")
    cities = list(dict.fromkeys(cities))[:5]
    exp = p.get("experience_years")
    if exp not in (None, ""):
        if not str(exp).isdigit() or not 0 <= int(exp) <= 50:
            raise AccountError("سنين الخبرة لازم تكون رقم بين 0 و50", field="experience_years")
        exp = int(exp)
    else:
        exp = None
    born = p.get("birth_year")
    this_year = date.today().year
    if born not in (None, ""):
        if not str(born).isdigit() or not this_year - 80 <= int(born) <= this_year - 14:
            raise AccountError("سنة الميلاد مو منطقية", field="birth_year")
        born = int(born)
    else:
        born = None
    con.execute(
        "INSERT INTO user_profiles (user_id, education, field, country, cities, experience_years, birth_year, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET education=excluded.education, field=excluded.field, "
        "country=excluded.country, cities=excluded.cities, experience_years=excluded.experience_years, "
        "birth_year=excluded.birth_year, updated_at=excluded.updated_at",
        (user_id, edu, field, country, "|" + "|".join(cities) + "|" if cities else "", exp, born, db.now()))


def delete_account(con, user: sqlite3.Row, password: str) -> None:
    """Required by Google Play and the App Store: everything about the person goes, now."""
    if not verify_password(str(password or ""), user["password_hash"]):
        raise AccountError("كلمة السر غلط", status=401, field="password", code="bad_password")
    for table in ("user_jobs", "user_profiles", "refresh_tokens", "email_codes"):
        con.execute(f"DELETE FROM {table} WHERE user_id=?", (user["id"],))
    con.execute("DELETE FROM users WHERE id=?", (user["id"],))
    con.commit()


# ---------------------------------------------------------------- saved jobs
def set_job_stage(con, user_id: int, job_id: int, stage: str, note: str | None = None) -> dict:
    if stage not in STAGES:
        raise AccountError("حالة غير معروفة", field="stage")
    if not con.execute("SELECT 1 FROM jobs WHERE id=? AND status IN ('live', 'expired')", (job_id,)).fetchone():
        raise AccountError("الوظيفة مو موجودة", status=404, code="job_not_found")
    now = db.now()
    note = (str(note).strip()[:500] or None) if note is not None else None
    con.execute(
        "INSERT INTO user_jobs (user_id, job_id, stage, note, created_at, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(user_id, job_id) DO UPDATE SET stage=excluded.stage, "
        "note=COALESCE(excluded.note, user_jobs.note), updated_at=excluded.updated_at",
        (user_id, job_id, stage, note, now, now))
    con.commit()
    row = con.execute("SELECT * FROM user_jobs WHERE user_id=? AND job_id=?", (user_id, job_id)).fetchone()
    return {"job_id": job_id, "stage": row["stage"], "note": row["note"], "updated_at": row["updated_at"]}


def remove_job(con, user_id: int, job_id: int) -> None:
    con.execute("DELETE FROM user_jobs WHERE user_id=? AND job_id=?", (user_id, job_id))
    con.commit()


def list_jobs(con, user_id: int) -> list[dict]:
    out = []
    for r in con.execute("SELECT * FROM user_jobs WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall():
        job = con.execute("SELECT * FROM jobs WHERE id=?", (r["job_id"],)).fetchone()
        item = {"job_id": r["job_id"], "stage": r["stage"], "note": r["note"], "updated_at": r["updated_at"], "job": None}
        if job and job["status"] in ("live", "expired"):
            item["job"] = db.row_to_dict(con, job)
        out.append(item)
    return out


# ---------------------------------------------------------------- numbers for the team (no names, no emails)
def aggregate_stats(con) -> dict:
    users = con.execute("SELECT COUNT(*), SUM(email_verified_at IS NOT NULL) FROM users").fetchone()
    by_edu = dict(con.execute("SELECT COALESCE(education, 'unknown'), COUNT(*) FROM user_profiles GROUP BY 1").fetchall())
    by_gender = dict(con.execute("SELECT gender, COUNT(*) FROM users GROUP BY 1").fetchall())
    by_city: dict[str, int] = {}
    for (cities,) in con.execute("SELECT cities FROM user_profiles"):
        for c in filter(None, (cities or "").split("|")):
            by_city[c] = by_city.get(c, 0) + 1
    stages = dict(con.execute("SELECT stage, COUNT(*) FROM user_jobs GROUP BY 1").fetchall())
    week = _iso(_now_dt() - timedelta(days=7))
    return {"users": users[0] or 0, "verified": users[1] or 0,
            "new_7d": con.execute("SELECT COUNT(*) FROM users WHERE created_at>=?", (week,)).fetchone()[0],
            "by_education": by_edu, "by_gender": by_gender, "by_city": by_city, "applications": stages}
