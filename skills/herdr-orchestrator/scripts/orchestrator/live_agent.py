from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.env import HerdrEnvError, require_herdr_env
from orchestrator.herdr_cli import HerdrCliError, run_herdr
from orchestrator.identity import (
    RepositoryIdentityError,
    get_repository_identity,
    is_valid_uuid_v4,
    resolve_git_common_dir,
)
from orchestrator.worktree import WorktreeError, resolve_worktree_root

PROMPTABLE_AGENT_STATES = frozenset({"idle", "done"})

AGENT_LABELS: dict[str, str] = {
    "agy": "Antigravity",
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "gemini": "Gemini CLI",
    "opencode": "OpenCode",
}


class LiveAgentError(Exception):
    pass


@dataclass(frozen=True)
class LiveAgentSnapshot:
    name: str
    agent_status: str
    foreground_cwd: Path
    pane_id: str | None
    raw: dict[str, Any]
    agent: str | None = None
    terminal_id: str | None = None
    agent_session_id: str | None = None
    workspace_id: str | None = None
    workspace_label: str | None = None
    workspace_number: int | None = None
    tab_id: str | None = None
    tab_number: int | None = None


def agent_human_label(agent_code: str | None) -> str:
    if not agent_code:
        return "未起動"
    return AGENT_LABELS.get(agent_code, agent_code)


def _extract_session_id(raw_obj: dict[str, Any]) -> str | None:
    sess = raw_obj.get("agent_session")
    if isinstance(sess, dict):
        val = sess.get("value")
        if isinstance(val, str) and val.strip():
            return val.strip()
    elif isinstance(sess, str) and sess.strip():
        return sess.strip()
    sid = raw_obj.get("agent_session_id")
    if isinstance(sid, str) and sid.strip():
        return sid.strip()
    return None


def _extract_optional_str(raw_obj: dict[str, Any], key: str) -> str | None:
    val = raw_obj.get(key)
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _require_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveAgentError(f"herdr payload invalid: {label} is not an object")
    return value


def _parse_foreground_cwd(agent: dict[str, Any]) -> Path:
    fg = agent.get("foreground_cwd")
    if fg is None or (isinstance(fg, str) and not fg.strip()):
        raise LiveAgentError("live agent foreground_cwd unavailable")
    if not isinstance(fg, str):
        raise LiveAgentError("live agent foreground_cwd has invalid type")
    path = Path(fg)
    if not path.is_absolute():
        raise LiveAgentError("live agent foreground_cwd must be an absolute path")
    return path


def _agent_from_herdr_payload(payload: dict[str, Any]) -> LiveAgentSnapshot:
    root = _require_dict(payload, label="root")
    if "error" in root:
        err = root["error"]
        code = err.get("code", "herdr_error") if isinstance(err, dict) else "herdr_error"
        raise LiveAgentError(f"herdr agent get failed: {code}")
    result = _require_dict(root.get("result"), label="result")
    agent = _require_dict(result.get("agent"), label="agent")
    pane_id = agent.get("pane_id")
    if pane_id is not None and not isinstance(pane_id, str):
        raise LiveAgentError("live agent pane_id has invalid type")
    raw_name = agent.get("name")
    if isinstance(raw_name, str) and raw_name.strip():
        name = raw_name
    elif isinstance(pane_id, str) and pane_id.strip():
        name = pane_id.strip()
    else:
        raise LiveAgentError("live agent has no name")
    fg_path = _parse_foreground_cwd(agent)
    status = agent.get("agent_status")
    if not isinstance(status, str) or not status:
        status = "unknown"
    return LiveAgentSnapshot(
        name=name,
        agent_status=status,
        foreground_cwd=fg_path,
        pane_id=pane_id,
        raw=agent,
        agent=_extract_optional_str(agent, "agent"),
        terminal_id=_extract_optional_str(agent, "terminal_id"),
        agent_session_id=_extract_session_id(agent),
        workspace_id=_extract_optional_str(agent, "workspace_id"),
        tab_id=_extract_optional_str(agent, "tab_id"),
    )


