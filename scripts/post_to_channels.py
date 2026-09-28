#!/usr/bin/env python3
"""
Posts row(s) of POSTSBOT.csv (message + matching image) to Discord (webhook)
and/or Telegram (bot API), then advances a saved pointer (state.json) shared
by both platforms. When the pointer reaches the end of the list it wraps
back to row 0.

Each platform is independently optional: set DISCORD_WEBHOOK_URL to enable
Discord, and TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to enable Telegram. Both
can be set to post the same row to both channels every run. If neither is
set, the run fails loudly rather than silently doing nothing.

Two modes, controlled by the POST_MODE env var:
  - "single" (default): posts just the next row. Used by the hourly cron.
  - "all": posts every remaining row in the cycle back-to-back (with a
    short delay between each to stay under each platform's rate limit),
    ending with the pointer back where it started. Used for an on-demand
    "fire everything now" run.

Run by .github/workflows/hourly-post.yml.
"""

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "POSTSBOT.csv"
IMAGES_DIR = ROOT / "images"
STATE_PATH = ROOT / "state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

POST_MODE = os.environ.get("POST_MODE", "single").strip().lower()
BURST_DELAY_SECONDS = float(os.environ.get("BURST_DELAY_SECONDS", "2"))

CONTENT_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


def load_rows():
    with open(CSV_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = [
            {"message": row["message"], "image": row["image"].strip()}
            for row in reader
            if row.get("message") is not None
        ]
    if not rows:
        raise SystemExit(f"No rows found in {CSV_PATH}")
    return rows


def load_index(total):
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            idx = int(data.get("index", 0))
        except (ValueError, json.JSONDecodeError):
            idx = 0
    else:
        idx = 0
    # Guard against a stale index if the CSV shrank since the last run.
    return idx % total


def save_index(idx):
    STATE_PATH.write_text(json.dumps({"index": idx}, indent=2) + "\n", encoding="utf-8")


def image_mime(image_path):
    return CONTENT_TYPES.get(image_path.suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------- Discord --

def post_discord(row):
    image_path = IMAGES_DIR / row["image"]
    mime = image_mime(image_path)
    payload = {"content": row["message"]}

    def attempt():
        with open(image_path, "rb") as img_file:
            files = {"file": (row["image"], img_file, mime)}
            data = {"payload_json": json.dumps(payload)}
            return requests.post(DISCORD_WEBHOOK_URL, data=data, files=files, timeout=30)

    resp = attempt()
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 2)
        time.sleep(float(retry_after) + 0.5)
        resp = attempt()

    if resp.status_code not in (200, 204):
        raise SystemExit(f"Discord webhook post failed ({resp.status_code}): {resp.text}")


# --------------------------------------------------------------- Telegram --

def discord_markdown_to_telegram(text):
    # Discord uses **bold**; Telegram's legacy Markdown mode uses *bold*.
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)


def post_telegram(row):
    image_path = IMAGES_DIR / row["image"]
    mime = image_mime(image_path)
    caption = discord_markdown_to_telegram(row["message"])[:1024]
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    def attempt():
        with open(image_path, "rb") as img_file:
            files = {"photo": (row["image"], img_file, mime)}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "Markdown",
            }
            return requests.post(url, data=data, files=files, timeout=30)

    resp = attempt()
    if resp.status_code == 429:
        retry_after = resp.json().get("parameters", {}).get("retry_after", 2)
        time.sleep(float(retry_after) + 0.5)
        resp = attempt()

    ok = resp.status_code == 200 and resp.json().get("ok")
    if not ok:
        raise SystemExit(f"Telegram post failed ({resp.status_code}): {resp.text}")


# ------------------------------------------------------------------ core --

def post_row(row):
    posted_anywhere = False

    if DISCORD_WEBHOOK_URL:
        post_discord(row)
        posted_anywhere = True

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        post_telegram(row)
        posted_anywhere = True

    if not posted_anywhere:
        raise SystemExit(
            "No destination configured: set DISCORD_WEBHOOK_URL and/or "
            "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."
        )


def post_single(rows, total):
    idx = load_index(total)
    row = rows[idx]
    print(f"Posting row {idx + 1}/{total} -> image={row['image']!r}")
    post_row(row)

    next_idx = (idx + 1) % total
    save_index(next_idx)
    print(f"Posted OK. Next run will post row {next_idx + 1}/{total}.")


def post_all(rows, total):
    start_idx = load_index(total)
    print(f"Burst mode: posting all {total} rows starting at row {start_idx + 1}.")

    idx = start_idx
    for count in range(total):
        row = rows[idx]
        print(f"[{count + 1}/{total}] Posting row {idx + 1}/{total} -> image={row['image']!r}")
        post_row(row)

        next_idx = (idx + 1) % total
        save_index(next_idx)  # save progress after every post, in case of a mid-run failure
        idx = next_idx

        if count < total - 1:
            time.sleep(BURST_DELAY_SECONDS)

    print(
        f"Burst complete: posted all {total} rows. "
        f"Pointer is back at row {idx + 1}/{total}; next hourly run continues from there."
    )


def main():
    rows = load_rows()
    total = len(rows)

    if POST_MODE == "all":
        post_all(rows, total)
    else:
        post_single(rows, total)


if __name__ == "__main__":
    sys.exit(main())
