#!/usr/bin/env python3
"""
Posts row(s) of POSTSBOT.csv (message + matching image) to a Discord channel
webhook, then advances a saved pointer (state.json). When the pointer
reaches the end of the list it wraps back to row 0.

Two modes, controlled by the POST_MODE env var:
  - "single" (default): posts just the next row. Used by the hourly cron.
  - "all": posts every remaining row in the cycle back-to-back (with a
    short delay between each to stay under Discord's rate limit), ending
    with the pointer back where it started. Used for an on-demand
    "fire everything now" run.

Run by .github/workflows/hourly-post.yml.
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "POSTSBOT.csv"
IMAGES_DIR = ROOT / "images"
STATE_PATH = ROOT / "state.json"

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
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


def post_row(row):
    image_name = row["image"]
    image_path = IMAGES_DIR / image_name
    if not image_path.exists():
        raise SystemExit(f"Image file not found: {image_path}")

    ext = image_path.suffix.lower()
    mime = CONTENT_TYPES.get(ext, "application/octet-stream")

    payload = {"content": row["message"]}

    with open(image_path, "rb") as img_file:
        files = {
            "file": (image_name, img_file, mime),
        }
        data = {
            "payload_json": json.dumps(payload),
        }
        resp = requests.post(WEBHOOK_URL, data=data, files=files, timeout=30)

    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 2)
        time.sleep(float(retry_after) + 0.5)
        with open(image_path, "rb") as img_file:
            files = {"file": (image_name, img_file, mime)}
            data = {"payload_json": json.dumps(payload)}
            resp = requests.post(WEBHOOK_URL, data=data, files=files, timeout=30)

    if resp.status_code not in (200, 204):
        raise SystemExit(
            f"Discord webhook post failed ({resp.status_code}): {resp.text}"
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
    if not WEBHOOK_URL:
        raise SystemExit("DISCORD_WEBHOOK_URL environment variable is not set")

    rows = load_rows()
    total = len(rows)

    if POST_MODE == "all":
        post_all(rows, total)
    else:
        post_single(rows, total)


if __name__ == "__main__":
    sys.exit(main())
