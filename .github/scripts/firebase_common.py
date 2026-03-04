#!/usr/bin/env python3
"""Common helpers for Firebase Crashlytics automation."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
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


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_project_and_app_candidates(
    package_name: str = "com.ugitai",
) -> tuple[list[str], list[str]]:
    sa_project_id = ""
    sa_project_number = ""
    sa_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_json:
        try:
            sa_info = json.loads(sa_json)
            sa_project_id = str(sa_info.get("project_id", "")).strip()
            sa_project_number = str(sa_info.get("project_number", "")).strip()
        except Exception:
            # Token creation will fail later with a clearer error if this JSON is bad.
            sa_project_id = ""
            sa_project_number = ""

    with open("app/google-services.json") as f:
        gs = json.load(f)

    default_project_id = gs["project_info"]["project_id"]
    default_project_number = str(gs["project_info"].get("project_number", "")).strip()
    mobilesdk_app_id = next(
        c["client_info"]["mobilesdk_app_id"]
        for c in gs["client"]
        if c["client_info"]["android_client_info"]["package_name"] == package_name
    )
    candidates = [f"android:{package_name}", mobilesdk_app_id]
    # Preserve order while deduplicating.
    seen = set()
    ordered = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            ordered.append(c)

    project_candidates: list[str] = []
    project_candidates.extend(_split_csv(os.getenv("FIREBASE_PROJECT_ID", "").strip()))
    project_candidates.extend(_split_csv(os.getenv("FIREBASE_PROJECT_NUMBER", "").strip()))
    project_candidates.extend([sa_project_id, sa_project_number, default_project_id, default_project_number])

    dedup_projects = []
    seen_projects = set()
    for project in project_candidates:
        if project and project not in seen_projects:
            seen_projects.add(project)
            dedup_projects.append(project)

    if not dedup_projects:
        raise RuntimeError("Could not resolve Firebase project id/number from env or google-services.json.")

    override_app_resources = os.getenv("CRASHLYTICS_APP_RESOURCE", "").strip()
    if override_app_resources:
        override_candidates = _split_csv(override_app_resources)
        if not override_candidates:
            raise RuntimeError("CRASHLYTICS_APP_RESOURCE override is empty after parsing.")
        merged = []
        seen = set()
        for c in override_candidates + ordered:
            if c and c not in seen:
                seen.add(c)
                merged.append(c)
        ordered = merged

    return dedup_projects, ordered


def build_crashlytics_base_url(project_id: str, app_resource: str) -> str:
    app_part = urllib.parse.quote(app_resource, safe="")
    return f"https://firebasecrashlytics.googleapis.com/v1beta1/projects/{project_id}/apps/{app_part}"


def resolve_crashlytics_base_url(
    project_candidates: list[str],
    app_candidates: list[str],
    token: str,
) -> tuple[str, str, str]:
    last_error: Exception | None = None
    attempted: list[str] = []
    for project_id in project_candidates:
        for app_resource in app_candidates:
            base_url = build_crashlytics_base_url(project_id, app_resource)
            attempted.append(f"{project_id}|{app_resource}")
            try:
                api_get(base_url, "/issues", token, params={"pageSize": "1"})
                return base_url, project_id, app_resource
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (400, 404):
                    continue
                raise
            except Exception as exc:
                last_error = exc
                continue

    detail = f" Last error: {last_error}" if last_error else ""
    attempted_msg = f" Attempted {len(attempted)} project/app combinations: {attempted}."
    raise RuntimeError(
        f"Could not resolve Crashlytics app resource from project candidates {project_candidates} "
        f"and app candidates {app_candidates}.{attempted_msg}{detail}"
    )


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
