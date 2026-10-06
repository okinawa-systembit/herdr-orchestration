from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.live_agent import (
    LiveAgentError,
    _agent_from_herdr_payload,
    assert_promptable_state,
    get_live_agent,
    verify_live_agent_context,
)
from orchestrator.live_agent import LiveAgentSnapshot


class LiveAgentTests(unittest.TestCase):
    def test_get_live_agent_parses_payload(self) -> None:
        payload = {
            "result": {
                "agent": {
                    "name": "reviewer",
                    "agent_status": "idle",
                    "foreground_cwd": "/tmp/repo",
                    "pane_id": "w1:p2",
                }
            }
        }
        with patch.dict(os.environ, {"HERDR_ENV": "1"}):
            with patch("orchestrator.live_agent.run_herdr", return_value=payload):
                snap = get_live_agent("reviewer")
        self.assertEqual(snap.name, "reviewer")
        self.assertEqual(snap.foreground_cwd, Path("/tmp/repo"))

    def test_rejects_cwd_without_foreground_cwd(self) -> None:
        payload = {
            "result": {
                "agent": {
                    "name": "worker",
                    "agent_status": "idle",
                    "cwd": "/tmp/repo",
                }
            }
        }
        with self.assertRaises(LiveAgentError) as ctx:
            _agent_from_herdr_payload(payload)
        self.assertIn("foreground_cwd", str(ctx.exception).lower())

    def test_rejects_non_absolute_foreground_cwd(self) -> None:
        payload = {
            "result": {
                "agent": {
                    "name": "worker",
                    "agent_status": "idle",
                    "foreground_cwd": "relative/path",
                }
            }
        }
        with self.assertRaises(LiveAgentError):
            _agent_from_herdr_payload(payload)

    def test_get_live_agent_requires_herdr_env(self) -> None:
        env = os.environ.copy()
        env["HERDR_ENV"] = ""
        with patch.dict(os.environ, env, clear=True):
            with patch("orchestrator.live_agent.run_herdr") as mock_run:
                with self.assertRaises(LiveAgentError):
                    get_live_agent("reviewer")
                mock_run.assert_not_called()

    def test_malformed_payload_raises_live_agent_error(self) -> None:
        with self.assertRaises(LiveAgentError):
            _agent_from_herdr_payload({"result": "not-a-dict"})

    def test_assert_promptable_rejects_working(self) -> None:
        snap = LiveAgentSnapshot(
            name="x",
            agent_status="working",
            foreground_cwd=Path("/tmp"),
            pane_id=None,
            raw={},
        )
        with self.assertRaises(LiveAgentError):
            assert_promptable_state(snap)

    def test_verify_live_agent_context_mismatch_worktree(self) -> None:
        agent_payload = {
            "result": {
                "agent": {
                    "name": "worker",
                    "agent_status": "idle",
                    "foreground_cwd": "/tmp/repo/sub",
                    "pane_id": "w1:p3",
                }
            }
        }
        with patch.dict(os.environ, {"HERDR_ENV": "1"}):
            with patch("orchestrator.live_agent.run_herdr", return_value=agent_payload):
                with patch(
                    "orchestrator.live_agent.resolve_worktree_root",
                    return_value=Path("/tmp/repo"),
                ):
                    with patch(
                        "orchestrator.live_agent.derive_repository_identity",
                        return_value="github.com/org/repo",
                    ):
                        with self.assertRaises(LiveAgentError):
                            verify_live_agent_context(
                                "worker",
                                expected_worktree_path=Path("/other"),
                                expected_repository_identity="github.com/org/repo",
                            )


if __name__ == "__main__":
    unittest.main()
