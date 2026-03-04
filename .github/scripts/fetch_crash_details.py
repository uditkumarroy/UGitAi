#!/usr/bin/env python3
"""Fetch crash title and stacktrace for CRASH_ISSUE_ID from Crashlytics BigQuery export."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from google.cloud import bigquery

from firebase_common import resolve_crashlytics_bigquery_source


def _write_outputs(title: str, stacktrace: str) -> None:
    with open("crash_title.txt", "w") as f:
        f.write(title)
    with open("crash_stacktrace.txt", "w") as f:
        f.write(stacktrace)


def _build_source_sql(project_id: str, dataset: str, batch_table: str, realtime_table: str | None) -> str:
    sources: list[str] = []
    if batch_table:
        sources.append(
            f"""
            SELECT *
            FROM `{project_id}.{dataset}.{batch_table}`
            WHERE issue_id = @issue_id
            """
        )
    if realtime_table:
        sources.append(
            f"""
            SELECT *
            FROM `{project_id}.{dataset}.{realtime_table}`
            WHERE issue_id = @issue_id
            """
        )
    if not sources:
        raise RuntimeError("No Crashlytics BigQuery table resolved.")
    return "\nUNION ALL\n".join(sources)


def _first_non_empty(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _format_stacktrace(row: dict[str, Any]) -> str:
    lines: list[str] = []
    exceptions = row.get("exceptions")
    if isinstance(exceptions, list):
        for exc in exceptions:
            if not isinstance(exc, dict):
                continue
            exc_type = _first_non_empty(exc, ["type", "name", "class"])
            exc_msg = _first_non_empty(
                exc,
                ["exception_message", "exceptionMessage", "subtitle", "message", "reason"],
            )
            if exc_type or exc_msg:
                lines.append(f"Fatal Exception: {exc_type}: {exc_msg}".strip(": "))

            frames = exc.get("frames") or exc.get("frame")
            if isinstance(frames, list):
                for frame in frames[:120]:
                    if not isinstance(frame, dict):
                        continue
                    symbol = _first_non_empty(frame, ["symbol", "method", "function", "name"]) or "?"
                    file_name = _first_non_empty(frame, ["file", "file_name", "sourceFile"]) or "?"
                    line_no = _first_non_empty(frame, ["line", "line_number", "lineNumber"]) or "?"
                    lines.append(f"\tat {symbol}({file_name}:{line_no})")
            lines.append("")

    blame_frame = row.get("blame_frame")
    if not lines and isinstance(blame_frame, dict):
        symbol = _first_non_empty(blame_frame, ["symbol", "method", "function", "name"]) or "?"
        file_name = _first_non_empty(blame_frame, ["file", "file_name", "sourceFile"]) or "?"
        line_no = _first_non_empty(blame_frame, ["line", "line_number", "lineNumber"]) or "?"
        lines.append(f"Top frame: {symbol}({file_name}:{line_no})")

    if not lines:
        lines.append("No structured stacktrace fields found in BigQuery export row.")

    return "\n".join(lines).strip()


def main() -> None:
    issue_id = os.getenv("CRASH_ISSUE_ID", "").strip()
    if not issue_id:
        raise RuntimeError("Missing CRASH_ISSUE_ID.")

    manual_title = os.getenv("MANUAL_CRASH_TITLE", "").strip()
    manual_stacktrace = os.getenv("MANUAL_CRASH_STACKTRACE", "").strip()
    if manual_title or manual_stacktrace:
        title = manual_title or f"Crashlytics issue {issue_id}"
        stacktrace = manual_stacktrace or "No stacktrace provided via workflow input."
        _write_outputs(title, stacktrace)
        print("Using manual crash details from workflow inputs.")
        print(f"Title: {title}")
        print(f"Stacktrace lines: {len(stacktrace.splitlines())}")
        return

    package_name = os.getenv("APP_PACKAGE_NAME", "com.ugitai")
    client, project_id, dataset, batch_table, realtime_table = resolve_crashlytics_bigquery_source(
        package_name=package_name,
        platform="ANDROID",
    )

    print(
        f"Fetching crash details from BigQuery: project={project_id} dataset={dataset} "
        f"batch={batch_table or '-'} realtime={realtime_table or '-'} issue={issue_id}"
    )

    source_sql = _build_source_sql(project_id, dataset, batch_table, realtime_table)
    query = f"""
    WITH source AS (
      {source_sql}
    )
    SELECT *
    FROM source
    ORDER BY event_timestamp DESC
    LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("issue_id", "STRING", issue_id)]
    )
    rows = list(client.query(query, job_config=job_config).result())
    if not rows:
        raise RuntimeError(
            f"No BigQuery crash event found for issue_id '{issue_id}' in resolved source tables."
        )

    row_dict = dict(rows[0].items())
    title = _first_non_empty(
        row_dict,
        ["issue_title", "error_type", "exception_type", "issue_type"],
    ) or f"Crashlytics issue {issue_id}"

    stacktrace = _format_stacktrace(row_dict)

    _write_outputs(title, stacktrace)

    # Keep small debug metadata for troubleshooting schema differences.
    with open("crash_event_meta.json", "w") as f:
        json.dump(
            {
                "project_id": project_id,
                "dataset": dataset,
                "batch_table": batch_table,
                "realtime_table": realtime_table,
                "issue_id": issue_id,
                "available_columns": sorted(row_dict.keys()),
            },
            f,
            indent=2,
            sort_keys=True,
        )

    print(f"Title: {title}")
    print(f"Stacktrace lines: {len(stacktrace.splitlines())}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        allow_missing = os.getenv("ALLOW_MISSING_CRASH_DETAILS", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "y",
            "on",
        )
        print(f"❌ Failed to fetch crash details: {exc}", file=sys.stderr)
        issue_id = os.getenv("CRASH_ISSUE_ID", "unknown")
        _write_outputs(issue_id, f"Could not fetch stacktrace: {exc}")
        if allow_missing:
            print("Continuing with fallback crash details because ALLOW_MISSING_CRASH_DETAILS=true.")
            sys.exit(0)
        sys.exit(1)
