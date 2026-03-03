#!/usr/bin/env python3
"""
Fetches crash title and stacktrace from the Firebase Crashlytics REST API
for a given issue ID, then writes them to:
  crash_title.txt       — crash title / exception class
  crash_stacktrace.txt  — formatted stacktrace from the most recent event

Required env vars:
  CRASH_ISSUE_ID              — Crashlytics issue ID
  GOOGLE_SERVICE_ACCOUNT_JSON — GCP service account key JSON (string)
"""

import json
import os
import sys
import urllib.request

import google.auth.transport.requests
from google.oauth2 import service_account

# ── Project constants (read from google-services.json) ────────────────────
with open("app/google-services.json") as f:
    _gs = json.load(f)

PROJECT_ID = _gs["project_info"]["project_id"]
APP_ID = next(
    c["client_info"]["mobilesdk_app_id"]
    for c in _gs["client"]
    if c["client_info"]["android_client_info"]["package_name"] == "com.ugitai"
)

ISSUE_ID = os.environ["CRASH_ISSUE_ID"]

BASE_URL = (
    f"https://firebasecrashlytics.googleapis.com/v1beta1"
    f"/projects/{PROJECT_ID}/apps/{APP_ID}"
)


# ── Auth ───────────────────────────────────────────────────────────────────
def load_service_account_json() -> str:
    env_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if env_value:
        return env_value
    raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON.")


def get_token() -> str:
    sa_json = load_service_account_json()
    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


# ── API helper ─────────────────────────────────────────────────────────────
def api_get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── Stacktrace formatter ───────────────────────────────────────────────────
def format_stacktrace(events: list) -> str:
    if not events:
        return "No events found for this issue."

    lines = []
    for exc in events[0].get("exceptions", []):
        exc_type = exc.get("type", "UnknownException")
        exc_msg = exc.get("exceptionMessage", "")
        lines.append(f"Fatal Exception: {exc_type}: {exc_msg}")
        for frame in exc.get("frames", []):
            symbol = frame.get("symbol", "?")
            file = frame.get("file", "?")
            line = frame.get("line", "?")
            lines.append(f"\tat {symbol}({file}:{line})")
        lines.append("")

    return "\n".join(lines).strip()


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"Fetching crash details — project: {PROJECT_ID}, issue: {ISSUE_ID}")

    token = get_token()

    # 1. Issue details → title
    issue = api_get(f"/issues/{ISSUE_ID}", token)
    subtitle = issue.get("subtitle", "")
    title = issue.get("title", "Unknown crash")
    if subtitle:
        title = f"{title} in {subtitle}"

    # 2. Most recent event → stacktrace
    events_resp = api_get(f"/issues/{ISSUE_ID}/events?pageSize=1", token)
    stacktrace = format_stacktrace(events_resp.get("events", []))

    # 3. Write outputs
    with open("crash_title.txt", "w") as f:
        f.write(title)

    with open("crash_stacktrace.txt", "w") as f:
        f.write(stacktrace)

    print(f"Title:      {title}")
    print(f"Stacktrace: {len(stacktrace.splitlines())} lines written to crash_stacktrace.txt")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Failed to fetch crash from Firebase: {e}", file=sys.stderr)
        # Write fallback files so downstream steps don't break
        with open("crash_title.txt", "w") as f:
            f.write(ISSUE_ID)
        with open("crash_stacktrace.txt", "w") as f:
            f.write(f"Could not fetch stacktrace: {e}")
        sys.exit(1)
