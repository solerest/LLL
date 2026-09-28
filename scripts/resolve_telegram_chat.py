#!/usr/bin/env python3
"""
One-off helper: asks Telegram's getUpdates for any pending updates the bot
has seen (channel posts, messages, being added as admin, etc.) and writes
every distinct chat it can see into telegram_chat_candidates.json at the
repo root, so a human (or Claude, reading the committed file back) can pick
out the right channel ID without needing direct network access to Telegram.

Never writes the bot token to disk or to a message it prints/returns. Any
failure is caught and written to telegram_chat_candidates.json as a
token-free diagnostic instead of raising, so this always exits 0 and the
result is always readable back from the repo (no need to dig through
Actions logs, which may be gone or masked).

Does NOT acknowledge/advance the update offset, so it's safe to re-run.

Run on demand by .github/workflows/resolve-telegram-chat.yml.
"""

import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "telegram_chat_candidates.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def write_result(payload):
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main():
    if not TOKEN:
        write_result({"ok": False, "error": "TELEGRAM_BOT_TOKEN is not set"})
        print("TELEGRAM_BOT_TOKEN is not set.")
        return

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"limit": 100, "timeout": 0},
            timeout=30,
        )
    except requests.RequestException as exc:
        # str(exc) can echo the request URL (which contains the token), so
        # only report the exception type, never its text.
        write_result({"ok": False, "error": f"network error ({type(exc).__name__})"})
        print("Request to Telegram failed (network error).")
        return

    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None

    if status != 200 or not isinstance(body, dict) or not body.get("ok"):
        # Telegram error bodies (e.g. {"ok":false,"description":"Unauthorized"})
        # never contain the token, so this is safe to write out directly.
        write_result({"ok": False, "http_status": status, "telegram_response": body})
        print(f"Telegram getUpdates failed (HTTP {status}). See telegram_chat_candidates.json for details.")
        return

    updates = body.get("result", [])
    print(f"Fetched {len(updates)} pending update(s) from Telegram.")

    chats = {}
    for update in updates:
        for key in ("channel_post", "message", "edited_channel_post", "my_chat_member"):
            obj = update.get(key)
            if not obj:
                continue
            chat = obj.get("chat")
            if not chat:
                continue
            chat_id = chat.get("id")
            chats[chat_id] = {
                "id": chat_id,
                "type": chat.get("type"),
                "title": chat.get("title") or chat.get("username") or chat.get("first_name"),
                "username": chat.get("username"),
                "seen_via": key,
            }

    result = list(chats.values())
    write_result({"ok": True, "chats": result})

    if result:
        print(f"Found {len(result)} distinct chat(s):")
        for c in result:
            print(f"  - id={c['id']}  type={c['type']}  title={c['title']!r}")
    else:
        print(
            "No chats found yet. Make sure the bot is added as an admin to "
            "the channel, then post any new message in the channel and "
            "re-run this workflow."
        )


if __name__ == "__main__":
    main()
