"""Interactive helper to generate a Telethon StringSession for MTProto-only public channels.

Usage:
    python -m tools.tg_session

Obtain TG_API_ID and TG_API_HASH once from https://my.telegram.org (API development tools),
run this script locally, sign in with your Telegram phone + login code, and store the
resulting TG_STRING_SESSION (together with TG_API_ID and TG_API_HASH) in GitHub Secrets
or local environment variables so channels with disabled web previews (`syria10102`, `syria110`)
are collected automatically.
"""
from __future__ import annotations

import os


def main() -> None:
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError as exc:
        raise SystemExit("Please install telethon first: pip install telethon") from exc

    raw_id = os.getenv("TG_API_ID") or input("Enter TG_API_ID (from https://my.telegram.org): ").strip()
    api_hash = os.getenv("TG_API_HASH") or input("Enter TG_API_HASH: ").strip()
    if not raw_id.isdigit() or not api_hash:
        raise SystemExit("Invalid TG_API_ID or TG_API_HASH.")

    with TelegramClient(StringSession(), int(raw_id), api_hash) as client:
        session_str = client.session.save()
        print("\nAdd these three environment secrets (GitHub Actions / .env):")
        print(f"TG_API_ID={raw_id}")
        print(f"TG_API_HASH={api_hash}")
        print(f"TG_STRING_SESSION={session_str}\n")


if __name__ == "__main__":
    main()