def get_caller_live_agent() -> LiveAgentSnapshot:
    """Resolve the Agent in the caller pane (HERDR_PANE_ID → agent get)."""
    pane_id = (os.environ.get("HERDR_PANE_ID") or "").strip()
    if not pane_id:
        raise LiveAgentError("caller HERDR_PANE_ID unavailable")
    return get_live_agent(pane_id)


def get_live_agent(agent_name: str) -> LiveAgentSnapshot:
    """Resolve live Agent by unique name or pane id hosting an agent."""
    try:
        require_herdr_env()
    except HerdrEnvError as exc:
        raise LiveAgentError(exc.message) from exc
    try:
        payload = run_herdr(["agent", "get", agent_name])
    except HerdrCliError as exc:
        raise LiveAgentError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise LiveAgentError("herdr agent get returned non-object payload")
    return _agent_from_herdr_payload(payload)


def assert_promptable_state(snapshot: LiveAgentSnapshot) -> None:
    if snapshot.agent_status not in PROMPTABLE_AGENT_STATES:
        raise LiveAgentError(
            f"agent {snapshot.name!r} state {snapshot.agent_status!r} not promptable"
        )


def verify_worktree_affinity(
    foreground_cwd: Path,
    *,
    expected_worktree_path: Path,
    expected_repository_identity: str,
) -> None:
    """
    §11.0 steps 1–5 (library): resolve git root + repository identity, compare to expected.
    """
    try:
        resolved = resolve_worktree_root(foreground_cwd)
    except WorktreeError as exc:
        raise LiveAgentError("worktree root resolution failed") from exc

    expected_root = expected_worktree_path.resolve()
    if resolved != expected_root:
        raise LiveAgentError(
            f"worktree mismatch: resolved={resolved!s} expected={expected_root!s}"
        )

    try:
        derived = get_repository_identity(resolved)
    except RepositoryIdentityError as exc:
        raise LiveAgentError("repository identity derivation failed") from exc

    if derived != expected_repository_identity:
        raise LiveAgentError(
            f"repository identity mismatch: derived={derived!r} expected={expected_repository_identity!r}"
        )


def verify_live_agent_context(
    agent_name: str,
    *,
    expected_worktree_path: Path,
    expected_repository_identity: str,
    require_promptable: bool = True,
) -> LiveAgentSnapshot:
    snap = get_live_agent(agent_name)
    if snap.name != agent_name and snap.pane_id != agent_name:
        raise LiveAgentError("agent.name mismatch after get")
    if require_promptable:
        assert_promptable_state(snap)
    verify_worktree_affinity(
        snap.foreground_cwd,
        expected_worktree_path=expected_worktree_path,
        expected_repository_identity=expected_repository_identity,
    )
    return snap


def is_path_within_worktree(candidate_cwd: Path, worktree_path: Path) -> bool:
    try:
        return candidate_cwd.resolve().is_relative_to(worktree_path.resolve())
    except (OSError, ValueError):
        return False


def check_cwd_affinity(pane_cwd: Path | str, current_cwd: Path | str) -> bool:
    """
    Check if pane_cwd and current_cwd have directory affinity:
    - Same directory (resolved)
    - One is relative to the other (subdirectory)
    - Same git common dir or git worktree root
    """
    try:
        p_cwd = Path(pane_cwd).resolve()
        c_cwd = Path(current_cwd).resolve()
    except (OSError, ValueError):
        return False

    if p_cwd == c_cwd:
        return True

    try:
        if c_cwd.is_relative_to(p_cwd):
            return True
    except (OSError, ValueError):
        pass

    try:
        p_common = resolve_git_common_dir(p_cwd)
        c_common = resolve_git_common_dir(c_cwd)
        if p_common.resolve() == c_common.resolve():
            return True
    except Exception:
        pass

    return False


