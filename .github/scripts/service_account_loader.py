#!/usr/bin/env python3
"""
Shared loader/validator for hardcoded Google service account JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(".github/config/google-service-account.json")


def _looks_like_placeholder(value: str) -> bool:
    upper = value.strip().upper()
    return "REPLACE_WITH" in upper or upper.startswith("REPLACE_ME")


def load_service_account_json() -> str:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"Missing {CONFIG_PATH}. Add a real service account JSON file."
        )

    with CONFIG_PATH.open() as f:
        payload: dict[str, Any] = json.load(f)

    required_keys = [
        "type",
        "project_id",
        "private_key_id",
        "private_key",
        "client_email",
        "client_id",
        "token_uri",
    ]
    missing = [k for k in required_keys if not str(payload.get(k, "")).strip()]
    if missing:
        raise RuntimeError(
            f"Invalid {CONFIG_PATH}: missing required key(s): {', '.join(missing)}"
        )

    if payload.get("type") != "service_account":
        raise RuntimeError(
            f"Invalid {CONFIG_PATH}: 'type' must be 'service_account'."
        )

    private_key = str(payload.get("private_key", ""))
    if _looks_like_placeholder(private_key):
        raise RuntimeError(
            f"Invalid {CONFIG_PATH}: private_key is still a placeholder."
        )

    # Normalize if key was pasted with literal backslash-n sequences.
    if "\\n" in private_key and "\n" not in private_key:
        private_key = private_key.replace("\\n", "\n")
        payload["private_key"] = private_key

    if not private_key.startswith("-----BEGIN PRIVATE KEY-----"):
        raise RuntimeError(
            f"Invalid {CONFIG_PATH}: private_key must start with "
            "'-----BEGIN PRIVATE KEY-----'."
        )
    if "-----END PRIVATE KEY-----" not in private_key:
        raise RuntimeError(
            f"Invalid {CONFIG_PATH}: private_key must contain "
            "'-----END PRIVATE KEY-----'."
        )

    for key in ("private_key_id", "client_email", "client_id"):
        value = str(payload.get(key, ""))
        if _looks_like_placeholder(value):
            raise RuntimeError(
                f"Invalid {CONFIG_PATH}: {key} is still a placeholder."
            )

    return json.dumps(payload)
