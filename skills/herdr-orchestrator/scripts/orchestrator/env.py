from __future__ import annotations

import os
from typing import NoReturn


class HerdrEnvError(Exception):
    def __init__(self, code: str = "herdr_env_required", message: str | None = None) -> None:
        self.code = code
        self.message = message or (
            "HERDR_ENV=1 is required. Run inside a Herdr-managed agent pane."
        )
        super().__init__(self.message)


def herdr_env_enabled() -> bool:
    return os.environ.get("HERDR_ENV") == "1"


def require_herdr_env() -> None:
    if not herdr_env_enabled():
        raise HerdrEnvError()


def herdr_env_payload() -> dict[str, object]:
    return {
        "herdr_env": herdr_env_enabled(),
        "herdr_workspace_id": os.environ.get("HERDR_WORKSPACE_ID"),
        "herdr_tab_id": os.environ.get("HERDR_TAB_ID"),
        "herdr_pane_id": os.environ.get("HERDR_PANE_ID"),
    }
