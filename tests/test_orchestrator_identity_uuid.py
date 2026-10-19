"""
Comprehensive tests for Herdr Orchestrator Repository Identity UUID implementation.
Mapped directly to docs/herdr-orchestrator-identity-uuid.md §10:
  §10.1 (ID-01 ~ ID-08): UUID Initialization & Idempotency
  §10.2 (GIT-01 ~ GIT-08): Git Environment & Uninitialized Handling
  §10.3 (WT-01 ~ WT-06): git worktree Sharing & Isolation
  §10.4 (AG-01 ~ AG-09): Agent Naming & Registry Affinity
  CLI tests: identity init / identity show
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

from orchestrator.identity import (
    GitRepositoryNotInitializedError,
    RepositoryIdentityError,
    get_or_create_repository_identity,
    get_repository_identity,
    is_valid_uuid_v4,
    resolve_git_common_dir,
)
from orchestrator.live_agent import LiveAgentError, verify_worktree_affinity
from orchestrator.naming import (
    derive_requester_role,
    expected_agent_name,
    hash16_from_repository_identity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "herdr-orchestrator" / "scripts" / "herdr-orchestrator"

_GIT_IDENTITY = ["-c", "user.name=herdr-orchestrator-test", "-c", "user.email=test@example.com"]


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(worktree: Path) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "init", "-b", "main", str(worktree)],
        check=True,
        capture_output=True,
        text=True,
    )
    (worktree / "README.md").write_text("test\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "commit", "-m", "init")


def _worker_fn_get_or_create(worktree_str: str) -> str:
    from orchestrator.identity import get_or_create_repository_identity
    return get_or_create_repository_identity(Path(worktree_str))


import stat

class OrchestratorIdentityUuidTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.worktree = self.root / "repo"
        _init_repo(self.worktree)

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        fake_herdr = bin_dir / "herdr"
        fake_herdr.write_text(
            """#!/usr/bin/env python3
import json, os, sys
if len(sys.argv) >= 4 and sys.argv[1] == "agent" and sys.argv[2] == "get":
    cwd = os.environ.get("FAKE_AGENT_CWD", os.getcwd())
    name = os.environ.get("FAKE_AGENT_NAME", "caller-agent")
    pane = os.environ.get("FAKE_AGENT_PANE", "w11:p1")
    agent = {
        "name": name,
        "agent_status": "idle",
        "foreground_cwd": cwd,
        "pane_id": pane,
    }
    print(json.dumps({"result": {"agent": agent}}))
    sys.exit(0)