def resolve_caller_live_agent_for_task(
    doc: dict[str, Any],
    *,
    preferred_role: str = "worker",
    proc_cwd: Path | None = None,
) -> tuple[LiveAgentSnapshot, dict[str, Any] | None]:
    """
    Resolve caller LiveAgentSnapshot with fallback recovery when HERDR_PANE_ID is contaminated
    (e.g., shared Codex daemon retaining another pane) or missing (e.g., Cursor subshell).
    Returns (snapshot, recovery_info_or_none).
    """
    actual_cwd = (proc_cwd or Path.cwd()).resolve()
    env_pane_id = (os.environ.get("HERDR_PANE_ID") or "").strip()

    initial_snap: LiveAgentSnapshot | None = None
    cwd_affinity_ok = False

    if env_pane_id:
        try:
            initial_snap = get_live_agent(env_pane_id)
            cwd_affinity_ok = check_cwd_affinity(initial_snap.foreground_cwd, actual_cwd)
        except LiveAgentError:
            initial_snap = None

    # Fast path: environment variable is intact and cwd has affinity
    if initial_snap is not None and cwd_affinity_ok:
        return initial_snap, None

    # Fallback recovery: environment variable is missing or mismatched (contaminated)
    worktree_path = Path(doc["context"]["worktree"]["path"]).resolve()
    worker_info = doc.get("worker", {})
    requester_info = doc.get("requester", {})

    worker_pane = worker_info.get("pane_id") or worker_info.get("agent_name")
    worker_cwd = Path(worker_info.get("cwd") or worktree_path).resolve()

    requester_pane = requester_info.get("pane_id") or requester_info.get("agent_name")
    requester_cwd = worktree_path

    def _try_recover_worker() -> tuple[LiveAgentSnapshot, dict[str, Any]] | None:
        if worker_pane and check_cwd_affinity(worker_cwd, actual_cwd):
            try:
                w_snap = get_live_agent(worker_pane)
                if check_cwd_affinity(w_snap.foreground_cwd, actual_cwd) and w_snap.agent_status != "none":
                    reason = "worker_cwd_affinity_contaminated" if env_pane_id else "worker_cwd_affinity_missing_env"
                    recovery = {
                        "recovered": True,
                        "reason": reason,
                        "env_pane_id": env_pane_id or None,
                        "resolved_pane_id": worker_pane,
                        "message": (
                            f"環境変数 HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリと矛盾していたため、"
                            f"タスク Worker コンテキスト ({worker_pane}) から呼び出し元を自動解決しました。"
                        ),
                    }
                    print(
                        f"警告: HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリ ({actual_cwd}) と一致しないため、"
                        f"タスク Worker コンテキスト ({worker_pane}) から呼び出し元を自動解決しました。",
                        file=sys.stderr,
                    )
                    return w_snap, recovery
            except LiveAgentError:
                pass
        return None

    def _try_recover_requester() -> tuple[LiveAgentSnapshot, dict[str, Any]] | None:
        if requester_pane and check_cwd_affinity(requester_cwd, actual_cwd):
            try:
                r_snap = get_live_agent(requester_pane)
                if check_cwd_affinity(r_snap.foreground_cwd, actual_cwd) and r_snap.agent_status != "none":
                    reason = "requester_cwd_affinity_contaminated" if env_pane_id else "requester_cwd_affinity_missing_env"
                    recovery = {
                        "recovered": True,
                        "reason": reason,
                        "env_pane_id": env_pane_id or None,
                        "resolved_pane_id": requester_pane,
                        "message": (
                            f"環境変数 HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリと矛盾していたため、"
                            f"タスク Requester コンテキスト ({requester_pane}) から呼び出し元を自動解決しました。"
                        ),
                    }
                    print(
                        f"警告: HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリ ({actual_cwd}) と一致しないため、"
                        f"タスク Requester コンテキスト ({requester_pane}) から呼び出し元を自動解決しました。",
                        file=sys.stderr,
                    )
                    return r_snap, recovery
            except LiveAgentError:
                pass
        return None

    order = [_try_recover_worker, _try_recover_requester]
    if preferred_role == "requester":
        order = [_try_recover_requester, _try_recover_worker]

    for try_fn in order:
        res = try_fn()
        if res is not None:
            return res

    # If recovery failed but we had an initial snapshot (even if mismatched), return it for normal fail-closed rejection
    if initial_snap is not None:
        return initial_snap, None

    # Neither env nor recovery succeeded
    raise LiveAgentError("caller HERDR_PANE_ID unavailable")


