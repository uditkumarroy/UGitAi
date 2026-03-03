#!/usr/bin/env python3
"""Fetch crash title and stacktrace for CRASH_ISSUE_ID from Crashlytics."""

from __future__ import annotations

import json
import os
import sys

from firebase_common import api_get, get_access_token, load_project_and_app_id


def format_stacktrace(events: list[dict]) -> str:
    if not events:
        return "No events found for this issue."

    lines: list[str] = []
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


def main() -> None:
    issue_id = os.getenv("CRASH_ISSUE_ID", "").strip()
    if not issue_id:
        raise RuntimeError("Missing CRASH_ISSUE_ID.")

    package_name = os.getenv("APP_PACKAGE_NAME", "com.ugitai")
    project_id, app_id = load_project_and_app_id(package_name)
    base_url = f"https://firebasecrashlytics.googleapis.com/v1beta1/projects/{project_id}/apps/{app_id}"

    token = get_access_token()

    print(f"Fetching crash details: project={project_id} issue={issue_id}")

    issue = api_get(base_url, f"/issues/{issue_id}", token)
    title = issue.get("title", "Unknown crash")
    subtitle = issue.get("subtitle", "")
    if subtitle:
        title = f"{title} in {subtitle}"

    events_resp = api_get(base_url, f"/issues/{issue_id}/events", token, params={"pageSize": "1"})
    stacktrace = format_stacktrace(events_resp.get("events", []))

    with open("crash_title.txt", "w") as f:
        f.write(str(title))

    with open("crash_stacktrace.txt", "w") as f:
        f.write(stacktrace)

    print(f"Title: {title}")
    print(f"Stacktrace lines: {len(stacktrace.splitlines())}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ Failed to fetch crash details: {exc}", file=sys.stderr)
        issue_id = os.getenv("CRASH_ISSUE_ID", "unknown")
        with open("crash_title.txt", "w") as f:
            f.write(issue_id)
        with open("crash_stacktrace.txt", "w") as f:
            f.write(f"Could not fetch stacktrace: {exc}")
        sys.exit(1)
