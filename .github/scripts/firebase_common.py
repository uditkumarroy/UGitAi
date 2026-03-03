#!/usr/bin/env python3
"""Common helpers for Firebase Crashlytics automation."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Any

import google.auth.transport.requests
from google.oauth2 import service_account

# Hardcode Google service account values here.
# Replace every REPLACE_* value with real credentials.
HARDCODED_SERVICE_ACCOUNT: dict[str, str] = {
    "type": "service_account",
    "project_id": "test-firebase-b96cd",
    "private_key_id": "REPLACE_PRIVATE_KEY_ID",
    "private_key": "-----BEGIN PRIVATE KEY-----\\nREPLACE_PRIVATE_KEY\\n-----END PRIVATE KEY-----\\n",
    "client_email": "REPLACE_SERVICE_ACCOUNT_EMAIL@test-firebase-b96cd.iam.gserviceaccount.com",
    "client_id": "REPLACE_CLIENT_ID",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "REPLACE_CLIENT_X509_CERT_URL",
    "universe_domain": "googleapis.com",
}


def parse_rfc3339(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _assert_not_placeholder(key: str, value: str) -> None:
    upper = value.upper()
    if "REPLACE_" in upper or "YOUR_" in upper:
        raise RuntimeError(
            f"Hardcoded service account is not configured: {key} still contains placeholder text."
        )


def get_service_account_info() -> dict[str, str]:
    required = [
        "type",
        "project_id",
        "private_key",
        "client_email",
        "token_uri",
    ]
    for key in required:
        value = str(HARDCODED_SERVICE_ACCOUNT.get(key, "")).strip()
        if not value:
            raise RuntimeError(f"Hardcoded service account is missing key: {key}")
        _assert_not_placeholder(key, value)

    # Keep optional fields present for completeness, but don't block auth on them.
    for optional in ("private_key_id", "client_id"):
        value = str(HARDCODED_SERVICE_ACCOUNT.get(optional, "")).strip()
        if not value:
            HARDCODED_SERVICE_ACCOUNT[optional] = "unused"

    private_key = HARDCODED_SERVICE_ACCOUNT["private_key"]
    # Support both escaped and literal newlines.
    if "\\n" in private_key and "\n" not in private_key:
        private_key = private_key.replace("\\n", "\n")

    if not private_key.startswith("-----BEGIN PRIVATE KEY-----"):
        raise RuntimeError("Hardcoded private_key must start with '-----BEGIN PRIVATE KEY-----'.")
    if "-----END PRIVATE KEY-----" not in private_key:
        raise RuntimeError("Hardcoded private_key must include '-----END PRIVATE KEY-----'.")

    normalized = dict(HARDCODED_SERVICE_ACCOUNT)
    normalized["private_key"] = private_key
    return normalized


def get_access_token() -> str:
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