def resolve_caller_live_agent_for_worker(
    *,
    expected_pane: str,
    expected_cwd: Path,
    proc_cwd: Path | None = None,
) -> tuple[LiveAgentSnapshot, dict[str, Any] | None]:
    """
    Resolve caller LiveAgentSnapshot for Worker result-submit with fallback recovery.
    """
    actual_cwd = (proc_cwd or Path.cwd()).resolve()
    env_pane_id = (os.environ.get("HERDR_PANE_ID") or "").strip()

    initial_snap: LiveAgentSnapshot | None = None
    cwd_affinity_ok = False

    if env_pane_id:
        try:
            initial_snap = get_live_agent(env_pane_id)
            cwd_affinity_ok = check_cwd_affinity(initial_snap.foreground_cwd, actual_cwd)
        except LiveAgentError:
            initial_snap = None

    if initial_snap is not None and cwd_affinity_ok:
        return initial_snap, None

    if expected_pane and check_cwd_affinity(expected_cwd, actual_cwd):
        try:
            w_snap = get_live_agent(expected_pane)
            if check_cwd_affinity(w_snap.foreground_cwd, actual_cwd) and w_snap.agent_status != "none":
                reason = "worker_cwd_affinity_contaminated" if env_pane_id else "worker_cwd_affinity_missing_env"
                recovery = {
                    "recovered": True,
                    "reason": reason,
                    "env_pane_id": env_pane_id or None,
                    "resolved_pane_id": expected_pane,
                    "message": (
                        f"環境変数 HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリと矛盾していたため、"
                        f"Worker コンテキスト ({expected_pane}) から呼び出し元を自動解決しました。"
                    ),
                }
                print(
                    f"警告: HERDR_PANE_ID ({env_pane_id or '未設定'}) が作業ディレクトリ ({actual_cwd}) と一致しないため、"
                    f"Worker コンテキスト ({expected_pane}) から呼び出し元を自動解決しました。",
                    file=sys.stderr,
                )
                return w_snap, recovery
        except LiveAgentError:
            pass

    if initial_snap is not None:
        return initial_snap, None

    raise LiveAgentError("caller HERDR_PANE_ID unavailable")


def verify_live_party_cwd(
    agent_name: str,
    *,
    expected_cwd: Path,
    worktree_path: Path,
    repository_identity: str,
    require_promptable: bool = True,
) -> LiveAgentSnapshot:
    snap = get_live_agent(agent_name)
    if snap.name != agent_name and snap.pane_id != agent_name:
        raise LiveAgentError("agent.name mismatch after get")
    if require_promptable:
        assert_promptable_state(snap)
    if is_valid_uuid_v4(repository_identity) and is_path_within_worktree(expected_cwd, worktree_path):
        verify_worktree_affinity(
            snap.foreground_cwd,
            expected_worktree_path=worktree_path,
            expected_repository_identity=repository_identity,
        )
    else:
        if snap.foreground_cwd.resolve() != expected_cwd.resolve():
            raise LiveAgentError(
                f"cwd mismatch: resolved={snap.foreground_cwd.resolve()!s} expected={expected_cwd.resolve()!s}"
            )
    return snap


