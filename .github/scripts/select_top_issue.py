#!/usr/bin/env python3
"""Select top Crashlytics issue from BigQuery export in [WINDOW_START_ISO, WINDOW_END_ISO]."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

from google.cloud import bigquery

from firebase_common import parse_rfc3339, resolve_crashlytics_bigquery_source


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


def _build_source_sql(project_id: str, dataset: str, batch_table: str, realtime_table: str | None) -> str:
    sources: list[str] = []
    if batch_table:
        sources.append(
            f"""
            SELECT issue_id, event_timestamp
            FROM `{project_id}.{dataset}.{batch_table}`
            WHERE event_timestamp BETWEEN @window_start AND @window_end
            """
        )
    if realtime_table:
        sources.append(
            f"""
            SELECT issue_id, event_timestamp
            FROM `{project_id}.{dataset}.{realtime_table}`
            WHERE event_timestamp BETWEEN @window_start AND @window_end
            """
        )
    if not sources:
        raise RuntimeError("No Crashlytics BigQuery table resolved.")
    return "\nUNION ALL\n".join(sources)


def main() -> None:
    package_name = os.getenv("APP_PACKAGE_NAME", "com.ugitai")
    end = parse_rfc3339(os.getenv("WINDOW_END_ISO")) or dt.datetime.now(dt.timezone.utc)
    start = parse_rfc3339(os.getenv("WINDOW_START_ISO")) or (end - dt.timedelta(hours=24))

    client, project_id, dataset, batch_table, realtime_table = resolve_crashlytics_bigquery_source(
        package_name=package_name,
        platform="ANDROID",
    )
    print(
        f"Using Crashlytics BigQuery source: {project_id}.{dataset} "
        f"(batch={batch_table or '-'}, realtime={realtime_table or '-'})"
    )
    print(f"Selecting issue between {start.isoformat()} and {end.isoformat()}")

    source_sql = _build_source_sql(project_id, dataset, batch_table, realtime_table)
    query = f"""
    WITH source AS (
      {source_sql}
    )
    SELECT
      issue_id,
      COUNT(1) AS event_count,
      MAX(event_timestamp) AS latest_event_time
    FROM source
    WHERE issue_id IS NOT NULL AND issue_id != ''
    GROUP BY issue_id
    ORDER BY event_count DESC, latest_event_time DESC
    LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", start),
            bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", end),
        ],
    )
    rows = list(client.query(query, job_config=job_config).result())

    if not rows:
        meta = {
            "no_issue": True,
            "reason": "No issues in the selected time window (BigQuery)",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "project_id": project_id,
            "dataset": dataset,
            "batch_table": batch_table,
            "realtime_table": realtime_table,
        }
        _write_side_files("", meta)
        _append_output("no_issue", "true")
        print("No Crashlytics issue found in the selected window.")
        return

    row = rows[0]
    issue_id = str(row["issue_id"])
    event_count = int(row["event_count"] or 0)
    latest_event_time = row["latest_event_time"]
    latest_event_iso = latest_event_time.isoformat() if latest_event_time else ""

    meta = {
        "no_issue": False,
        "issue_id": issue_id,
        "event_count": event_count,
        "latest_event_time": latest_event_iso,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "project_id": project_id,
        "dataset": dataset,
        "batch_table": batch_table,
        "realtime_table": realtime_table,
    }

    _write_side_files(issue_id, meta)
    _append_output("no_issue", "false")
    _append_output("issue_id", issue_id)
    print(f"Selected issue_id={issue_id} event_count={event_count} latest={latest_event_iso}")


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
        sys.exit(0)