sys.exit(1)
""",
            encoding="utf-8",
        )
        fake_herdr.chmod(fake_herdr.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.bin_dir = bin_dir

    # -------------------------------------------------------------------------
    # §10.1 UUID Initialization
    # -------------------------------------------------------------------------

    def test_id01_init_immediately_after_git_init(self) -> None:
        """ID-01: git init 直後に初期化 -> UUID 生成成功"""
        bare_repo = self.root / "bare-init"
        _git(self.root, "init", "-b", "main", str(bare_repo))
        uuid_val = get_or_create_repository_identity(bare_repo)
        self.assertTrue(is_valid_uuid_v4(uuid_val))
        # Verify readback matches
        self.assertEqual(get_repository_identity(bare_repo), uuid_val)

    def test_id02_init_with_origin_configured(self) -> None:
        """ID-02: origin 設定済みで初期化 -> UUID 生成成功"""
        _git(self.worktree, "remote", "add", "origin", "https://github.com/org/repo.git")
        uuid_val = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid_val))

    def test_id03_reexecution_returns_same_uuid(self) -> None:
        """ID-03: 初期化済みリポジトリで再実行 -> 同じ UUID（既存 UUID 再利用）"""
        uuid1 = get_or_create_repository_identity(self.worktree)
        uuid2 = get_or_create_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid2)
        uuid3 = get_repository_identity(self.worktree)
        self.assertEqual(uuid1, uuid3)

    def test_id04_cli_manual_init_then_handoff_reuses_uuid(self) -> None:
        """ID-04: CLI 手動初期化後に handoff -> UUID 再利用"""
        # 1. Run herdr-orchestrator identity init
        res = subprocess.run(
            [sys.executable, str(CLI), "identity", "init"],
            cwd=str(self.worktree),
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data["command"], "identity")
        self.assertEqual(data["action"], "init")
        manual_uuid = data["repository_id"]
        self.assertTrue(is_valid_uuid_v4(manual_uuid))

        # 2. get_or_create should re-use this exact UUID
        uuid_after = get_or_create_repository_identity(self.worktree)
        self.assertEqual(uuid_after, manual_uuid)

    def test_id05_handoff_auto_initialization(self) -> None:
        """ID-05: handoff による自動初期化 -> UUID 未初期化でも自動初期化成功"""
        # Ensure not yet initialized
        with self.assertRaises(RepositoryIdentityError):
            get_repository_identity(self.worktree)

        # Trigger get_or_create as handoff does
        uuid_val = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid_val))
        self.assertEqual(get_repository_identity(self.worktree), uuid_val)

    def test_id06_concurrent_initialization_acquires_lock(self) -> None:
        """ID-06: 同時初期化 -> 同じ UUID に確定（排他制御）"""
        target_repo = self.root / "concurrent-repo"
        _init_repo(target_repo)

        # Execute concurrent calls across separate processes
        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(_worker_fn_get_or_create, str(target_repo))
                for _ in range(8)
            ]
            results = [f.result() for f in futures]

        self.assertEqual(len(set(results)), 1)
        self.assertTrue(is_valid_uuid_v4(results[0]))
        self.assertEqual(get_repository_identity(target_repo), results[0])

    def test_id07_invalid_uuid_format_fails_without_regeneration(self) -> None:
        """ID-07: UUID 形式不正 -> エラー、自動再生成なし"""
        _git(self.worktree, "config", "--file", ".git/config", "herdr.repository-id", "invalid-uuid-format")

        with self.assertRaises(RepositoryIdentityError) as ctx1:
            get_repository_identity(self.worktree)
        self.assertIn("invalid repository identity format", str(ctx1.exception))

        with self.assertRaises(RepositoryIdentityError) as ctx2:
            get_or_create_repository_identity(self.worktree)
        self.assertIn("invalid repository identity format", str(ctx2.exception))

        # Check that config was NOT overwritten
        res = subprocess.run(
            ["git", "-C", str(self.worktree), "config", "--file", ".git/config", "--get", "herdr.repository-id"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(res.stdout.strip(), "invalid-uuid-format")

    def test_id08_git_config_write_or_verify_failure_aborts(self) -> None:
        """ID-08: Git config 書き込み・読み直し失敗 -> エラー返却、UUID を返さない"""
        with patch("orchestrator.identity._read_uuid_from_git_config", side_effect=[None, "mismatched-readback"]):
            with self.assertRaises(RepositoryIdentityError) as ctx:
                get_or_create_repository_identity(self.worktree)
            self.assertIn("write verification failed", str(ctx.exception))

    # -------------------------------------------------------------------------
    # §10.2 Git Environment
    # -------------------------------------------------------------------------

    def test_git01_uninitialized_directory_cli_error(self) -> None:
        """GIT-01: Git 未初期化時の CLI 動作 -> git_repository_not_initialized 専用エラー、非ゼロ終了"""
        empty_dir = self.root / "not-a-git-repo"
        empty_dir.mkdir()

        for subcmd in (["identity", "init"], ["identity", "show"]):
            res = subprocess.run(
                [sys.executable, str(CLI), *subcmd],
                cwd=str(empty_dir),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data.get("error"), "git_repository_not_initialized")

    def test_git02_uninitialized_skill_behavior_and_no_auto_git_init(self) -> None:
        """GIT-02: Git 未初期化時の Skill 動作（責務） -> git init の自動実行禁止確認"""
        empty_dir = self.root / "no-git-dir"
        empty_dir.mkdir()

        res = subprocess.run(
            [sys.executable, str(CLI), "identity", "init"],
            cwd=str(empty_dir),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        # Ensure .git was NOT created
        self.assertFalse((empty_dir / ".git").exists())

    def test_git03_uninitialized_does_not_create_task_registry_entry(self) -> None:
        """GIT-03: Git 未初期化時の Task Registry -> 不完全なタスクを作成しない"""
        empty_dir = self.root / "empty-repo"
        empty_dir.mkdir()
        env = dict(
            os.environ,
            HERDR_ENV="1",
            HERDR_PANE_ID="w11:p1",
            PATH=f"{self.bin_dir}:{os.environ.get('PATH', '')}",
            FAKE_AGENT_CWD=str(empty_dir),
        )
        res = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "handoff",
                "--mode",
                "sync",
                "--worker-role",
                "reviewer",
                "--task-type",
                "review",
                "--instruction",
                "test",
            ],
            cwd=str(empty_dir),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("error"), "git_repository_not_initialized")
        # Ensure registry directory/file was not created inside empty_dir
        self.assertFalse((empty_dir / ".herdr").exists())

    def test_git04_origin_unset_handoff_succeeds(self) -> None:
        """GIT-04: origin 未設定 -> UUID 生成・取得成功"""
        # worktree has no origin set
        uuid_val = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid_val))
        self.assertEqual(get_repository_identity(self.worktree), uuid_val)

    def test_git05_origin_url_change_maintains_uuid(self) -> None:
        """GIT-05: origin URL 変更 -> UUID 維持"""
        uuid_orig = get_or_create_repository_identity(self.worktree)
        _git(self.worktree, "remote", "add", "origin", "https://github.com/org1/repo1.git")
        self.assertEqual(get_repository_identity(self.worktree), uuid_orig)

        _git(self.worktree, "remote", "set-url", "origin", "https://github.com/org2/repo2.git")
        self.assertEqual(get_repository_identity(self.worktree), uuid_orig)

    def test_git06_origin_removal_maintains_uuid(self) -> None:
        """GIT-06: origin 削除 -> UUID 維持"""
        _git(self.worktree, "remote", "add", "origin", "https://github.com/org/repo.git")
        uuid_orig = get_or_create_repository_identity(self.worktree)

        _git(self.worktree, "remote", "remove", "origin")
        self.assertEqual(get_repository_identity(self.worktree), uuid_orig)

    def test_git07_japanese_directory_name(self) -> None:
        """GIT-07: 日本語ディレクトリ名 -> UUID 生成・取得成功"""
        jp_dir = self.root / "日本語のリポジトリ_テスト"
        _init_repo(jp_dir)

        uuid_val = get_or_create_repository_identity(jp_dir)
        self.assertTrue(is_valid_uuid_v4(uuid_val))
        self.assertEqual(get_repository_identity(jp_dir), uuid_val)

        # Agent name generation should succeed cleanly
        agent_name = expected_agent_name(uuid_val, "reviewer")
        self.assertTrue(agent_name.startswith("r-"))
        self.assertTrue(agent_name.endswith("-reviewer"))

    def test_git08_directory_move_or_rename_maintains_uuid(self) -> None:
        """GIT-08: ディレクトリ移動・改名 -> UUID 維持"""
        uuid_orig = get_or_create_repository_identity(self.worktree)
        name_orig = expected_agent_name(uuid_orig, "orchestrator")

        renamed = self.root / "repo_renamed"
        self.worktree.rename(renamed)

        uuid_renamed = get_repository_identity(renamed)
        self.assertEqual(uuid_orig, uuid_renamed)
        name_renamed = expected_agent_name(uuid_renamed, "orchestrator")
        self.assertEqual(name_orig, name_renamed)

    # -------------------------------------------------------------------------
    # §10.3 git worktree
    # -------------------------------------------------------------------------

    def test_wt01_main_worktree_uuid_generation(self) -> None:
        """WT-01: メイン worktree で UUID 生成 -> 正常登録"""
        uuid_main = get_or_create_repository_identity(self.worktree)
        self.assertTrue(is_valid_uuid_v4(uuid_main))
        # Ensure it is in .git/config
        cfg = (self.worktree / ".git" / "config").read_text(encoding="utf-8")
        self.assertIn(uuid_main, cfg)

    def test_wt02_linked_worktree_reads_same_uuid(self) -> None:
        """WT-02: linked worktree から UUID 取得 -> 同じ UUID"""
        uuid_main = get_or_create_repository_identity(self.worktree)
        linked_wt = self.root / "linked-wt"
        _git(self.worktree, "worktree", "add", "-b", "feature", str(linked_wt))

        uuid_linked = get_repository_identity(linked_wt)
        self.assertEqual(uuid_main, uuid_linked)

    def test_wt03_linked_worktree_first_init(self) -> None:
        """WT-03: linked worktree から初回 UUID 生成 -> 共通 Git config に登録"""
        linked_wt = self.root / "linked-first-init"
        _git(self.worktree, "worktree", "add", "-b", "feat-first", str(linked_wt))

        uuid_linked = get_or_create_repository_identity(linked_wt)
        self.assertTrue(is_valid_uuid_v4(uuid_linked))

        # Main worktree should see the same UUID from common dir
        uuid_main = get_repository_identity(self.worktree)
        self.assertEqual(uuid_linked, uuid_main)

    def test_wt04_concurrent_init_across_worktrees(self) -> None:
        """WT-04: 複数 worktree から同時初期化 -> 同じ UUID（共通ロック排他制御）"""
        target_main = self.root / "wt-target-main"
        _init_repo(target_main)
        target_linked = self.root / "wt-target-linked"
        _git(target_main, "worktree", "add", "-b", "feat-concurrent", str(target_linked))

        with ProcessPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(_worker_fn_get_or_create, str(target_main if i % 2 == 0 else target_linked))
                for i in range(8)
            ]
            results = [f.result() for f in futures]

        self.assertEqual(len(set(results)), 1)
        self.assertTrue(is_valid_uuid_v4(results[0]))
        self.assertEqual(get_repository_identity(target_main), results[0])
        self.assertEqual(get_repository_identity(target_linked), results[0])

    def test_wt05_worktree_path_mismatch_fails_affinity(self) -> None:
        """WT-05: worktree path 不一致 -> 安全停止 (worker_context_unverified)"""
        uuid_val = get_or_create_repository_identity(self.worktree)
        linked_wt = self.root / "wt-diff-path"
        _git(self.worktree, "worktree", "add", "-b", "feat-diff", str(linked_wt))

        # But expected worktree path is the main worktree!
        with self.assertRaises(LiveAgentError) as ctx:
            verify_worktree_affinity(
                linked_wt,
                expected_worktree_path=self.worktree,
                expected_repository_identity=uuid_val,
            )
        self.assertIn("worktree mismatch", str(ctx.exception))

    def test_wt06_config_worktree_isolation(self) -> None:
        """WT-06: config.worktree に同名キー存在 -> 共通 config の UUID を確実に取得"""
        uuid_main = get_or_create_repository_identity(self.worktree)
        linked_wt = self.root / "wt-config-iso"
        _git(self.worktree, "worktree", "add", "-b", "feat-config-iso", str(linked_wt))

        # Enable worktreeConfig and set herdr.repository-id to a different value in worktree config
        _git(self.worktree, "config", "extensions.worktreeConfig", "true")
        _git(linked_wt, "config", "--worktree", "herdr.repository-id", "11111111-2222-4333-8444-555555555555")

        # get_repository_identity MUST read from <git-common-dir>/config directly and return uuid_main
        uuid_read = get_repository_identity(linked_wt)
        self.assertEqual(uuid_read, uuid_main)

    # -------------------------------------------------------------------------
    # §10.4 Agent & Task Registry
    # -------------------------------------------------------------------------

    def test_ag01_pattern_a_agent_name(self) -> None:
        """AG-01: パターン A による Agent 名生成 (r-<hash16>-<role>) -> 決定論的な小文字ハイフン名"""
        uuid_val = "550e8400-e29b-41d4-a716-446655440000"
        digest16 = hashlib.sha256(uuid_val.encode("ascii")).hexdigest()[:16].lower()
        self.assertEqual(hash16_from_repository_identity(uuid_val), digest16)

        name = expected_agent_name(uuid_val, "reviewer")
        self.assertEqual(name, f"r-{digest16}-reviewer")
        self.assertEqual(derive_requester_role(uuid_val, name), "reviewer")

    def test_ag02_max_length_constraint(self) -> None:
        """AG-02: Agent 名最大長検証（最長 orchestrator） -> 31 文字 (<= 32 文字制約)"""
        uuid_val = "550e8400-e29b-41d4-a716-446655440000"
        name = expected_agent_name(uuid_val, "orchestrator")
        # 'r-' (2) + 16 chars + '-' (1) + 'orchestrator' (12) = 31 chars
        self.assertEqual(len(name), 31)
        self.assertLessEqual(len(name), 32)

    def test_ag03_same_uuid_role_deterministic(self) -> None:
        """AG-03: 同じ UUID・role で再起動 -> 同じ Agent 名"""
        uuid_val = "c9a646d3-9c61-4cb7-89cd-4c2dd81c7ff3"
        name1 = expected_agent_name(uuid_val, "implementer")
        name2 = expected_agent_name(uuid_val, "implementer")
        self.assertEqual(name1, name2)

    def test_ag04_worker_uninitialized_uuid_raises(self) -> None:
        """AG-04: worker 側で UUID 未登録 -> 自動生成せずエラー停止"""
        uninit_repo = self.root / "worker-uninit"
        _init_repo(uninit_repo)

        with self.assertRaises(RepositoryIdentityError) as ctx:
            get_repository_identity(uninit_repo)
        self.assertIn("repository identity not initialized", str(ctx.exception))

    def test_ag05_repository_identity_mismatch_fails(self) -> None:
        """AG-05: Repository Identity 不一致 -> 安全停止"""
        uuid_actual = get_or_create_repository_identity(self.worktree)
        uuid_expected = "22222222-2222-4222-8222-222222222222"
        with self.assertRaises(LiveAgentError) as ctx:
            verify_worktree_affinity(
                self.worktree,
                expected_worktree_path=self.worktree,
                expected_repository_identity=uuid_expected,
            )
        self.assertIn("repository identity mismatch", str(ctx.exception))

    def test_ag06_legacy_origin_identity_not_accepted(self) -> None:
        """AG-06: 旧 origin 方式の Task Registry -> 誤って引き継がない / 形式不正"""
        legacy_id = "github.com/org/repo"
        self.assertFalse(is_valid_uuid_v4(legacy_id))

        _git(self.worktree, "config", "--file", ".git/config", "herdr.repository-id", legacy_id)
        with self.assertRaises(RepositoryIdentityError) as ctx:
            get_repository_identity(self.worktree)
        self.assertIn("invalid repository identity format", str(ctx.exception))

    def test_ag07_different_uuids_produce_different_names(self) -> None:
        """AG-07: 異なる UUID での Agent 名分離 -> 異なる UUID で異なる Agent 名が生成されること"""
        uuid1 = "11111111-1111-4111-8111-111111111111"
        uuid2 = "22222222-2222-4222-8222-222222222222"
        name1 = expected_agent_name(uuid1, "reviewer")
        name2 = expected_agent_name(uuid2, "reviewer")
        self.assertNotEqual(name1, name2)

    def test_ag08_path_independent_agent_name(self) -> None:
        """AG-08: ディレクトリ名改名・パス変更時の Agent 名 -> Agent 名が一切変化しない"""
        uuid_val = get_or_create_repository_identity(self.worktree)
        name1 = expected_agent_name(uuid_val, "design")

        # Change directory path completely
        new_path = self.root / "completely_different_path"
        self.worktree.rename(new_path)
        uuid_read = get_repository_identity(new_path)
        name2 = expected_agent_name(uuid_read, "design")
        self.assertEqual(name1, name2)

    def test_ag09_same_uuid_different_worktree_rejected(self) -> None:
        """AG-09: 同一 UUID かつ別 worktree path での worker 照合 -> 安全停止（誤配送抑止）"""
        uuid_val = get_or_create_repository_identity(self.worktree)
        other_wt = self.root / "other-wt-worker"
        _git(self.worktree, "worktree", "add", "-b", "other-branch", str(other_wt))

        with self.assertRaises(LiveAgentError) as ctx:
            verify_worktree_affinity(
                other_wt,
                expected_worktree_path=self.worktree,
                expected_repository_identity=uuid_val,
            )
        self.assertIn("worktree mismatch", str(ctx.exception))

    # -------------------------------------------------------------------------
    # CLI Tests (identity init / identity show)
    # -------------------------------------------------------------------------

    def test_cli_identity_init_and_show_success(self) -> None:
        """CLI identity init & show successful workflow"""
        # 1. show before init -> failure exit 1 with repository_identity_not_initialized
        res = subprocess.run(
            [sys.executable, str(CLI), "identity", "show"],
            cwd=str(self.worktree),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        err = json.loads(res.stdout)
        self.assertEqual(err["error"], "repository_identity_not_initialized")

        # 2. init -> success exit 0
        res = subprocess.run(
            [sys.executable, str(CLI), "identity", "init"],
            cwd=str(self.worktree),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["command"], "identity")
        self.assertEqual(data["action"], "init")
        repo_id = data["repository_id"]
        self.assertTrue(is_valid_uuid_v4(repo_id))

        # 3. show after init -> success exit 0
        res = subprocess.run(
            [sys.executable, str(CLI), "identity", "show"],
            cwd=str(self.worktree),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["command"], "identity")
        self.assertEqual(data["action"], "show")
        self.assertEqual(data["repository_id"], repo_id)


if __name__ == "__main__":
    unittest.main()