def list_live_panes_with_ui() -> list[dict[str, Any]] | None:
    """
    Query Herdr pane list + workspace list + tab list.
    Returns None if `herdr pane list` is unsupported (e.g., minimal fake herdr in legacy tests).
    """
    if os.environ.get("HERDR_ENV") != "1":
        return None
    try:
        pane_payload = run_herdr(["pane", "list"])
    except HerdrCliError:
        return None
    if not isinstance(pane_payload, dict):
        return None
    res = pane_payload.get("result")
    if not isinstance(res, dict) or not isinstance(res.get("panes"), list):
        return None

    ws_map: dict[str, dict[str, Any]] = {}
    try:
        ws_payload = run_herdr(["workspace", "list"])
        if isinstance(ws_payload, dict) and isinstance(ws_payload.get("result"), dict):
            for ws in ws_payload["result"].get("workspaces") or []:
                if isinstance(ws, dict) and isinstance(ws.get("workspace_id"), str):
                    ws_map[ws["workspace_id"]] = ws
    except HerdrCliError:
        pass

    tab_map: dict[str, dict[str, Any]] = {}
    try:
        tab_payload = run_herdr(["tab", "list"])
        if isinstance(tab_payload, dict) and isinstance(tab_payload.get("result"), dict):
            for tb in tab_payload["result"].get("tabs") or []:
                if isinstance(tb, dict) and isinstance(tb.get("tab_id"), str):
                    tab_map[tb["tab_id"]] = tb
    except HerdrCliError:
        pass

    panes: list[dict[str, Any]] = []
    for raw_p in res["panes"]:
        if not isinstance(raw_p, dict):
            continue
        pid = _extract_optional_str(raw_p, "pane_id")
        if not pid:
            continue
        fg_raw = _extract_optional_str(raw_p, "foreground_cwd") or _extract_optional_str(raw_p, "cwd")
        cwd_resolved = str(Path(fg_raw).resolve()) if fg_raw and Path(fg_raw).is_absolute() else fg_raw
        ws_id = _extract_optional_str(raw_p, "workspace_id")
        tb_id = _extract_optional_str(raw_p, "tab_id")
        ws_info = ws_map.get(ws_id or "", {})
        tb_info = tab_map.get(tb_id or "", {})
        agent_code = _extract_optional_str(raw_p, "agent")
        agent_status = _extract_optional_str(raw_p, "agent_status") or ("none" if not agent_code else "unknown")
        panes.append(
            {
                "pane_id": pid,
                "name": _extract_optional_str(raw_p, "name") or pid,
                "cwd": cwd_resolved,
                "agent": agent_code,
                "agent_label": agent_human_label(agent_code),
                "agent_status": agent_status,
                "terminal_id": _extract_optional_str(raw_p, "terminal_id"),
                "agent_session_id": _extract_session_id(raw_p),
                "workspace_id": ws_id,
                "workspace_label": _extract_optional_str(ws_info, "label") or _extract_optional_str(raw_p, "workspace_label"),
                "workspace_number": ws_info.get("number") if isinstance(ws_info.get("number"), int) else raw_p.get("workspace_number"),
                "tab_id": tb_id,
                "tab_number": tb_info.get("number") if isinstance(tb_info.get("number"), int) else raw_p.get("tab_number"),
                "raw": raw_p,
            }
        )
    return panes


def format_pane_display_summary(
    meta: dict[str, Any],
    *,
    fallback_role: str | None = None,
) -> str:
    """
    Build workspace-centric human-readable summary for a bound role target (§6.1):
    e.g. 'ワークスペース #2「log-workspace」の『ログ調査』（Antigravity / w16:p1）'
    """
    pane_id = meta.get("pane_id") or "?"
    display_name = meta.get("display_name") or fallback_role or pane_id
    agent_code = meta.get("agent")
    agent_lbl = agent_human_label(agent_code) if agent_code else "未起動"
    ws_num = meta.get("workspace_number")
    ws_lbl = meta.get("workspace_label")
    ws_prefix = ""
    if isinstance(ws_num, int) and ws_lbl:
        ws_prefix = f"ワークスペース #{ws_num}「{ws_lbl}」の"
    elif ws_lbl:
        ws_prefix = f"ワークスペース「{ws_lbl}」の"
    elif isinstance(ws_num, int):
        ws_prefix = f"ワークスペース #{ws_num} の"
    return f"{ws_prefix}『{display_name}』（{agent_lbl} / {pane_id}）"


def format_pane_candidate_summary(pane: dict[str, Any]) -> str:
    """
    Build workspace-centric candidate description (§6.1):
    e.g. 'ワークスペース #2「log-workspace」 / タブ #1 / Antigravity（/home/kadena/logs-investigation, w16:p1）'
    """
    pane_id = pane.get("pane_id") or "?"
    ws_num = pane.get("workspace_number")
    ws_lbl = pane.get("workspace_label")
    tab_num = pane.get("tab_number")
    agent_lbl = pane.get("agent_label") or agent_human_label(pane.get("agent"))
    cwd = pane.get("cwd") or "?"
    parts: list[str] = []
    if isinstance(ws_num, int) and ws_lbl:
        parts.append(f"ワークスペース #{ws_num}「{ws_lbl}」")
    elif ws_lbl:
        parts.append(f"ワークスペース「{ws_lbl}」")
    elif isinstance(ws_num, int):
        parts.append(f"ワークスペース #{ws_num}")
    if isinstance(tab_num, int):
        parts.append(f"タブ #{tab_num}")
    parts.append(agent_lbl)
    prefix = " / ".join(parts)
    return f"{prefix}（{cwd}, {pane_id}）"


