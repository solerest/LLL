#!/usr/bin/env python3
"""
One-off helper: asks Telegram's getUpdates for any pending updates the bot
has seen (channel posts, messages, being added as admin, etc.) and writes
every distinct chat it can see into telegram_chat_candidates.json at the
repo root, so a human (or Claude, reading the committed file back) can pick
out the right channel ID without needing direct network access to Telegram.

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


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN environment variable is not set")

    resp = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/getUpdates",
        params={"limit": 100, "timeout": 0},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram getUpdates failed: {payload}")

    updates = payload.get("result", [])
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
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

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
