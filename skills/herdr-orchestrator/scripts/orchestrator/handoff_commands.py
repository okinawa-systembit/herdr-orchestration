from __future__ import annotations

import copy
import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from orchestrator.handoff_lock import HandoffLockSession
from orchestrator.herdr_cli import HerdrCliError, classify_prompt_error, run_herdr
from orchestrator.identity import (
    GLOBAL_SCOPE_ID,
    GitRepositoryNotInitializedError,
    RepositoryIdentityError,
    derive_directory_scope_id,
    get_or_create_repository_identity,
    resolve_git_common_dir,
)
from orchestrator.live_agent import (
    LiveAgentError,
    LiveAgentSnapshot,
    format_pane_candidate_summary,
    format_pane_display_summary,
    get_caller_live_agent,
    get_live_agent,
    list_live_panes_with_ui,
    sync_herdr_pane_label,
    verify_live_agent_context,
)
from orchestrator.naming import derive_requester_role, expected_agent_name
from orchestrator.role_bindings import (
    RoleBindingError,
    compare_and_update_role_binding,
    get_role_binding_document,
    is_target_meta_valid,
    resolve_pane_by_role,
    resolve_role_by_pane,
    validate_role_name,
)
from orchestrator.registry.errors import RegistryError
from orchestrator.registry import operations as registry_ops
from orchestrator.registry.schema import MODES, PATTERN_A_ROLES, STANDARD_ROLES, UNBOUND_ROLE
from orchestrator.worktree import WorktreeError, resolve_worktree_root

DEFAULT_RESULT_WAIT_SEC = 30 * 60
RESULT_POLL_INTERVAL_SEC = float(
    os.environ.get("HERDR_ORCHESTRATOR_RESULT_POLL_SEC", "2.0")
)
TERMINAL_RESULTS = frozenset({"succeeded", "failed", "unavailable"})