def sync_herdr_pane_label(
    pane_id: str,
    display_name: str | None = None,
    *,
    clear: bool = False,
) -> dict[str, Any]:
    """Best-effort Herdr UI pane rename (`herdr pane rename <pane_id> <display_name>` / `herdr pane rename <pane_id> --clear`).

    Returns a status dict:
      {"synced": bool, "action": "rename" | "clear" | "skipped" | "failed", "label": str | None, "error": str | None}
    """
    if os.environ.get("HERDR_ENV") != "1":
        return {"synced": False, "action": "skipped", "label": None, "error": "HERDR_ENV_not_set"}
    try:
        if clear:
            run_herdr(["pane", "rename", pane_id, "--clear"])
            return {"synced": True, "action": "clear", "label": None, "error": None}
        elif display_name:
            clean_name = display_name
            while clean_name.startswith("--"):
                clean_name = clean_name[2:].strip()
            if clean_name:
                run_herdr(["pane", "rename", pane_id, clean_name])
                return {"synced": True, "action": "rename", "label": clean_name, "error": None}
            return {"synced": False, "action": "skipped", "label": None, "error": "empty_label_after_strip"}
        return {"synced": False, "action": "skipped", "label": None, "error": "no_display_name"}
    except HerdrCliError as exc:
        return {"synced": False, "action": "failed", "label": display_name, "error": str(exc)}


def inspect_pane_for_role_set(pane_id: str, *, display_name: str, bound_at: str) -> dict[str, Any]:
    """
    Inspect live pane for `role set` in HERDR_ENV=1.
    Raises LiveAgentError("role_target_agent_not_running") if the pane is missing or has no running agent.
    """
    panes = list_live_panes_with_ui()
    if panes is not None:
        matched = [p for p in panes if p.get("pane_id") == pane_id]
        if not matched:
            raise LiveAgentError(
                f"指定されたペイン {pane_id!r} が見つかりません。エージェントが起動しているペインを指定してください。"
            )
        p = matched[0]
        agent_code = p.get("agent")
        status = p.get("agent_status")
        if not agent_code or status == "none" or not p.get("cwd"):
            raise LiveAgentError(
                f"ペイン {pane_id!r} でエージェントが起動していません（シェル状態）。エージェントを起動してから role set を実行してください。"
            )
        return {
            "pane_id": pane_id,
            "display_name": display_name,
            "cwd": str(Path(p["cwd"]).resolve()),
            "agent": agent_code,
            "terminal_id": p.get("terminal_id"),
            "agent_session_id": p.get("agent_session_id"),
            "workspace_id": p.get("workspace_id"),
            "workspace_label": p.get("workspace_label"),
            "workspace_number": p.get("workspace_number"),
            "tab_id": p.get("tab_id"),
            "tab_number": p.get("tab_number"),
            "bound_at": bound_at,
        }

    # Fallback when `herdr pane list` is not supported by minimal fake herdr in tests
    try:
        snap = get_live_agent(pane_id)
    except LiveAgentError as exc:
        raise LiveAgentError(
            f"ペイン {pane_id!r} で稼働中のエージェントを確認できませんでした: {exc}"
        ) from exc
    if not snap.agent or snap.agent_status == "none":
        raise LiveAgentError(
            f"ペイン {pane_id!r} でエージェントが起動していません（シェル状態）。"
        )
    return {
        "pane_id": pane_id,
        "display_name": display_name,
        "cwd": str(snap.foreground_cwd.resolve()),
        "agent": snap.agent,
        "terminal_id": snap.terminal_id,
        "agent_session_id": snap.agent_session_id,
        "workspace_id": snap.workspace_id,
        "workspace_label": snap.workspace_label,
        "workspace_number": snap.workspace_number,
        "tab_id": snap.tab_id,
        "tab_number": snap.tab_number,
        "bound_at": bound_at,
    }
