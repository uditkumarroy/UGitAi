#!/usr/bin/env python3
"""
Reads a Firebase crash from the environment / crash_stacktrace.txt,
uses Claude (with tool use) to locate the root cause in the codebase,
applies the fix in-place, and writes fix_summary.md for the PR body.

Required env vars:
  ANTHROPIC_API_KEY
"""

import os
import subprocess
import anthropic

# ── Crash details (populated by fetch_crash.py) ────────────────────────────
CRASH_ISSUE_ID = os.environ["CRASH_ISSUE_ID"]

with open("crash_title.txt") as f:
    CRASH_TITLE = f.read().strip()

with open("crash_stacktrace.txt") as f:
    CRASH_STACKTRACE = f.read().strip()


# ── Helpers ────────────────────────────────────────────────────────────────
def collect_source_files() -> dict[str, str]:
    """Return a dict of {relative_path: content} for all relevant source files."""
    result = subprocess.run(
        [
            "find", ".",
            "-name", "*.kt",
            "-not", "-path", "*/build/*",
            "-not", "-path", "*/.git/*",
        ],
        capture_output=True,
        text=True,
    )

    files: dict[str, str] = {}
    for path in result.stdout.strip().splitlines():
        try:
            with open(path) as fh:
                files[path] = fh.read()
        except Exception:
            pass

    # Include build config files — they can be the source of crashes too
    for extra in [
        "app/build.gradle.kts",
        "build.gradle.kts",
        "gradle/libs.versions.toml",
        "app/src/main/AndroidManifest.xml",
    ]:
        if extra not in files:
            try:
                with open(extra) as fh:
                    files[extra] = fh.read()
            except Exception:
                pass

    return files


def format_code_context(files: dict[str, str]) -> str:
    sections = []
    for path, content in files.items():
        lang = "kotlin" if path.endswith(".kt") else "groovy"
        sections.append(f"### `{path}`\n```{lang}\n{content}\n```")
    return "\n\n".join(sections)


def write_file(path: str, content: str) -> None:
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


def load_anthropic_api_key() -> str:
    env_value = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if env_value:
        return env_value
    raise RuntimeError("Missing ANTHROPIC_API_KEY.")


# ── Claude tools ───────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "apply_fix",
        "description": (
            "Apply a code fix to a file. Provide the COMPLETE new file content — "
            "do not use placeholders or partial snippets. "
            "Call once per file that needs changing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Repo-relative path, e.g. app/src/main/java/com/ugitai/MainActivity.kt",
                },
                "new_content": {
                    "type": "string",
                    "description": "Complete new content of the file after the fix.",
                },
                "explanation": {
                    "type": "string",
                    "description": "One sentence: what changed and why.",
                },
            },
            "required": ["file_path", "new_content", "explanation"],
        },
    },
    {
        "name": "write_fix_summary",
        "description": (
            "Call this ONCE after all apply_fix calls are done. "
            "Writes the PR summary to fix_summary.md."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root_cause": {
                    "type": "string",
                    "description": "Root cause of the crash in 1-2 sentences.",
                },
                "fix_description": {
                    "type": "string",
                    "description": "What was changed to fix it.",
                },
                "files_changed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths that were modified.",
                },
            },
            "required": ["root_cause", "fix_description", "files_changed"],
        },
    },
]


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    client = anthropic.Anthropic(api_key=load_anthropic_api_key())

    files = collect_source_files()
    print(f"📂 Loaded {len(files)} source file(s) for context.")

    prompt = f"""You are a senior Android / Kotlin engineer. A Firebase Crashlytics crash was detected in production.

## Crash Details
**Issue ID:** {CRASH_ISSUE_ID}
**Title:** {CRASH_TITLE}

**Stacktrace:**
```
{CRASH_STACKTRACE}
```

## Codebase
{format_code_context(files)}

## Instructions
1. Read the stacktrace carefully to pinpoint the exact line and root cause.
2. Identify the relevant file(s) in the codebase above.
3. Call `apply_fix` for every file that must change — supply the **complete** new file content.
4. Call `write_fix_summary` once at the end.

Rules:
- Fix only what is necessary to resolve the crash.
- Do not add unrelated refactors, comments, or new features.
- If the crash is caused by a missing null-check, add one; if it's a missing dependency, add it.
- Prefer the minimal, safest change."""

    messages = [{"role": "user", "content": prompt}]
    applied_files: list[str] = []
    summary_written = False

    # Agentic loop — Claude may call tools multiple rounds
    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8096,
            tools=TOOLS,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            # No more tool calls — we're done
            break

        tool_results = []

        for tool_use in tool_uses:
            if tool_use.name == "apply_fix":
                file_path: str = tool_use.input["file_path"]
                new_content: str = tool_use.input["new_content"]
                explanation: str = tool_use.input["explanation"]

                # Strip leading ./ if present
                file_path = file_path.lstrip("./")
                if not file_path.startswith("app/") and not file_path.startswith("gradle/"):
                    file_path = file_path  # trust Claude's path

                write_file(file_path, new_content)
                applied_files.append(file_path)
                print(f"✅ Fixed: {file_path}\n   → {explanation}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": f"Fix applied to {file_path}",
                })

            elif tool_use.name == "write_fix_summary":
                root_cause: str = tool_use.input["root_cause"]
                fix_description: str = tool_use.input["fix_description"]
                files_changed: list[str] = tool_use.input.get("files_changed", applied_files)

                file_list = "\n".join(f"- `{f}`" for f in files_changed)
                summary = (
                    f"**Root Cause:** {root_cause}\n\n"
                    f"**Fix Applied:** {fix_description}\n\n"
                    f"**Files Changed:**\n{file_list}"
                )

                with open("fix_summary.md", "w") as fh:
                    fh.write(summary)

                summary_written = True
                print("\n📝 PR summary written to fix_summary.md")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Summary written.",
                })

        # Feed results back and continue
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if response.stop_reason == "end_turn":
            break

    # Fallback summary if Claude skipped write_fix_summary
    if not summary_written:
        if applied_files:
            file_list = "\n".join(f"- `{f}`" for f in applied_files)
            with open("fix_summary.md", "w") as fh:
                fh.write(f"**Files Changed:**\n{file_list}")
        else:
            with open("fix_summary.md", "w") as fh:
                fh.write(
                    "_Claude could not identify a code-level fix for this crash. "
                    "Manual investigation required._"
                )

    print(f"\n🎉 Done. Modified {len(applied_files)} file(s): {', '.join(applied_files) or 'none'}")


if __name__ == "__main__":
    main()
