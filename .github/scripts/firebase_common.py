#!/usr/bin/env python3
"""Common helpers for Firebase Crashlytics automation."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from typing import Any

import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account

SERVICE_ACCOUNT_GLOB = "test-firebase-b96cd-*.json"


def _resolve_service_account_file() -> Path:
    candidates = sorted(
        Path(".").glob(SERVICE_ACCOUNT_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError(f"Missing service account file matching: {SERVICE_ACCOUNT_GLOB}")
    return candidates[0]


def parse_rfc3339(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def get_service_account_info() -> dict[str, str]:
    service_account_file = _resolve_service_account_file()

    with open(service_account_file) as f:
        payload: dict[str, str] = json.load(f)

    required = [
        "type",
        "project_id",
        "private_key",
        "client_email",
        "token_uri",
    ]
    for key in required:
        value = str(payload.get(key, "")).strip()
        if not value:
            raise RuntimeError(f"Service account JSON is missing key: {key}")

    # Keep optional fields present for completeness, but don't block auth on them.
    for optional in ("private_key_id", "client_id"):
        value = str(payload.get(optional, "")).strip()
        if not value:
            payload[optional] = "unused"

    private_key = payload["private_key"]
    # Support both escaped and literal newlines.
    if "\\n" in private_key and "\n" not in private_key:
        private_key = private_key.replace("\\n", "\n")

    if not private_key.startswith("-----BEGIN PRIVATE KEY-----"):
        raise RuntimeError("Service account private_key must start with '-----BEGIN PRIVATE KEY-----'.")
    if "-----END PRIVATE KEY-----" not in private_key:
        raise RuntimeError("Service account private_key must include '-----END PRIVATE KEY-----'.")

    normalized = dict(payload)
    normalized["private_key"] = private_key
    return normalized


def get_access_token() -> str:
    # Preferred in GitHub Actions: keyless auth via Workload Identity Federation.
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        if creds.token:
            return creds.token
    except Exception as adc_error:
        # If ADC is unavailable, fall back to local JSON key file for local-only runs.
        if os.getenv("GITHUB_ACTIONS") == "true":
            raise RuntimeError(
                "Google ADC auth failed in GitHub Actions. Configure "
                "google-github-actions/auth with workload identity provider and service account. "
                f"Original error: {adc_error}"
            ) from adc_error

    creds = service_account.Credentials.from_service_account_info(
        get_service_account_info(),
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


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
