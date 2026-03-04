#!/usr/bin/env python3
"""Analyze crash context with Claude, apply code edits, and write fix_summary.md."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import anthropic


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}.")
    return value


def read_text(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def collect_context_files() -> dict[str, str]:
    cmd = [
        "find",
        "app",
        "-type",
        "f",
        "(",
        "-name",
        "*.kt",
        "-o",
        "-name",
        "*.kts",
        "-o",
        "-name",
        "*.xml",
        "-o",
        "-name",
        "*.pro",
        ")",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    files: dict[str, str] = {}
    for path in sorted(result.stdout.splitlines()):
        if "/build/" in path:
            continue
        try:
            with open(path) as f:
                files[path] = f.read()
        except Exception:
            continue

    for extra in [
        "build.gradle.kts",
        "settings.gradle.kts",
        "gradle/libs.versions.toml",
        "app/src/main/AndroidManifest.xml",
    ]:
        if Path(extra).exists() and extra not in files:
            with open(extra) as f:
                files[extra] = f.read()

    return files


def format_context(files: dict[str, str]) -> str:
    chunks: list[str] = []
    for path, content in files.items():
        lang = "kotlin"
        if path.endswith(".xml"):
            lang = "xml"
        elif path.endswith(".toml"):
            lang = "toml"
        elif path.endswith(".pro"):
            lang = "text"
        chunks.append(f"### `{path}`\n```{lang}\n{content}\n```")
    return "\n\n".join(chunks)


def is_safe_relative_path(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    return True


def write_file(path: str, content: str) -> None:
    if not is_safe_relative_path(path):
        raise RuntimeError(f"Unsafe file path requested: {path}")

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)


def write_fallback_summary(issue_id: str, reason: str) -> None:
    content = (
        f"**Issue ID:** {issue_id}\n\n"
        "**Claude Analysis:** unavailable.\n\n"
        f"**Reason:** {reason}\n\n"
        "No automated code changes were applied in this run."
    )
    with open("fix_summary.md", "w") as f:
        f.write(content)


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _model_candidates() -> list[str]:
    primary = os.getenv("ANTHROPIC_MODEL", "").strip()
    fallback_env = _split_csv(os.getenv("ANTHROPIC_MODEL_FALLBACKS", ""))
    defaults = [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-haiku-4-5-20251001",
    ]

    ordered: list[str] = []
    seen = set()
    for model in [primary] + fallback_env + defaults:
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)
    return ordered


def _is_model_not_found_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "not_found_error" in msg
        or ("model" in msg and "not found" in msg)
        or ("model:" in msg and "404" in msg)
    )


def main() -> None:
    api_key = require_env("ANTHROPIC_API_KEY")
    issue_id = require_env("CRASH_ISSUE_ID")

    crash_title = read_text("crash_title.txt")
    crash_stacktrace = read_text("crash_stacktrace.txt")

    files = collect_context_files()
    print(f"Loaded {len(files)} file(s) for Claude context.")

    client = anthropic.Anthropic(api_key=api_key)

    tools = [
        {
            "name": "apply_file",
            "description": "Write full file content to a repo-relative path.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "new_content": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["file_path", "new_content", "reason"],
            },
        },
        {
            "name": "write_summary",
            "description": "Write final human-readable summary to fix_summary.md.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "root_cause": {"type": "string"},
                    "fix": {"type": "string"},
                    "files_changed": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["root_cause", "fix", "files_changed"],
            },
        },
    ]

    prompt = f"""You are a senior Android engineer fixing a production Firebase crash.

Issue ID: {issue_id}
Title: {crash_title}

Stacktrace:
```
{crash_stacktrace}
```

Codebase:
{format_context(files)}

Instructions:
1. Identify the root cause from the stacktrace.
2. Apply the smallest safe fix.
3. Use tool apply_file for every changed file, providing complete file content.
4. Use tool write_summary once at the end.

Rules:
- Only fix crash-related code.
- Do not add unrelated refactors.
- Keep behavior unchanged except crash prevention.
"""

    messages: list[dict] = [{"role": "user", "content": prompt}]
    changed_files: list[str] = []
    summary_written = False
    selected_model = ""
    model_candidates = _model_candidates()
    print(f"Claude model candidates: {', '.join(model_candidates)}")

    for _ in range(8):
        try:
            response = None
            last_exc: Exception | None = None

            # Keep using the first successful model in subsequent turns.
            if selected_model:
                active_candidates = [selected_model]
            else:
                active_candidates = model_candidates

            for model in active_candidates:
                try:
                    response = client.messages.create(
                        model=model,
                        max_tokens=8096,
                        tools=tools,
                        messages=messages,
                    )
                    selected_model = model
                    print(f"Using Claude model: {selected_model}")
                    break
                except Exception as exc:
                    last_exc = exc
                    if _is_model_not_found_error(exc) and not selected_model:
                        print(f"Model unavailable: {model}", file=sys.stderr)
                        continue
                    raise

            if response is None:
                if last_exc is not None:
                    raise last_exc
                raise RuntimeError("Claude response was empty.")
        except Exception as exc:
            reason = f"Claude API call failed: {exc}"
            print(reason, file=sys.stderr)
            write_fallback_summary(issue_id, reason)
            if _is_truthy(os.getenv("ALLOW_CLAUDE_FAILURE", "true")):
                return
            raise

        tool_uses = [x for x in response.content if x.type == "tool_use"]
        if not tool_uses:
            break

        tool_results: list[dict] = []

        for tool_use in tool_uses:
            if tool_use.name == "apply_file":
                file_path = str(tool_use.input["file_path"]).lstrip("./")
                new_content = str(tool_use.input["new_content"])
                reason = str(tool_use.input["reason"])

                write_file(file_path, new_content)
                changed_files.append(file_path)
                print(f"Applied {file_path}: {reason}")

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": f"Wrote {file_path}",
                    }
                )

            elif tool_use.name == "write_summary":
                root_cause = str(tool_use.input["root_cause"])
                fix = str(tool_use.input["fix"])
                files_changed = tool_use.input.get("files_changed", changed_files)
                files_text = "\n".join(f"- `{f}`" for f in files_changed)
                summary = (
                    f"**Root Cause:** {root_cause}\n\n"
                    f"**Fix Applied:** {fix}\n\n"
                    f"**Files Changed:**\n{files_text}\n"
                )
                with open("fix_summary.md", "w") as f:
                    f.write(summary)
                summary_written = True

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": "Summary written to fix_summary.md",
                    }
                )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    if not summary_written:
        if changed_files:
            files_text = "\n".join(f"- `{f}`" for f in changed_files)
            content = f"**Files Changed:**\n{files_text}\n"
        else:
            content = "_No code changes were generated by Claude for this crash._"
        with open("fix_summary.md", "w") as f:
            f.write(content)

    unique_files = sorted(set(changed_files))
    print(f"Done. Changed files: {', '.join(unique_files) if unique_files else 'none'}")


if __name__ == "__main__":
    main()
