#!/usr/bin/env python3
"""GitHub App インストールアクセストークン自動取得モジュール.

GitHub App の App ID、Installation ID、および秘密鍵 (.pem) から
RS256 JWT を生成し、GitHub REST API より有効期限 1 時間の
インストールアクセストークン (ghs_...) を発行します。
外部ライブラリ (cryptography 等) への依存なしに、OS 標準の openssl CLI
でも動作するハイブリッド設計となっています。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple


class BotConfig(NamedTuple):
    app_id: str
    install_id: str
    key_path: str
    source: str  # "env" or "file"


class AuthResult(NamedTuple):
    token: str | None
    mode: str  # "bot" or "personal"
    error: str | None


def b64url(data: bytes) -> str:
    """Base64URL エンコード (パディング '=' なし)."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def resolve_repo_root(start_dir: Path | None = None) -> Path:
    """カレントディレクトリまたは指定パスからリポジトリルートを解決する.

    1. git rev-parse --show-toplevel を試行
       - 対象 worktree に .env.github-app がない場合、--git-common-dir の親 (メインリポジトリ) も探索
    2. 失敗した場合は start_dir から親方向へ .git または .env.github-app/.env.github を探索
    3. 見つからなければ start_dir (または cwd) を返す
    """
    base = (start_dir if start_dir is not None else Path.cwd()).resolve()

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            top_level = proc.stdout.strip()
            if top_level:
                top_path = Path(top_level).resolve()
                if (top_path / ".env.github-app").is_file() or (
                    top_path / ".env.github"
                ).is_file():
                    return top_path

                # linked worktree の場合、メインリポジトリ側の設定もフォールバック探索
                proc_common = subprocess.run(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=base,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc_common.returncode == 0:
                    common_dir = proc_common.stdout.strip()
                    if common_dir:
                        common_path = (top_path / common_dir).resolve()
                        main_root = common_path.parent
                        if (main_root / ".env.github-app").is_file() or (
                            main_root / ".env.github"
                        ).is_file():
                            return main_root

                return top_path
    except OSError:
        pass

    # git コマンドが使えない環境での親ディレクトリ探索
    curr = base
    while True:
        if (curr / ".git").exists():
            return curr
        if (curr / ".env.github-app").is_file() or (curr / ".env.github").is_file():
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent

    return base


def _parse_env_line(line: str) -> tuple[str, str] | None:
    """設定ファイルの 1 行をキーと値に分解する."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        return None

    k, v = line.split("=", 1)
    k = k.strip()
    v = v.strip()

    if not v:
        return k, ""

    # クォートで始まっている場合: "val" または 'val'
    if v[0] in ('"', "'"):
        quote_char = v[0]
        close_idx = v.find(quote_char, 1)
        if close_idx != -1:
            val = v[1:close_idx]
            return k, val

    # クォートで始まっていない場合: 行末コメント ( #...) を除去
    if " #" in v:
        v = v.split(" #", 1)[0].rstrip()
    elif "\t#" in v:
        v = v.split("\t#", 1)[0].rstrip()

    return k, v


def load_bot_config(repo_root: Path | None = None) -> BotConfig | None:
    """環境変数または設定ファイルから Bot 設定を読み込む.

    優先順位:
    1. 環境変数 (GITHUB_BOT_APP_ID, GITHUB_BOT_INSTALL_ID, GITHUB_APP_PRIVATE_KEY_PATH)
    2. リポジトリ内の .env.github-app または .env.github

    いずれのソースでも、1 項目でも設定があればそのソースから BotConfig を生成する.
    まったく設定が存在しない場合は None を返す.
    """
    env_app_id = os.environ.get("GITHUB_BOT_APP_ID", "").strip()
    env_inst_id = os.environ.get("GITHUB_BOT_INSTALL_ID", "").strip()
    env_key_path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()

    if env_app_id or env_inst_id or env_key_path:
        return BotConfig(
            app_id=env_app_id,
            install_id=env_inst_id,
            key_path=env_key_path,
            source="env",
        )

    root = resolve_repo_root(repo_root) if repo_root is not None else resolve_repo_root()

    for env_filename in (".env.github-app", ".env.github"):
        env_file = root / env_filename
        if env_file.is_file():
            file_values: dict[str, str] = {}
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    parsed = _parse_env_line(line)
                    if parsed is not None:
                        k, v = parsed
                        file_values[k] = v
            except OSError as err:
                raise RuntimeError(
                    f"設定ファイル {env_file} の読み込みに失敗しました: {err}"
                ) from err

            file_app_id = file_values.get("GITHUB_BOT_APP_ID", "").strip()
            file_inst_id = file_values.get("GITHUB_BOT_INSTALL_ID", "").strip()
            file_key_path = file_values.get(
                "GITHUB_APP_PRIVATE_KEY_PATH", ""
            ).strip()

            if file_app_id or file_inst_id or file_key_path:
                return BotConfig(
                    app_id=file_app_id,
                    install_id=file_inst_id,
                    key_path=file_key_path,
                    source=str(env_file.name),
                )

    return None


def _sign_rs256(
    data: bytes,
    private_key_pem: bytes | None = None,
    private_key_path: Path | None = None,
) -> bytes:
    """RS256 (SHA-256 with RSA PKCS#1 v1.5) でデータを署名する.

    cryptography ライブラリが利用可能な場合はそれを使用し、
    未導入の場合は OS 標準の openssl CLI サブプロセスで署名する。
    private_key_path が指定されている場合はディスク上のファイルを直接
    openssl に渡し、一時ファイルの複製を作成しない。
    """
    if private_key_pem is None and private_key_path is None:
        raise ValueError("private_key_pem または private_key_path のいずれかが必要です。")

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem_data = (
            private_key_pem
            if private_key_pem is not None
            else private_key_path.read_bytes()  # type: ignore[union-attr]
        )

        try:
            private_key = load_pem_private_key(pem_data, password=None)
        except Exception as err:
            raise ValueError(f"秘密鍵 PEM の解析に失敗しました: {err}") from err

        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError("RSA 秘密鍵ではありません。")

        try:
            return private_key.sign(
                data,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as err:
            raise RuntimeError(f"JWT の署名に失敗しました: {err}") from err

    except ImportError:
        pass

    # openssl CLI へのフォールバック
    openssl_path = shutil.which("openssl")
    if not openssl_path:
        raise RuntimeError(
            "署名に必要な cryptography パッケージまたは openssl コマンドが見つかりません。"
        )

    # 既存の秘密鍵ファイルが存在する場合は直接利用 (一時ファイル作成なし)
    if private_key_path is not None and private_key_path.is_file():
        proc = subprocess.run(
            [openssl_path, "dgst", "-sha256", "-sign", str(private_key_path)],
            input=data,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"openssl による署名に失敗しました: {err_msg}")
        return proc.stdout

    # メモリ上の PEM バイト列のみの場合は一時ファイルを作成して署名
    temp_dir = os.environ.get("XDG_RUNTIME_DIR")
    if temp_dir and not os.path.isdir(temp_dir):
        temp_dir = None

    temp_fd, temp_key_path = tempfile.mkstemp(
        prefix="gh_app_key_", suffix=".pem", dir=temp_dir
    )
    try:
        with os.fdopen(temp_fd, "wb") as f:
            assert private_key_pem is not None
            f.write(private_key_pem)

        proc = subprocess.run(
            [openssl_path, "dgst", "-sha256", "-sign", temp_key_path],
            input=data,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"openssl による署名に失敗しました: {err_msg}")

        return proc.stdout
    finally:
        try:
            os.unlink(temp_key_path)
        except OSError:
            pass


def generate_jwt(
    app_id: str,
    private_key_pem: bytes | None = None,
    now: int | None = None,
    private_key_path: Path | None = None,
) -> str:
    """RS256 で署名された GitHub App JWT を生成する."""
    current_time = int(time.time()) if now is None else now
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iat": current_time - 60,
        "exp": current_time + 540,  # 9分後 (GitHub 上限は10分)
        "iss": int(app_id) if app_id.isdigit() else app_id,
    }

    encoded_header = b64url(json.dumps(header).encode("utf-8"))
    encoded_payload = b64url(json.dumps(payload).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")

    signature = _sign_rs256(
        signing_input,
        private_key_pem=private_key_pem,
        private_key_path=private_key_path,
    )
    encoded_signature = b64url(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def fetch_installation_token(
    jwt_token: str,
    install_id: str,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """GitHub REST API から Installation Access Token を取得する."""
    url = f"https://api.github.com/app/installations/{install_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "herdr-orchestrator-auth",
    }

    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")

    try:
        if opener is not None:
            response = opener.open(req, timeout=15)
        else:
            response = urllib.request.urlopen(req, timeout=15)
        response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        error_body = ""
        try:
            error_body = err.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(
            f"GitHub API トークン取得に失敗しました (HTTP {err.code}): {error_body}"
        ) from err
    except urllib.error.URLError as err:
        raise RuntimeError(
            f"GitHub API との通信に失敗しました: {err.reason}"
        ) from err

    try:
        data = json.loads(response_body)
    except Exception as err:
        raise RuntimeError(
            f"GitHub API のレスポンス解析に失敗しました: {err}"
        ) from err

    token = data.get("token")
    if not token or not isinstance(token, str):
        raise RuntimeError(
            f"GitHub API の応答に token が含まれていません: {response_body[:200]}"
        )

    return token


def obtain_token(repo_root: Path | None = None) -> AuthResult:
    """Bot 設定があればトークンを取得し、なければ個人認証モードとする."""
    try:
        config = load_bot_config(repo_root)
    except Exception as err:
        return AuthResult(token=None, mode="bot", error=str(err))

    if config is None:
        return AuthResult(token=None, mode="personal", error=None)

    # 1 項目でも設定されている場合は Bot モード。不足があれば即時エラー
    missing: list[str] = []
    if not config.app_id:
        missing.append("GITHUB_BOT_APP_ID")
    if not config.install_id:
        missing.append("GITHUB_BOT_INSTALL_ID")
    if not config.key_path:
        missing.append("GITHUB_APP_PRIVATE_KEY_PATH")

    if missing:
        return AuthResult(
            token=None,
            mode="bot",
            error=(
                f"GitHub App 設定 ({config.source}) に不足があります: "
                f"{', '.join(missing)} が必要です"
            ),
        )

    def _is_valid_positive_int(s: str) -> bool:
        return s.isascii() and s.isdigit() and int(s) > 0

    if not _is_valid_positive_int(config.install_id):
        return AuthResult(
            token=None,
            mode="bot",
            error=(
                f"GITHUB_BOT_INSTALL_ID には正の整数を指定してください: "
                f"{config.install_id!r}"
            ),
        )

    if not _is_valid_positive_int(config.app_id):
        return AuthResult(
            token=None,
            mode="bot",
            error=(
                f"GITHUB_BOT_APP_ID には正の整数を指定してください: "
                f"{config.app_id!r}"
            ),
        )

    key_file = Path(os.path.expanduser(config.key_path))
    if not key_file.is_file():
        return AuthResult(
            token=None,
            mode="bot",
            error=f"秘密鍵ファイルが見つかりません: {config.key_path}",
        )

    try:
        # ディスク上の秘密鍵ファイルを直接使用 (一時ファイル複製なし)
        jwt = generate_jwt(config.app_id, private_key_path=key_file)
        token = fetch_installation_token(jwt, config.install_id)
    except Exception as err:
        return AuthResult(token=None, mode="bot", error=str(err))

    return AuthResult(token=token, mode="bot", error=None)


def main() -> int:
    """CLI エントリポイント."""
    repo_root = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else resolve_repo_root()
    )

    result = obtain_token(repo_root)

    if result.mode == "personal":
        sys.stderr.write(
            "INFO: GitHub App 設定なし: 個人 gh 認証を使用します。\n"
        )
        return 0

    if result.error is not None:
        sys.stderr.write(f"ERROR: {result.error}\n")
        return 1

    if result.token is not None:
        # stdout に純粋なトークンのみ出力 (改行付き)
        sys.stdout.write(f"{result.token}\n")
        sys.stdout.flush()
        return 0

    sys.stderr.write("ERROR: トークンの取得に失敗しました。\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