class HandoffCommandError(Exception):
    def __init__(
        self,
        code: str,
        *,
        message: str | None = None,
        retryable: bool = False,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.retryable = retryable
        self.task_id = task_id
        self.details = details or {}
        super().__init__(self.message)


def _registry_to_handoff(exc: RegistryError) -> HandoffCommandError:
    return HandoffCommandError(
        exc.code,
        message=exc.message,
        retryable=exc.retryable,
        task_id=exc.task_id,
    )


def _git_branch(worktree: Path) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch or None


def build_task_envelope(task_id: str) -> str:
    return (
        f"{task_id}\n"
        "herdr-orchestrator task-status / task-result を実行し Registry を読んでから作業すること"
    )


def _dispatch_outcome_from_prompt_error(exc: HerdrCliError) -> tuple[str, str]:
    return classify_prompt_error(exc)


def _require_live_pane_id(snap: Any, *, party: str) -> str:
    pane_id = snap.pane_id
    if not isinstance(pane_id, str) or not pane_id.strip():
        raise HandoffCommandError(
            "live_pane_id_missing",
            message=f"{party} live agent has no pane_id",
        )
    return pane_id


@dataclass(frozen=True)
class HandoffInput:
    mode: str
    worker_role: str
    task_type: str
    instruction: str
    result_wait_timeout_sec: int
    scope: str | None = None


def _non_empty_str_match(a: str | None, b: str | None) -> bool:
    """RV020 N-1: null or empty strings never match each other."""
    return bool(
        isinstance(a, str)
        and isinstance(b, str)
        and a.strip()
        and b.strip()
        and a.strip() == b.strip()
    )


def _snapshot_from_pane_dict(p: dict[str, Any]) -> LiveAgentSnapshot:
    pane_id = p["pane_id"]
    return LiveAgentSnapshot(
        name=pane_id,
        agent_status=p.get("agent_status") or "idle",
        foreground_cwd=Path(p["cwd"]).resolve(),
        pane_id=pane_id,
        raw=p.get("raw") or p,
        agent=p.get("agent"),
        terminal_id=p.get("terminal_id"),
        agent_session_id=p.get("agent_session_id"),
        workspace_id=p.get("workspace_id"),
        workspace_label=p.get("workspace_label"),
        workspace_number=p.get("workspace_number"),
        tab_id=p.get("tab_id"),
        tab_number=p.get("tab_number"),
    )


def _verify_or_track_bound_worker(
    *,
    chosen_scope_id: str,
    canonical_worker_role: str,
    bound_pane_id: str,
    target_meta: dict[str, Any],
    req_pane_opt: str | None,
    local_scope_id: str,
    local_doc: dict[str, Any],
    global_doc: dict[str, Any],
) -> LiveAgentSnapshot:
    expected_cwd = Path(target_meta["cwd"]).resolve()
    expected_agent = target_meta["agent"]
    expected_terminal_id = target_meta.get("terminal_id")
    expected_session_id = target_meta.get("agent_session_id")
    target_summary = format_pane_display_summary(
        target_meta, fallback_role=canonical_worker_role
    )

    same_cwd_other_roles = 0
    other_expected_sessions: set[str] = set()
    other_bound_panes: set[str] = set()

    for s_id, s_doc in ((local_scope_id, local_doc), (GLOBAL_SCOPE_ID, global_doc)):
        for r_name, p_id in s_doc.get("roles", {}).items():
            if s_id == chosen_scope_id and r_name == canonical_worker_role:
                continue
            if p_id == bound_pane_id:
                # 同一ペインに登録された他スコープの兼任ロールは別ペインからの侵入ではないため除外
                continue
            other_bound_panes.add(p_id)
            m = s_doc.get("targets", {}).get(r_name)
            if isinstance(m, dict):
                cwd_val = m.get("cwd")
                if isinstance(cwd_val, str) and cwd_val.strip():
                    try:
                        if Path(cwd_val).resolve() == expected_cwd:
                            same_cwd_other_roles += 1
                    except OSError:
                        pass
                sess_val = m.get("agent_session_id")
                if isinstance(sess_val, str) and sess_val.strip():
                    other_expected_sessions.add(sess_val.strip())

    is_unique_cwd_role = same_cwd_other_roles == 0
    panes = list_live_panes_with_ui()

    if panes is None:
        # Fallback for minimal fake herdr test harnesses that only implement `herdr agent get`
        if bound_pane_id == req_pane_opt:
            raise HandoffCommandError(
                "worker_context_changed",
                message=f"{target_summary} が依頼元自身のペインと重なっています。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        try:
            snap = get_live_agent(bound_pane_id)
        except LiveAgentError as exc:
            raise HandoffCommandError(
                "worker_pane_closed_or_missing",
                message=f"{target_summary} に配送できませんでした（閉じられているか応答しません）: {exc}",
                details={"target": {**target_meta, "display_summary": target_summary}},
            ) from exc
        if snap.agent_status == "none" or not snap.agent:
            raise HandoffCommandError(
                "worker_agent_not_running",
                message=f"{target_summary} に配送できませんでした（現在エージェントが起動しておらずシェル状態です）。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        if snap.agent_status not in ("idle", "done"):
            raise HandoffCommandError(
                "worker_not_promptable",
                message=f"{target_summary} が応答可能な状態ではありません（status={snap.agent_status}）。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        live_cwd = snap.foreground_cwd.resolve()
        live_agent = snap.agent
        live_term = snap.terminal_id
        live_sess = snap.agent_session_id

        cwd_ok = live_cwd == expected_cwd
        agent_ok = live_agent == expected_agent
        sess_not_other = not (live_sess and live_sess in other_expected_sessions)
        identity_ok = (
            _non_empty_str_match(live_term, expected_terminal_id)
            or _non_empty_str_match(live_sess, expected_session_id)
            or is_unique_cwd_role
        )
        if not (cwd_ok and agent_ok and sess_not_other and identity_ok):
            raise HandoffCommandError(
                "worker_context_changed",
                message=(
                    f"{target_summary} の状態が登録時と変わっています"
                    f"（登録時: {expected_cwd} / {expected_agent}、現在: {live_cwd} / {live_agent}）。"
                ),
                details={
                    "target": {**target_meta, "display_summary": target_summary},
                    "actual": {
                        "pane_id": bound_pane_id,
                        "cwd": str(live_cwd),
                        "agent": live_agent,
                        "terminal_id": live_term,
                        "agent_session_id": live_sess,
                    },
                },
            )
        updated_meta = {
            **target_meta,
            "pane_id": bound_pane_id,
            "terminal_id": live_term,
            "agent_session_id": live_sess,
        }
        ok = compare_and_update_role_binding(
            chosen_scope_id,
            canonical_worker_role,
            expected_previous_pane_id=bound_pane_id,
            new_pane_id=bound_pane_id,
            target_meta=updated_meta,
        )
        if not ok:
            raise HandoffCommandError(
                "worker_context_changed",
                message=f"{target_summary} のバインディングが並行操作により変更されました。再実行してください。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        return replace(snap, name=bound_pane_id)

    # Full `herdr pane list` path (§5.2)
    matched_live = [p for p in panes if p.get("pane_id") == bound_pane_id]
    if matched_live and bound_pane_id != req_pane_opt:
        live_p = matched_live[0]
        live_agent = live_p.get("agent")
        live_status = live_p.get("agent_status")
        if not live_agent or live_status == "none":
            raise HandoffCommandError(
                "worker_agent_not_running",
                message=(
                    f"{target_summary} に配送できませんでした"
                    "（現在そのペインでエージェントが起動しておらずシェル状態です）。"
                    "エージェントを起動してから再実行してください。"
                ),
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        if live_status not in ("idle", "done"):
            raise HandoffCommandError(
                "worker_not_promptable",
                message=f"{target_summary} が応答しません（status={live_status}）。ペインの状態を確認してください。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        live_cwd_raw = live_p.get("cwd")
        live_cwd = Path(live_cwd_raw).resolve() if live_cwd_raw else None
        live_term = live_p.get("terminal_id")
        live_sess = live_p.get("agent_session_id")

        cwd_ok = live_cwd is not None and live_cwd == expected_cwd
        agent_ok = live_agent == expected_agent
        sess_not_other = not (live_sess and live_sess in other_expected_sessions)
        identity_ok = (
            _non_empty_str_match(live_term, expected_terminal_id)
            or _non_empty_str_match(live_sess, expected_session_id)
            or is_unique_cwd_role
        )
        if cwd_ok and agent_ok and sess_not_other and identity_ok:
            updated_meta = {
                **target_meta,
                "pane_id": bound_pane_id,
                "cwd": str(live_cwd),
                "agent": live_agent,
                "terminal_id": live_term,
                "agent_session_id": live_sess,
                "workspace_id": live_p.get("workspace_id") or target_meta.get("workspace_id"),
                "workspace_label": live_p.get("workspace_label") or target_meta.get("workspace_label"),
                "workspace_number": live_p.get("workspace_number") if live_p.get("workspace_number") is not None else target_meta.get("workspace_number"),
                "tab_id": live_p.get("tab_id") or target_meta.get("tab_id"),
                "tab_number": live_p.get("tab_number") if live_p.get("tab_number") is not None else target_meta.get("tab_number"),
            }
            ok = compare_and_update_role_binding(
                chosen_scope_id,
                canonical_worker_role,
                expected_previous_pane_id=bound_pane_id,
                new_pane_id=bound_pane_id,
                target_meta=updated_meta,
            )
            if not ok:
                raise HandoffCommandError(
                    "worker_context_changed",
                    message=f"{target_summary} のバインディングが並行操作により変更されました。再実行してください。",
                    details={"target": {**target_meta, "display_summary": target_summary}},
                )
            return _snapshot_from_pane_dict(live_p)

        raise HandoffCommandError(
            "worker_context_changed",
            message=(
                f"{target_summary} を確認したところ、登録時と状態が変わっています"
                f"（登録時: {expected_cwd} / {expected_agent}、現在: {live_cwd} / {live_agent}）。"
                "このペインに配送してよいか人間に確認してください。"
            ),
            details={
                "target": {**target_meta, "display_summary": target_summary},
                "actual": {
                    "pane_id": bound_pane_id,
                    "cwd": str(live_cwd) if live_cwd else None,
                    "agent": live_agent,
                    "terminal_id": live_term,
                    "agent_session_id": live_sess,
                },
            },
        )

    # Case I -> J / J2 -> K / L: bound_pane_id is closed or collided with requester pane
    live_pane_ids = {p["pane_id"] for p in panes}
    active_other_bound_panes = other_bound_panes & live_pane_ids
    candidates = [
        p
        for p in panes
        if p.get("pane_id") != req_pane_opt
        and p.get("pane_id") not in active_other_bound_panes
        and p.get("agent")
        and p.get("agent_status") in ("idle", "done")
        and p.get("cwd")
    ]

    chosen_p: dict[str, Any] | None = None

    # Condition A: exact agent_session_id match
    sess_matches = [
        p for p in candidates if _non_empty_str_match(p.get("agent_session_id"), expected_session_id)
    ]
    if len(sess_matches) == 1:
        chosen_p = sess_matches[0]
    elif len(sess_matches) > 1:
        cand_list = [
            {k: v for k, v in p.items() if k != "raw"} | {"display_candidate": format_pane_candidate_summary(p)}
            for p in sess_matches
        ]
        raise HandoffCommandError(
            "worker_ambiguous_candidates",
            message=(
                f"全再起動後に『{target_meta.get('display_name') or canonical_worker_role}』の候補となるペインが複数見つかりました。"
                "どちらのワークスペースのペインに配送しますか？"
            ),
            details={
                "target": {**target_meta, "display_summary": target_summary},
                "candidates": cand_list,
            },
        )

    # Condition B: 1-folder 1-role config (`is_unique_cwd_role`) + single `cwd` & `agent` match
    if chosen_p is None and is_unique_cwd_role:
        cwd_agent_matches = [
            p
            for p in candidates
            if Path(p["cwd"]).resolve() == expected_cwd
            and p.get("agent") == expected_agent
            and not (p.get("agent_session_id") and p.get("agent_session_id") in other_expected_sessions)
        ]
        if len(cwd_agent_matches) == 1:
            chosen_p = cwd_agent_matches[0]
        elif len(cwd_agent_matches) > 1:
            cand_list = [
                {k: v for k, v in p.items() if k != "raw"} | {"display_candidate": format_pane_candidate_summary(p)}
                for p in cwd_agent_matches
            ]
            raise HandoffCommandError(
                "worker_ambiguous_candidates",
                message=(
                    f"全再起動後に『{target_meta.get('display_name') or canonical_worker_role}』の候補となるペインが複数見つかりました。"
                    "どちらのワークスペースのペインに配送しますか？"
                ),
                details={
                    "target": {**target_meta, "display_summary": target_summary},
                    "candidates": cand_list,
                },
            )

    if chosen_p is not None:
        new_pane_id = chosen_p["pane_id"]
        updated_meta = {
            **target_meta,
            "pane_id": new_pane_id,
            "cwd": str(Path(chosen_p["cwd"]).resolve()),
            "agent": chosen_p["agent"],
            "terminal_id": chosen_p.get("terminal_id"),
            "agent_session_id": chosen_p.get("agent_session_id"),
            "workspace_id": chosen_p.get("workspace_id"),
            "workspace_label": chosen_p.get("workspace_label"),
            "workspace_number": chosen_p.get("workspace_number"),
            "tab_id": chosen_p.get("tab_id"),
            "tab_number": chosen_p.get("tab_number"),
        }
        ok = compare_and_update_role_binding(
            chosen_scope_id,
            canonical_worker_role,
            expected_previous_pane_id=bound_pane_id,
            new_pane_id=new_pane_id,
            target_meta=updated_meta,
        )
        if not ok:
            raise HandoffCommandError(
                "worker_context_changed",
                message=f"{target_summary} のバインディングが並行操作により変更されました。再実行してください。",
                details={"target": {**target_meta, "display_summary": target_summary}},
            )
        sync_herdr_pane_label(new_pane_id, updated_meta.get("display_name"))
        return _snapshot_from_pane_dict(chosen_p)

    # Case L: Stop and present candidates to human
    cand_list = [
        {k: v for k, v in p.items() if k != "raw"} | {"display_candidate": format_pane_candidate_summary(p)}
        for p in candidates
    ]
    same_dir_cands = [
        c
        for c in cand_list
        if Path(c["cwd"]).resolve() == expected_cwd and c.get("agent") == expected_agent
    ]
    if len(same_dir_cands) > 1:
        raise HandoffCommandError(
            "worker_ambiguous_candidates",
            message=(
                f"『{target_meta.get('display_name') or canonical_worker_role}』の候補となるペインが複数見つかりました。"
                "どちらのワークスペースのペインに配送しますか？"
            ),
            details={
                "target": {**target_meta, "display_summary": target_summary},
                "candidates": same_dir_cands,
            },
        )
    raise HandoffCommandError(
        "worker_pane_closed_or_missing",
        message=(
            f"以前登録されていた {target_summary} は既に閉じられているか特定できないため配送できませんでした。"
        ),
        details={
            "target": {**target_meta, "display_summary": target_summary},
            "candidates": cand_list,
        },
    )


def _resolve_parties(
    *,
    worker_role: str,
    scope: str | None = None,
) -> tuple[Any, Any, Path, str, str, str, list[dict[str, Any]]]:
    if scope is not None and scope not in ("local", "global"):
        raise HandoffCommandError("invalid_input", message="scope must be local or global")
    try:
        canonical_worker_role = validate_role_name(worker_role)
    except RoleBindingError as exc:
        raise HandoffCommandError("invalid_input", message=exc.message) from exc

    try:
        requester = get_caller_live_agent()
    except LiveAgentError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc

    is_git = True
    try:
        worktree = resolve_worktree_root(requester.foreground_cwd)
        identity = get_or_create_repository_identity(worktree)
    except WorktreeError as exc:
        try:
            resolve_git_common_dir(requester.foreground_cwd)
        except GitRepositoryNotInitializedError:
            is_git = False
            worktree = requester.foreground_cwd.resolve()
            identity = derive_directory_scope_id(worktree)
        except RepositoryIdentityError as repo_exc:
            raise HandoffCommandError("requester_context_unverified", message=str(repo_exc)) from repo_exc
        else:
            raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
    except GitRepositoryNotInitializedError:
        is_git = False
        worktree = requester.foreground_cwd.resolve()
        identity = derive_directory_scope_id(worktree)
    except RepositoryIdentityError as exc:
        raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc

    try:
        local_doc = get_role_binding_document(identity)
        global_doc = get_role_binding_document(GLOBAL_SCOPE_ID)
    except RoleBindingError as exc:
        raise HandoffCommandError(exc.code, message=exc.message) from exc

    req_pane_opt = (
        requester.pane_id.strip()
        if isinstance(requester.pane_id, str) and requester.pane_id.strip()
        else None
    )

    local_req_matches = (
        [r for r, p in local_doc["roles"].items() if p == req_pane_opt]
        if req_pane_opt
        else []
    )
    global_req_matches = (
        [r for r, p in global_doc["roles"].items() if p == req_pane_opt]
        if req_pane_opt
        else []
    )

    in_local = canonical_worker_role in local_doc["roles"]
    in_global = canonical_worker_role in global_doc["roles"]

    if len(local_req_matches) == 1 and req_pane_opt is not None:
        requester_role = local_req_matches[0]
        if not is_target_meta_valid(local_doc["targets"].get(requester_role)):
            raise HandoffCommandError(
                "role_binding_outdated",
                message=(
                    f"依頼元ロール『{requester_role}』（{req_pane_opt}）の設定が旧バージョンの形式です。"
                    f"'herdr-orchestrator role set {requester_role} {req_pane_opt}' を実行して再登録してください。"
                ),
                details={"role": requester_role, "pane_id": req_pane_opt, "scope": "local"},
            )
        requester = replace(requester, name=req_pane_opt)
    elif len(global_req_matches) == 1 and req_pane_opt is not None:
        requester_role = global_req_matches[0]
        if not is_target_meta_valid(global_doc["targets"].get(requester_role)):
            raise HandoffCommandError(
                "role_binding_outdated",
                message=(
                    f"依頼元ロール『{requester_role}』（{req_pane_opt}）の設定が旧バージョンの形式です。"
                    f"'herdr-orchestrator role set {requester_role} {req_pane_opt} --global' を実行して再登録してください。"
                ),
                details={"role": requester_role, "pane_id": req_pane_opt, "scope": "global"},
            )
        requester = replace(requester, name=req_pane_opt)
    else:
        if is_git:
            try:
                derived_role = derive_requester_role(identity, requester.name)
            except ValueError as exc:
                if not in_local and not in_global:
                    raise HandoffCommandError("requester_context_unverified", message=str(exc)) from exc
                requester_role = UNBOUND_ROLE
                if req_pane_opt is not None:
                    requester = replace(requester, name=req_pane_opt)
            else:
                existing_req_pane = local_doc["roles"].get(derived_role)
                if existing_req_pane is not None and existing_req_pane != req_pane_opt:
                    raise HandoffCommandError(
                        "requester_context_unverified",
                        message=(
                            f"requester role {derived_role!r} is bound to pane {existing_req_pane!r}, "
                            f"not caller pane {req_pane_opt!r}"
                        ),
                    )
                requester_role = derived_role
        else:
            requester_role = UNBOUND_ROLE
            if req_pane_opt is not None:
                requester = replace(requester, name=req_pane_opt)

    warnings: list[dict[str, Any]] = []
    chosen_scope_id: str | None = None
    chosen_doc: dict[str, Any] | None = None

    available_roles_detail = {
        "available_roles": {
            "local": sorted(local_doc["roles"].keys()),
            "global": sorted(global_doc["roles"].keys()),
        }
    }

    if scope == "local":
        if not in_local:
            raise HandoffCommandError(
                "worker_role_not_bound",
                message=f"ロール『{canonical_worker_role}』はローカルスコープに登録されていません。",
                details=available_roles_detail,
            )
        chosen_scope_id, chosen_doc = identity, local_doc
        if in_global:
            warnings.append(
                {
                    "code": "worker_role_shadowed_by_explicit_scope",
                    "message": f"グローバルにも同名ロール『{canonical_worker_role}』が存在しますが、--scope local によりローカルを採用しました。",
                }
            )
    elif scope == "global":
        if not in_global:
            raise HandoffCommandError(
                "worker_role_not_bound",
                message=f"ロール『{canonical_worker_role}』はグローバルスコープに登録されていません。",
                details=available_roles_detail,
            )
        chosen_scope_id, chosen_doc = GLOBAL_SCOPE_ID, global_doc
        if in_local:
            warnings.append(
                {
                    "code": "worker_role_shadowed_by_explicit_scope",
                    "message": f"ローカルにも同名ロール『{canonical_worker_role}』が存在しますが、--scope global によりグローバルを採用しました。",
                }
            )
    else:
        if in_local and in_global:
            local_meta = local_doc["targets"].get(canonical_worker_role) or {
                "pane_id": local_doc["roles"][canonical_worker_role],
                "display_name": canonical_worker_role,
            }
            global_meta = global_doc["targets"].get(canonical_worker_role) or {
                "pane_id": global_doc["roles"][canonical_worker_role],
                "display_name": canonical_worker_role,
            }
            local_summary = format_pane_display_summary(local_meta, fallback_role=canonical_worker_role)
            global_summary = format_pane_display_summary(global_meta, fallback_role=canonical_worker_role)
            raise HandoffCommandError(
                "worker_role_scope_ambiguous",
                message=(
                    f"『{canonical_worker_role}』が 2 つのスコープに登録されています。どちらに依頼しますか？\n"
                    f"1. {local_summary}［このプロジェクト専用］（--scope local）\n"
                    f"2. {global_summary}［グローバル共通］（--scope global）"
                ),
                details={
                    "role": canonical_worker_role,
                    "candidates": [
                        {"scope": "local", **local_meta, "display_summary": local_summary},
                        {"scope": "global", **global_meta, "display_summary": global_summary},
                    ],
                },
            )
        elif in_local:
            chosen_scope_id, chosen_doc = identity, local_doc
        elif in_global:
            chosen_scope_id, chosen_doc = GLOBAL_SCOPE_ID, global_doc

    if chosen_scope_id is not None and chosen_doc is not None:
        bound_worker_pane = chosen_doc["roles"][canonical_worker_role]
        target_meta = chosen_doc["targets"].get(canonical_worker_role)
        if not is_target_meta_valid(target_meta):
            scope_flag = " --global" if chosen_scope_id == GLOBAL_SCOPE_ID else ""
            raise HandoffCommandError(
                "role_binding_outdated",
                message=(
                    f"ロール『{canonical_worker_role}』（{bound_worker_pane}）の設定が旧バージョンの形式です。"
                    f"'herdr-orchestrator role set {canonical_worker_role} {bound_worker_pane}{scope_flag}' を実行して再登録してください。"
                ),
                details={
                    "role": canonical_worker_role,
                    "pane_id": bound_worker_pane,
                    "scope": "global" if chosen_scope_id == GLOBAL_SCOPE_ID else "local",
                },
            )
        assert target_meta is not None
        worker = _verify_or_track_bound_worker(
            chosen_scope_id=chosen_scope_id,
            canonical_worker_role=canonical_worker_role,
            bound_pane_id=bound_worker_pane,
            target_meta=target_meta,
            req_pane_opt=req_pane_opt,
            local_scope_id=identity,
            local_doc=local_doc,
            global_doc=global_doc,
        )
        _require_live_pane_id(requester, party="requester")
        wrk_pane = _require_live_pane_id(worker, party="worker")
        worker = replace(worker, name=wrk_pane)
        return requester, worker, worktree, identity, requester_role, canonical_worker_role, warnings

    # Neither local nor global has this role bound
    if not is_git:
        if canonical_worker_role in PATTERN_A_ROLES:
            raise HandoffCommandError(
                "git_repository_not_initialized",
                message=f"{worktree} is not inside a git repository",
            )
        raise HandoffCommandError(
            "worker_role_not_bound",
            message=f"ロール『{canonical_worker_role}』は登録されていません。登録先ペインを確認し、'herdr-orchestrator role set {canonical_worker_role} <pane_id> [--global]' で登録してください。",
            details=available_roles_detail,
        )

    if canonical_worker_role not in PATTERN_A_ROLES:
        raise HandoffCommandError(
            "worker_role_not_bound",
            message=f"ロール『{canonical_worker_role}』は登録されていません。登録先ペインを確認し、'herdr-orchestrator role set {canonical_worker_role} <pane_id> [--global]' で登録してください。",
            details=available_roles_detail,
        )

    worker_target = expected_agent_name(identity, canonical_worker_role)
    try:
        worker = verify_live_agent_context(
            worker_target,
            expected_worktree_path=worktree,
            expected_repository_identity=identity,
            require_promptable=True,
        )
    except LiveAgentError as exc:
        raise HandoffCommandError(
            "worker_unavailable",
            message=str(exc),
            details=available_roles_detail,
        ) from exc
    _require_live_pane_id(requester, party="requester")
    _require_live_pane_id(worker, party="worker")
    return requester, worker, worktree, identity, requester_role, canonical_worker_role, warnings


def _build_create_payload(
    *,
    handoff: HandoffInput,
    worktree: Path,
    identity: str,
    requester_role: str,
    worker_role: str,
    requester_snap: Any,
    worker_snap: Any,
) -> dict[str, Any]:
    completion_action = "none" if handoff.mode == "sync" else "resume_requester"
    return {
        "task": {
            "type": handoff.task_type,
            "mode": handoff.mode,
            "instruction": handoff.instruction,
        },
        "context": {
            "repository": {"identity": identity},
            "worktree": {
                "path": str(worktree.resolve()),
                "branch": _git_branch(worktree),
                "commit": None,
            },
        },
        "requester": {
            "role": requester_role,
            "agent_name": requester_snap.name,
            "pane_id": _require_live_pane_id(requester_snap, party="requester"),
        },
        "worker": {
            "role": worker_role,
            "agent_name": worker_snap.name,
            "pane_id": _require_live_pane_id(worker_snap, party="worker"),
            "cwd": str(worker_snap.foreground_cwd.resolve()),
        },
        "completion": {"action": completion_action},
    }


def _observe_worker_during_result_wait(doc: dict[str, Any]) -> None:
    worker_name = doc["worker"]["agent_name"]
    dispatch_status = doc["dispatch"]["status"]
    try:
        snap = get_live_agent(worker_name)
    except LiveAgentError as exc:
        if dispatch_status == "sent":
            raise HandoffCommandError(
                "worker_unavailable",
                retryable=False,
                task_id=doc["task"]["id"],
                message=str(exc),
            ) from exc
        return
    if snap.agent_status == "blocked":
        raise HandoffCommandError(
            "worker_blocked",
            retryable=False,
            task_id=doc["task"]["id"],
            message="worker is blocked during result wait",
        )


def _wait_for_result_terminal(task_id: str, *, timeout_sec: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        doc = registry_ops.get_task(task_id)
        if doc["result"]["status"] in TERMINAL_RESULTS:
            return doc
        if doc["dispatch"]["status"] == "pending":
            raise HandoffCommandError(
                "dispatch_not_ready",
                retryable=True,
                task_id=task_id,
                message="mark-dispatch incomplete; cannot poll result",
            )
        _observe_worker_during_result_wait(doc)
        time.sleep(RESULT_POLL_INTERVAL_SEC)
    raise HandoffCommandError(
        "result_wait_timeout",
        retryable=True,
        task_id=task_id,
        message="result.status did not reach terminal before timeout",
    )


def run_handoff(handoff: HandoffInput) -> dict[str, Any]:
    if handoff.mode not in MODES:
        raise HandoffCommandError("invalid_input", message="mode must be sync or async")

    requester, worker, worktree, identity, requester_role, canonical_worker_role, warnings = (
        _resolve_parties(
            worker_role=handoff.worker_role,
            scope=handoff.scope,
        )
    )
    create_payload = _build_create_payload(
        handoff=handoff,
        worktree=worktree,
        identity=identity,
        requester_role=requester_role,
        worker_role=canonical_worker_role,
        requester_snap=requester,
        worker_snap=worker,
    )

    try:
        doc = registry_ops.create_task(create_payload)
    except RegistryError as exc:
        raise _registry_to_handoff(exc) from exc

    task_id = doc["task"]["id"]
    prompt_sent = False
    dispatch_outcome: str | None = None
    dispatch_reason: str | None = None

    try:
        with HandoffLockSession(task_id):
            try:
                doc = registry_ops.record_prompt_start(task_id)
            except RegistryError as exc:
                raise _registry_to_handoff(exc) from exc

            try:
                registry_ops.assert_handoff_continuable(task_id)
            except RegistryError as exc:
                raise _registry_to_handoff(exc) from exc

            try:
                run_herdr(["agent", "prompt", worker.name, build_task_envelope(task_id)])
                prompt_sent = True
                dispatch_outcome, dispatch_reason = "sent", None
            except HerdrCliError as exc:
                prompt_sent = False
                dispatch_outcome, dispatch_reason = _dispatch_outcome_from_prompt_error(exc)

            try:
                doc = registry_ops.mark_dispatch(
                    task_id,
                    outcome=dispatch_outcome,
                    reason=dispatch_reason,
                )
            except RegistryError as exc:
                raise _registry_to_handoff(exc) from exc

            result_wait: dict[str, Any] | None = None
            if handoff.mode == "sync" and dispatch_outcome == "sent":
                try:
                    result_wait = _wait_for_result_terminal(
                        task_id,
                        timeout_sec=handoff.result_wait_timeout_sec,
                    )
                    doc = result_wait
                except HandoffCommandError:
                    raise
                except RegistryError as exc:
                    raise _registry_to_handoff(exc) from exc
    except RegistryError as exc:
        raise _registry_to_handoff(exc) from exc

    out: dict[str, Any] = {
        "command": "handoff",
        "task_id": task_id,
        "mode": handoff.mode,
        "prompt_sent": prompt_sent,
        "task": copy.deepcopy(doc["task"]),
        "dispatch": copy.deepcopy(doc["dispatch"]),
        "result": copy.deepcopy(doc["result"]),
        "completion": copy.deepcopy(doc["completion"]),
    }
    if warnings:
        out["warnings"] = warnings
    if result_wait is not None:
        out["result_wait"] = {
            "completed": True,
            "result_status": result_wait["result"]["status"],
            "task_status": result_wait["task"]["status"],
        }
    elif handoff.mode == "sync" and dispatch_outcome == "sent":
        out["result_wait"] = {"completed": False}
    return out
