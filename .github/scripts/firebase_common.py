#!/usr/bin/env python3
"""Common helpers for Firebase Crashlytics automation."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request
from typing import Any

import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account


def parse_rfc3339(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def get_access_token() -> str:
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        try:
            info = json.loads(sa_json)
            private_key = str(info.get("private_key", ""))
            if "\\n" in private_key and "\n" not in private_key:
                info["private_key"] = private_key.replace("\\n", "\n")
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            creds.refresh(google.auth.transport.requests.Request())
            if creds.token:
                return creds.token
        except Exception as exc:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is set but invalid or unusable."
            ) from exc

    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token
    except Exception as exc:
        raise RuntimeError(
            "Google ADC auth failed. Configure GitHub OIDC using "
            "google-github-actions/auth and repo vars "
            "GCP_WORKLOAD_IDENTITY_PROVIDER + GCP_SERVICE_ACCOUNT_EMAIL."
        ) from exc
    raise RuntimeError("Google ADC auth returned no token.")


def load_project_and_app_id(package_name: str = "com.ugitai") -> tuple[str, str]:
    with open("app/google-services.json") as f:
        gs = json.load(f)

    project_id = gs["project_info"]["project_id"]
    app_id = next(
        c["client_info"]["mobilesdk_app_id"]
        for c in gs["client"]
        if c["client_info"]["android_client_info"]["package_name"] == package_name
    )
    return project_id, app_id


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
        value = issue.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    name = issue.get("name")
    if isinstance(name, str) and "/" in name:
        return name.rsplit("/", 1)[-1]

    return None


def extract_event_time(issue: dict[str, Any], event: dict[str, Any]) -> dt.datetime | None:
    keys = (
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
        for key in keys:
            parsed = parse_rfc3339(source.get(key))
            if parsed is not None:
                return parsed
    return None


def extract_priority(issue: dict[str, Any], event: dict[str, Any]) -> int:
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
