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


def load_bot_config(repo_root: Path) -> BotConfig | None:
    """環境変数または設定ファイルから Bot 設定を読み込む.

    優先順位:
    1. 環境変数 (GITHUB_BOT_APP_ID, GITHUB_BOT_INSTALL_ID, GITHUB_APP_PRIVATE_KEY_PATH)
    2. リポジトリ直下の .env.github-app または .env.github

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

    for env_filename in (".env.github-app", ".env.github"):
        env_file = repo_root / env_filename
        if env_file.is_file():
            file_values: dict[str, str] = {}
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
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


def _sign_rs256(data: bytes, private_key_pem: bytes) -> bytes:
    """RS256 (SHA-256 with RSA PKCS#1 v1.5) でデータを署名する.

    cryptography ライブラリが利用可能な場合はそれを使用し、
    未導入の場合は OS 標準の openssl CLI サブプロセスで署名する。
    """
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        try:
            private_key = load_pem_private_key(private_key_pem, password=None)
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

    # セキュアな一時ファイルに秘密鍵を書き出して署名
    temp_fd, temp_key_path = tempfile.mkstemp(prefix="gh_app_key_", suffix=".pem")
    try:
        with os.fdopen(temp_fd, "wb") as f:
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


def generate_jwt(app_id: str, private_key_pem: bytes, now: int | None = None) -> str:
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

    signature = _sign_rs256(signing_input, private_key_pem)
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
            response = opener.open(req)
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


def obtain_token(repo_root: Path) -> AuthResult:
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

    key_file = Path(os.path.expanduser(config.key_path))
    if not key_file.is_file():
        return AuthResult(
            token=None,
            mode="bot",
            error=f"秘密鍵ファイルが見つかりません: {config.key_path}",
        )

    try:
        pem_bytes = key_file.read_bytes()
    except OSError as err:
        return AuthResult(
            token=None,
            mode="bot",
            error=f"秘密鍵ファイルの読み込みに失敗しました: {err}",
        )

    try:
        jwt = generate_jwt(config.app_id, pem_bytes)
        token = fetch_installation_token(jwt, config.install_id)
    except Exception as err:
        return AuthResult(token=None, mode="bot", error=str(err))

    return AuthResult(token=token, mode="bot", error=None)


def main() -> int:
    """CLI エントリポイント."""
    repo_root = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path.cwd().resolve()
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
