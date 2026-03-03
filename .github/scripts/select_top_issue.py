#!/usr/bin/env python3
"""
Selects the top Firebase Crashlytics issue between a time window:
  WINDOW_START_ISO (inclusive) -> WINDOW_END_ISO (inclusive)

Writes:
  selected_issue_id.txt    - the chosen issue ID or empty if none found
  selected_issue_meta.json - metadata about the selected issue (or no_issue marker)

Required env vars:
  GOOGLE_SERVICE_ACCOUNT_JSON

Optional env vars:
  APP_PACKAGE_NAME  (default: com.ugitai)
  WINDOW_START_ISO  (default: 24 hours before now)
  WINDOW_END_ISO    (default: now)
  MAX_ISSUES        (default: 100)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any

import google.auth.transport.requests
from google.oauth2 import service_account


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_rfc3339(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def write_outputs(issue_id: str, meta: dict[str, Any]) -> None:
    with open("selected_issue_id.txt", "w") as f:
        f.write(issue_id)
    with open("selected_issue_meta.json", "w") as f:
        json.dump(meta, f, indent=2, sort_keys=True)


def get_token(service_account_json: str) -> str:
    creds = service_account.Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def load_service_account_json() -> str:
    env_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if env_value:
        return env_value
    raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON.")


def api_get(base_url: str, path: str, token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{base_url}{path}{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def extract_issue_id(issue: dict[str, Any]) -> str | None:
    for key in ("issueId", "id"):
        if isinstance(issue.get(key), str) and issue[key].strip():
            return issue[key].strip()
    name = issue.get("name")
    if isinstance(name, str) and "/" in name:
        return name.rsplit("/", 1)[-1]
    return None


def extract_numeric_priority(issue: dict[str, Any], event: dict[str, Any]) -> int:
    keys = (
        "eventCount",
        "eventsCount",
        "fatalEventsCount",
        "fatalEventCount",
        "crashCount",
        "impactedUsersCount",
        "velocityAlertCount",
        "regressedEventCount",
    )
    for source in (issue, event):
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def extract_event_time(issue: dict[str, Any], event: dict[str, Any]) -> dt.datetime | None:
    time_keys = (
        "eventTime",
        "latestEventTime",
        "lastSeenTime",
        "updateTime",
        "createTime",
        "firstSeenTime",
        "time",
        "timestamp",
    )
    for source in (event, issue):
        for key in time_keys:
            parsed = parse_rfc3339(source.get(key))
            if parsed is not None:
                return parsed
    return None


def main() -> None:
    with open("app/google-services.json") as f:
        gs = json.load(f)

    package_name = os.getenv("APP_PACKAGE_NAME", "com.ugitai")
    project_id = gs["project_info"]["project_id"]
    app_id = next(
        c["client_info"]["mobilesdk_app_id"]
        for c in gs["client"]
        if c["client_info"]["android_client_info"]["package_name"] == package_name
    )

    start_iso = os.getenv("WINDOW_START_ISO")
    end_iso = os.getenv("WINDOW_END_ISO")
    window_end = parse_rfc3339(end_iso) or utc_now()
    window_start = parse_rfc3339(start_iso) or (window_end - dt.timedelta(hours=24))
    max_issues = int(os.getenv("MAX_ISSUES", "100"))

    service_account_json = load_service_account_json()
    token = get_token(service_account_json)

    base_url = f"https://firebasecrashlytics.googleapis.com/v1beta1/projects/{project_id}/apps/{app_id}"

    print(f"Selecting Crashlytics issue between {window_start.isoformat()} and {window_end.isoformat()}")

    issues: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(issues) < max_issues:
        params = {"pageSize": str(min(50, max_issues - len(issues)))}
        if page_token:
            params["pageToken"] = page_token
        resp = api_get(base_url, "/issues", token, params=params)
        batch = resp.get("issues", [])
        if not isinstance(batch, list):
            break
        issues.extend(batch)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not issues:
        print("No Crashlytics issues returned by API.")
        write_outputs("", {"no_issue": True, "reason": "No issues returned by API"})
        return

    ranked: list[dict[str, Any]] = []
    for idx, issue in enumerate(issues):
        issue_id = extract_issue_id(issue)
        if not issue_id:
            continue

        event_resp = api_get(base_url, f"/issues/{issue_id}/events", token, params={"pageSize": "1"})
        events = event_resp.get("events", [])
        latest_event = events[0] if isinstance(events, list) and events else {}

        event_time = extract_event_time(issue, latest_event)
        if event_time is None:
            continue
        if event_time < window_start or event_time > window_end:
            continue

        ranked.append(
            {
                "issue_id": issue_id,
                "title": issue.get("title", ""),
                "event_time": event_time,
                "priority": extract_numeric_priority(issue, latest_event),
                "api_rank": idx,
            }
        )

    if not ranked:
        print("No issues found in detection window.")
        write_outputs(
            "",
            {
                "no_issue": True,
                "reason": "No issues in time window",
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
            },
        )
        return

    ranked.sort(
        key=lambda x: (
            x["priority"],
            x["event_time"],
            -x["api_rank"],  # keep API order as final tiebreaker
        ),
        reverse=True,
    )
    top = ranked[0]
    issue_id = top["issue_id"]

    meta = {
        "no_issue": False,
        "issue_id": issue_id,
        "title": top["title"],
        "priority": top["priority"],
        "event_time": top["event_time"].isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "candidates_in_window": len(ranked),
    }
    write_outputs(issue_id, meta)
    print(f"Selected issue_id={issue_id} title='{top['title']}' priority={top['priority']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ Failed to select top issue: {exc}", file=sys.stderr)
        write_outputs("", {"no_issue": True, "reason": f"selector_failed: {exc}"})
        sys.exit(1)
