"""
Tests for foreground_cwd fallback to pane cwd (Issue #194 / FC-01 - FC-09).
Conforms to docs/herdr-orchestrator-foreground-cwd-fallback.md §9.4.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from orchestrator.live_agent import (  # noqa: E402
    LiveAgentError,
    _WARNED_PANE_CWD_PANES,
    get_live_agent,
    list_live_panes_with_ui,
    resolve_observed_cwd,
)
from orchestrator.naming import expected_agent_name  # noqa: E402
from orchestrator.role_bindings import role_bindings_file_path  # noqa: E402

CLI = SCRIPTS_DIR / "herdr-orchestrator"
_GIT_IDENTITY = ["-c", "user.name=herdr-test", "-c", "user.email=test@example.com"]


def _init_repo(root: Path, name: str) -> Path:
    repo = root / name
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", *_GIT_IDENTITY, "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", *_GIT_IDENTITY, "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    (repo / "out.md").write_text("ok\n", encoding="utf-8")
    return repo


def _write_fake_herdr(bin_dir: Path) -> Path:
    script = bin_dir / "herdr"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
from pathlib import Path

state_file = Path(os.environ["HERDR_FAKE_STATE"])
state = json.loads(state_file.read_text(encoding="utf-8"))
args = sys.argv[1:]

if args == ["--version"]:
    print("herdr 0.9.5")
    sys.exit(0)

if args[:2] == ["pane", "list"]:
    print(json.dumps({"ok": True, "result": {"panes": state.get("panes", [])}}))
    sys.exit(0)

if args[:2] == ["workspace", "list"]:
    print(json.dumps({"ok": True, "result": {"workspaces": [{"workspace_id": "w15", "label": "ws", "number": 15}]}}))
    sys.exit(0)

if args[:2] == ["tab", "list"]:
    print(json.dumps({"ok": True, "result": {"tabs": [{"tab_id": "w15:t1", "number": 1}]}}))
    sys.exit(0)

if args[:2] == ["pane", "rename"]:
    sys.exit(0)

if args[:2] == ["agent", "get"] and len(args) >= 3:
    target = args[2]
    for p in state.get("panes", []):
        if p.get("pane_id") == target or p.get("name") == target:
            print(json.dumps({"ok": True, "result": {"agent": p}}))
            sys.exit(0)
    print(json.dumps({"ok": False, "error": {"code": "agent_not_found"}}))
    sys.exit(1)

if args[:2] == ["agent", "prompt"] and len(args) >= 4:
    target = args[2]
    msg = args[3]
    log_path = Path(os.environ["HERDR_PROMPT_LOG"])
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"target": target, "msg": msg}) + "\\n")
    auto_submit_target = os.environ.get("HERDR_AUTO_SUBMIT_ON_PROMPT_TARGET")
    if auto_submit_target and target == auto_submit_target:
        m = re.search(r"task_[0-9a-f-]+", msg)
        if m:
            tid = m.group(0)
            cli = os.environ["HERDR_ORCHESTRATOR_BIN"]
            worker_cwd = os.environ["HERDR_AUTO_SUBMIT_CWD"]
            env = dict(os.environ)
            env["HERDR_PANE_ID"] = target
            env.pop("HERDR_AUTO_SUBMIT_ON_PROMPT_TARGET", None)
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys, time, subprocess; time.sleep(0.3); subprocess.run(sys.argv[1:], check=True)",
                    cli,
                    "result-submit",
                    tid,
                    "--result-status",
                    "succeeded",
                    "--result-ref",
                    "out.md",
                    "--summary",
                    "auto done",
                ],
                env=env,
                cwd=worker_cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    print(json.dumps({"ok": True}))
    sys.exit(0)

sys.exit(1)
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class ForegroundCwdFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        _WARNED_PANE_CWD_PANES.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.repo_a = _init_repo(self.tmp_root, "repo_a")
        self.repo_b = _init_repo(self.tmp_root, "repo_b")
        self.xdg_state = self.tmp_root / "xdg_state"
        self.xdg_config = self.tmp_root / "xdg_config"
        self.bin_dir = self.tmp_root / "bin"
        self.xdg_state.mkdir(parents=True, exist_ok=True)
        self.xdg_config.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        _write_fake_herdr(self.bin_dir)
        self.state_file = self.tmp_root / "herdr_state.json"
        self.prompt_log = self.tmp_root / "prompt_log.jsonl"
        self.prompt_log.write_text("", encoding="utf-8")

        self.env = dict(os.environ)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"
        self.env["XDG_STATE_HOME"] = str(self.xdg_state)
        self.env["XDG_CONFIG_HOME"] = str(self.xdg_config)
        self.env["HERDR_ENV"] = "1"
        self.env["HERDR_PANE_ID"] = "w15:p1"
        self.env["HERDR_FAKE_STATE"] = str(self.state_file)
        self.env["HERDR_PROMPT_LOG"] = str(self.prompt_log)
        self.env["HERDR_ORCHESTRATOR_BIN"] = str(CLI)
        self.env.pop("HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD", None)

        self._set_panes(
            [
                {
                    "pane_id": "w15:p1",
                    "name": "w15:p1",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_1",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_req"},
                    "foreground_cwd": str(self.repo_a.resolve()),
                    "cwd": str(self.repo_a.resolve()),
                },
                {
                    "pane_id": "w15:p2",
                    "name": "w15:p2",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_2",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_wrk"},
                    "cwd": str(self.repo_a.resolve()),
                },
            ]
        )

    def tearDown(self) -> None:
        _WARNED_PANE_CWD_PANES.clear()
        self._tmp.cleanup()

    def _set_panes(self, panes: list[dict[str, object]]) -> None:
        self.state_file.write_text(json.dumps({"panes": panes}, indent=2), encoding="utf-8")

    def _run_cli(
        self,
        *args: str,
        cwd: Path | None = None,
        env_extra: dict[str, str | None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        run_env = dict(self.env)
        if env_extra:
            for k, v in env_extra.items():
                if v is None:
                    run_env.pop(k, None)
                else:
                    run_env[k] = v
        return subprocess.run(
            [str(CLI), *args],
            capture_output=True,
            text=True,
            env=run_env,
            cwd=str(cwd or self.repo_a),
            check=False,
        )

    def _tasks_dir(self) -> Path:
        return self.xdg_state / "herdr-orchestrator" / "tasks"

    def _read_prompt_log(self) -> list[dict[str, str]]:
        lines = [ln for ln in self.prompt_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines]

    # -------------------------------------------------------------------------
    # FC-01: resolve_observed_cwd & consistency between agent get / pane list
    # -------------------------------------------------------------------------
    def test_fc01_resolve_observed_cwd_and_pathway_consistency(self) -> None:
        # (a) foreground_cwd present
        p_fg, src_fg = resolve_observed_cwd({"foreground_cwd": str(self.repo_a), "cwd": str(self.repo_b)})
        self.assertEqual(src_fg, "foreground")
        self.assertEqual(p_fg, self.repo_a)

        # (b) foreground_cwd absent, cwd present
        p_pc, src_pc = resolve_observed_cwd({"cwd": str(self.repo_a)})
        self.assertEqual(src_pc, "pane_cwd")
        self.assertEqual(p_pc, self.repo_a)

        # (c) neither present
        with self.assertRaises(LiveAgentError):
            resolve_observed_cwd({})

        # (d) relative cwd
        with self.assertRaises(LiveAgentError):
            resolve_observed_cwd({"cwd": "relative/path"})

        # Consistency between agent get and pane list
        with patch.dict(os.environ, self.env, clear=True):
            _WARNED_PANE_CWD_PANES.clear()
            panes = list_live_panes_with_ui()
            assert panes is not None
            p2 = next(p for p in panes if p["pane_id"] == "w15:p2")
            snap2 = get_live_agent("w15:p2")
            self.assertEqual(p2["cwd_source"], "pane_cwd")
            self.assertEqual(snap2.cwd_source, "pane_cwd")
            self.assertEqual(Path(p2["cwd"]).resolve(), snap2.foreground_cwd.resolve())

    # -------------------------------------------------------------------------
    # FC-02: Role Binding full flow (async) with pane_cwd worker
    # -------------------------------------------------------------------------
    def test_fc02_role_binding_async_full_flow(self) -> None:
        r_set = self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2")
        self.assertEqual(r_set.returncode, 0, r_set.stderr + r_set.stdout)
        self.assertEqual(json.loads(r_set.stdout)["cwd_source"], "pane_cwd")

        r_h = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "review")
        self.assertEqual(r_h.returncode, 0, r_h.stderr + r_h.stdout)
        d_h = json.loads(r_h.stdout)
        self.assertTrue(d_h["prompt_sent"])
        self.assertEqual(d_h["cwd_sources"], {"requester": "foreground", "worker": "pane_cwd"})
        warn_codes = [w["code"] for w in d_h.get("warnings", [])]
        self.assertIn("foreground_cwd_unavailable", warn_codes)
        self.assertIn("foreground_cwd unavailable for pane w15:p2", r_h.stderr)
        tid = d_h["task_id"]

        r_st = self._run_cli("task-status", tid, env_extra={"HERDR_PANE_ID": "w15:p2"})
        self.assertEqual(r_st.returncode, 0, r_st.stderr + r_st.stdout)
        d_st = json.loads(r_st.stdout)
        self.assertTrue(d_st["recipient_verification"]["verified"])
        self.assertEqual(d_st["recipient_verification"]["actual_caller"]["cwd_source"], "pane_cwd")
        self.assertIn("foreground_cwd unavailable for pane w15:p2", r_st.stderr)

        r_sub = self._run_cli(
            "result-submit", tid, "--result-status", "succeeded", "--result-ref", "out.md", "--summary", "ok",
            env_extra={"HERDR_PANE_ID": "w15:p2"},
        )
        self.assertEqual(r_sub.returncode, 0, r_sub.stderr + r_sub.stdout)
        reg_doc = json.loads((self._tasks_dir() / f"{tid}.json").read_text(encoding="utf-8"))
        self.assertEqual(reg_doc["result"]["status"], "succeeded")

    # -------------------------------------------------------------------------
    # FC-03: Role Binding full flow (sync) - single stderr warning per process
    # -------------------------------------------------------------------------
    def test_fc03_role_binding_sync_single_stderr_warning(self) -> None:
        self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2")
        r_h = self._run_cli(
            "handoff", "--mode", "sync", "--to", "レビュワー", "--instruction", "sync review",
            env_extra={
                "HERDR_AUTO_SUBMIT_ON_PROMPT_TARGET": "w15:p2",
                "HERDR_AUTO_SUBMIT_CWD": str(self.repo_a),
            },
        )
        self.assertEqual(r_h.returncode, 0, r_h.stderr + r_h.stdout)
        d_h = json.loads(r_h.stdout)
        self.assertTrue(d_h["result_wait"]["completed"])
        self.assertEqual(r_h.stderr.count("foreground_cwd unavailable for pane w15:p2"), 1)

    # -------------------------------------------------------------------------
    # FC-04: Pattern A rejects pane_cwd worker
    # -------------------------------------------------------------------------
    def test_fc04_pattern_a_rejects_pane_cwd(self) -> None:
        id_res = self._run_cli("identity", "init")
        self.assertEqual(id_res.returncode, 0)
        repo_id = json.loads(id_res.stdout)["repository_id"]
        pat_name = expected_agent_name(repo_id, "reviewer")
        self._set_panes(
            [
                {
                    "pane_id": "w15:p1",
                    "name": expected_agent_name(repo_id, "design"),
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_1",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_req"},
                    "foreground_cwd": str(self.repo_a.resolve()),
                    "cwd": str(self.repo_a.resolve()),
                },
                {
                    "pane_id": "w15:p2",
                    "name": pat_name,
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_2",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_wrk"},
                    "cwd": str(self.repo_a.resolve()),
                },
            ]
        )
        r_h = self._run_cli("handoff", "--mode", "async", "--to", "reviewer", "--instruction", "review")
        self.assertEqual(r_h.returncode, 1)
        err = json.loads(r_h.stdout)
        self.assertEqual(err["error"], "worker_unavailable", r_h.stdout + r_h.stderr)
        self.assertEqual(err["reason"], "foreground_cwd_required")
        self.assertFalse(self._tasks_dir().exists() and any(self._tasks_dir().glob("task_*.json")))
        self.assertEqual(self._read_prompt_log(), [])

    # -------------------------------------------------------------------------
    # FC-05: Strict mode rejects role set, handoff, task-status, result-submit
    # -------------------------------------------------------------------------
    def test_fc05_strict_mode_rejects_all_pathways(self) -> None:
        # 1. role set rejected under strict mode
        r_set_strict = self._run_cli(
            "role", "set", "レビュワー", "--pane", "w15:p2",
            env_extra={"HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD": "1"},
        )
        self.assertEqual(r_set_strict.returncode, 1)
        self.assertEqual(json.loads(r_set_strict.stdout)["error"], "role_target_foreground_cwd_required")
        id_res = self._run_cli("identity", "init")
        repo_id = json.loads(id_res.stdout)["repository_id"]
        # Role binding file must not have レビュワー
        with patch.dict(os.environ, self.env, clear=True):
            bf = role_bindings_file_path(repo_id)
            if bf.exists():
                self.assertNotIn("レビュワー", json.loads(bf.read_text(encoding="utf-8")).get("roles", {}))

        # Register under default mode
        self.assertEqual(self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2").returncode, 0)

        # 2. handoff under strict mode -> handoff_not_continuable, no prompt sent, task left created
        r_h_strict = self._run_cli(
            "handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work",
            env_extra={"HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD": "1"},
        )
        self.assertEqual(r_h_strict.returncode, 1)
        self.assertEqual(json.loads(r_h_strict.stdout)["error"], "handoff_not_continuable")
        self.assertEqual(self._read_prompt_log(), [])

        # 3. Handoff under default mode, then task-status & result-submit under strict mode
        r_h_ok = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work")
        self.assertEqual(r_h_ok.returncode, 0)
        tid = json.loads(r_h_ok.stdout)["task_id"]

        r_st_strict = self._run_cli(
            "task-status", tid,
            env_extra={"HERDR_PANE_ID": "w15:p2", "HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD": "1"},
        )
        self.assertEqual(r_st_strict.returncode, 0)
        rv = json.loads(r_st_strict.stdout)["recipient_verification"]
        self.assertFalse(rv["verified"])
        self.assertEqual(rv["reason"], "caller_live_agent_unavailable")

        r_sub_strict = self._run_cli(
            "result-submit", tid, "--result-status", "succeeded", "--result-ref", "out.md", "--summary", "ok",
            env_extra={"HERDR_PANE_ID": "w15:p2", "HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD": "1"},
        )
        self.assertEqual(r_sub_strict.returncode, 1)
        self.assertEqual(json.loads(r_sub_strict.stdout)["error"], "worker_context_unverified")

    # -------------------------------------------------------------------------
    # FC-06: Invalid env value warns on stderr and behaves as strict mode
    # -------------------------------------------------------------------------
    def test_fc06_invalid_env_value_treats_as_strict(self) -> None:
        r_set = self._run_cli(
            "role", "set", "レビュワー", "--pane", "w15:p2",
            env_extra={"HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD": "yes"},
        )
        self.assertEqual(r_set.returncode, 1)
        self.assertEqual(json.loads(r_set.stdout)["error"], "role_target_foreground_cwd_required")
        self.assertIn("invalid HERDR_ORCHESTRATOR_REQUIRE_FOREGROUND_CWD='yes'", r_set.stderr)

    # -------------------------------------------------------------------------
    # FC-07: Wrong repository cwd is rejected by existing checks
    # -------------------------------------------------------------------------
    def test_fc07_wrong_repo_cwd_rejected(self) -> None:
        self.assertEqual(self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2").returncode, 0)

        # Move w15:p2 cwd to repo_b -> handoff rejected with worker_context_changed
        panes = json.loads(self.state_file.read_text(encoding="utf-8"))["panes"]
        for p in panes:
            if p["pane_id"] == "w15:p2":
                p["cwd"] = str(self.repo_b.resolve())
        self._set_panes(panes)

        r_h = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work")
        self.assertEqual(r_h.returncode, 1)
        self.assertEqual(json.loads(r_h.stdout)["error"], "worker_context_changed")
        self.assertEqual(self._read_prompt_log(), [])

        # Restore repo_a, handoff, then move to repo_b before result-submit -> worker_context_unverified
        for p in panes:
            if p["pane_id"] == "w15:p2":
                p["cwd"] = str(self.repo_a.resolve())
        self._set_panes(panes)
        r_h2 = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work")
        self.assertEqual(r_h2.returncode, 0)
        tid = json.loads(r_h2.stdout)["task_id"]

        for p in panes:
            if p["pane_id"] == "w15:p2":
                p["cwd"] = str(self.repo_b.resolve())
        self._set_panes(panes)
        r_sub = self._run_cli(
            "result-submit", tid, "--result-status", "succeeded", "--result-ref", "out.md", "--summary", "ok",
            env_extra={"HERDR_PANE_ID": "w15:p2"},
        )
        self.assertEqual(r_sub.returncode, 1)
        self.assertEqual(json.loads(r_sub.stdout)["error"], "worker_context_unverified")

    # -------------------------------------------------------------------------
    # FC-08: completion when requester is pane_cwd
    # -------------------------------------------------------------------------
    def test_fc08_completion_when_requester_is_pane_cwd(self) -> None:
        self._set_panes(
            [
                {
                    "pane_id": "w15:p1",
                    "name": "w15:p1",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_1",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_req"},
                    "cwd": str(self.repo_a.resolve()),
                },
                {
                    "pane_id": "w15:p2",
                    "name": "w15:p2",
                    "workspace_id": "w15",
                    "tab_id": "w15:t1",
                    "terminal_id": "term_2",
                    "agent": "codex",
                    "agent_status": "idle",
                    "agent_session": {"value": "sess_wrk"},
                    "foreground_cwd": str(self.repo_a.resolve()),
                    "cwd": str(self.repo_a.resolve()),
                },
            ]
        )
        self.assertEqual(self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2").returncode, 0)
        r_h = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work")
        self.assertEqual(r_h.returncode, 0, r_h.stderr + r_h.stdout)
        d_h = json.loads(r_h.stdout)
        self.assertEqual(d_h["cwd_sources"]["requester"], "pane_cwd")
        tid = d_h["task_id"]

        r_sub = self._run_cli(
            "result-submit", tid, "--result-status", "succeeded", "--result-ref", "out.md", "--summary", "ok",
            env_extra={"HERDR_PANE_ID": "w15:p2"},
        )
        self.assertEqual(r_sub.returncode, 0, r_sub.stderr + r_sub.stdout)
        d_sub = json.loads(r_sub.stdout)
        self.assertEqual(d_sub["completion_chain"]["completion"]["status"], "sent")
        prompts = self._read_prompt_log()
        self.assertTrue(any(p["target"] == "w15:p1" and tid in p["msg"] for p in prompts))

    # -------------------------------------------------------------------------
    # FC-09: Regression when all panes have foreground_cwd
    # -------------------------------------------------------------------------
    def test_fc09_regression_all_foreground_cwd(self) -> None:
        panes = json.loads(self.state_file.read_text(encoding="utf-8"))["panes"]
        for p in panes:
            p["foreground_cwd"] = str(self.repo_a.resolve())
        self._set_panes(panes)

        self.assertEqual(self._run_cli("role", "set", "レビュワー", "--pane", "w15:p2").returncode, 0)
        r_h = self._run_cli("handoff", "--mode", "async", "--to", "レビュワー", "--instruction", "work")
        self.assertEqual(r_h.returncode, 0, r_h.stderr + r_h.stdout)
        self.assertNotIn("foreground_cwd unavailable", r_h.stderr)
        d_h = json.loads(r_h.stdout)
        self.assertEqual(d_h["cwd_sources"], {"requester": "foreground", "worker": "foreground"})
        self.assertNotIn("warnings", d_h)

        tid = d_h["task_id"]
        r_st = self._run_cli("task-status", tid, env_extra={"HERDR_PANE_ID": "w15:p2"})
        self.assertEqual(r_st.returncode, 0)
        self.assertNotIn("foreground_cwd unavailable", r_st.stderr)
        self.assertEqual(json.loads(r_st.stdout)["recipient_verification"]["actual_caller"]["cwd_source"], "foreground")


if __name__ == "__main__":
    unittest.main()
