#!/usr/bin/env python3
"""Select the top Crashlytics issue in [WINDOW_START_ISO, WINDOW_END_ISO]."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

from firebase_common import (
    api_get,
    extract_event_time,
    extract_issue_id,
    extract_priority,
    get_access_token,
    load_project_and_app_candidates,
    parse_rfc3339,
    resolve_crashlytics_base_url,
)


def _append_output(name: str, value: str) -> None:
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a") as f:
        f.write(f"{name}={value}\n")


def _write_side_files(issue_id: str, meta: dict[str, Any]) -> None:
    with open("selected_issue_id.txt", "w") as f:
        f.write(issue_id)
    with open("selected_issue_meta.json", "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)


def main() -> None:
    package_name = os.getenv("APP_PACKAGE_NAME", "com.ugitai")
    project_candidates, app_candidates = load_project_and_app_candidates(package_name)

    end = parse_rfc3339(os.getenv("WINDOW_END_ISO")) or dt.datetime.now(dt.timezone.utc)
    start = parse_rfc3339(os.getenv("WINDOW_START_ISO")) or (end - dt.timedelta(hours=24))
    max_issues = int(os.getenv("MAX_ISSUES", "100"))

    token = get_access_token()
    try:
        base_url, resolved_project, resolved_app = resolve_crashlytics_base_url(
            project_candidates,
            app_candidates,
            token,
        )
        print(f"Using Crashlytics project/app: {resolved_project} / {resolved_app}")
    except Exception as exc:
        meta = {
            "no_issue": True,
            "reason": f"Could not resolve Crashlytics app: {exc}",
            "project_candidates": project_candidates,
            "app_candidates": app_candidates,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
        _write_side_files("", meta)
        _append_output("no_issue", "true")
        print(f"Could not resolve Crashlytics app. Skipping run. Details: {exc}")
        return

    print(f"Selecting issue between {start.isoformat()} and {end.isoformat()}")

    issues: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(issues) < max_issues:
        params = {"pageSize": str(min(50, max_issues - len(issues)))}
        if page_token:
            params["pageToken"] = page_token

        response = api_get(base_url, "/issues", token, params=params)
        batch = response.get("issues", [])
        if not isinstance(batch, list):
            break
        issues.extend(batch)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    ranked: list[dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        issue_id = extract_issue_id(issue)
        if not issue_id:
            continue

        try:
            events_resp = api_get(
                base_url,
                f"/issues/{issue_id}/events",
                token,
                params={"pageSize": "1"},
            )
            events = events_resp.get("events", [])
            latest_event = events[0] if isinstance(events, list) and events else {}
        except Exception:
            latest_event = {}

        event_time = extract_event_time(issue, latest_event)
        if event_time is None:
            continue
        if event_time < start or event_time > end:
            continue

        ranked.append(
            {
                "issue_id": issue_id,
                "title": str(issue.get("title", "")),
                "event_time": event_time,
                "priority": extract_priority(issue, latest_event),
                "api_rank": idx,
            }
        )

    if not ranked:
        meta = {
            "no_issue": True,
            "reason": "No issues in the selected time window",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
        }
        _write_side_files("", meta)
        _append_output("no_issue", "true")
        print("No Crashlytics issue found in the selected window.")
        return

    ranked.sort(
        key=lambda x: (x["priority"], x["event_time"], -x["api_rank"]),
        reverse=True,
    )
    top = ranked[0]

    meta = {
        "no_issue": False,
        "issue_id": top["issue_id"],
        "title": top["title"],
        "priority": top["priority"],
        "event_time": top["event_time"].isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "candidate_count": len(ranked),
    }

    _write_side_files(top["issue_id"], meta)
    _append_output("no_issue", "false")
    _append_output("issue_id", top["issue_id"])

    print(
        f"Selected issue_id={top['issue_id']} title='{top['title']}' "
        f"priority={top['priority']}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ Failed to select top issue: {exc}", file=sys.stderr)
        _append_output("no_issue", "true")
        with open("selected_issue_id.txt", "w") as f:
            f.write("")
        with open("selected_issue_meta.json", "w") as f:
            json.dump({"no_issue": True, "reason": f"selector_failed: {exc}"}, f, indent=2)
        sys.exit(1)
