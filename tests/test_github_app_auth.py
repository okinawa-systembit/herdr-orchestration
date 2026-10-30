"""Unit tests for GitHub App authentication module."""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.lib.github_app_auth import (
    b64url,
    fetch_installation_token,
    generate_jwt,
    load_bot_config,
    main as cli_main,
    obtain_token,
)


class TestGitHubAppAuth(unittest.TestCase):
    """GitHub App 認証モジュールの単体テスト."""

    test_rsa_pem: bytes = b""
    test_pub_pem: bytes = b""

    @classmethod
    def setUpClass(cls) -> None:
        """テスト用の RSA 秘密鍵および公開鍵を生成する."""
        gen_proc = subprocess.run(
            ["openssl", "genrsa", "2048"],
            capture_output=True,
            check=True,
        )
        cls.test_rsa_pem = gen_proc.stdout

        with tempfile.NamedTemporaryFile("wb", delete=False) as key_f:
            key_f.write(cls.test_rsa_pem)
            key_file = key_f.name

        try:
            pub_proc = subprocess.run(
                ["openssl", "rsa", "-in", key_file, "-pubout"],
                capture_output=True,
                check=True,
            )
            cls.test_pub_pem = pub_proc.stdout
        finally:
            try:
                os.unlink(key_file)
            except OSError:
                pass

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # テスト実行時の環境変数を退避
        self._orig_env = {
            "GITHUB_BOT_APP_ID": os.environ.get("GITHUB_BOT_APP_ID"),
            "GITHUB_BOT_INSTALL_ID": os.environ.get("GITHUB_BOT_INSTALL_ID"),
            "GITHUB_APP_PRIVATE_KEY_PATH": os.environ.get(
                "GITHUB_APP_PRIVATE_KEY_PATH"
            ),
        }
        for k in self._orig_env:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        # 環境変数を復元
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.temp_dir.cleanup()

    def test_b64url(self) -> None:
        """b64url のエンコードとパディング除去のテスト."""
        self.assertEqual(b64url(b"hello world"), "aGVsbG8gd29ybGQ")
        self.assertNotIn("=", b64url(b"a"))
        self.assertNotIn("=", b64url(b"any arbitrary byte string here"))

    def test_generate_jwt_structure_and_signature(self) -> None:
        """生成された JWT の構造と署名が有効であることの検証."""
        now = 1700000000
        token = generate_jwt("12345", self.test_rsa_pem, now=now)
        parts = token.split(".")
        self.assertEqual(len(parts), 3)

        # Header の検証
        header_raw = base64.urlsafe_b64decode(parts[0] + "==")
        header = json.loads(header_raw.decode("utf-8"))
        self.assertEqual(header, {"alg": "RS256", "typ": "JWT"})

        # Payload の検証
        payload_raw = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(payload_raw.decode("utf-8"))
        self.assertEqual(payload["iss"], 12345)
        self.assertEqual(payload["iat"], now - 60)
        self.assertEqual(payload["exp"], now + 540)

        # 署名の検証 (openssl dgst -sha256 -verify を使用)
        signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
        pad_len = (4 - len(parts[2]) % 4) % 4
        sig_bytes = base64.urlsafe_b64decode(parts[2] + ("=" * pad_len))

        with tempfile.NamedTemporaryFile("wb", delete=False) as pub_f:
            pub_f.write(self.test_pub_pem)
            pub_file = pub_f.name

        with tempfile.NamedTemporaryFile("wb", delete=False) as sig_f:
            sig_f.write(sig_bytes)
            sig_file = sig_f.name

        try:
            verify_proc = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    pub_file,
                    "-signature",
                    sig_file,
                ],
                input=signing_input,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verify_proc.returncode, 0)
            self.assertIn("Verified OK", verify_proc.stdout.decode("utf-8"))
        finally:
            for p in (pub_file, sig_file):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    def test_generate_jwt_string_app_id(self) -> None:
        """app_id が非数値文字列の場合、文字列として iss に格納されること."""
        token = generate_jwt("my-bot-app", self.test_rsa_pem, now=1700000000)
        parts = token.split(".")
        payload_raw = base64.urlsafe_b64decode(parts[1] + "==")
        payload = json.loads(payload_raw.decode("utf-8"))
        self.assertEqual(payload["iss"], "my-bot-app")

    def test_generate_jwt_invalid_key(self) -> None:
        """不正な秘密鍵 PEM で ValueError が送出されること."""
        with self.assertRaises(ValueError):
            generate_jwt("12345", b"INVALID PEM DATA")

    def test_load_bot_config_from_env(self) -> None:
        """環境変数からの Bot 設定読み込み."""
        os.environ["GITHUB_BOT_APP_ID"] = "111"
        os.environ["GITHUB_BOT_INSTALL_ID"] = "222"
        os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = "/path/to/key.pem"

        cfg = load_bot_config(self.tmp_path)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.app_id, "111")
        self.assertEqual(cfg.install_id, "222")
        self.assertEqual(cfg.key_path, "/path/to/key.pem")
        self.assertEqual(cfg.source, "env")

    def test_load_bot_config_env_priority_over_file(self) -> None:
        """環境変数が設定ファイルより優先されること."""
        env_file = self.tmp_path / ".env.github-app"
        env_file.write_text(
            "GITHUB_BOT_APP_ID=file_app\n"
            "GITHUB_BOT_INSTALL_ID=file_inst\n"
            "GITHUB_APP_PRIVATE_KEY_PATH=/file/key.pem\n",
            encoding="utf-8",
        )

        os.environ["GITHUB_BOT_APP_ID"] = "env_app"

        cfg = load_bot_config(self.tmp_path)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.app_id, "env_app")
        self.assertEqual(cfg.source, "env")

    def test_load_bot_config_from_file_dot_env_github_app(self) -> None:
        """設定ファイルからの読み込み (.env.github-app)."""
        env_file = self.tmp_path / ".env.github-app"
        env_file.write_text(
            "# コメント行\n"
            "GITHUB_BOT_APP_ID='999'\n"
            "GITHUB_BOT_INSTALL_ID=\"888\"\n"
            "GITHUB_APP_PRIVATE_KEY_PATH=/app/key.pem\n",
            encoding="utf-8",
        )

        cfg = load_bot_config(self.tmp_path)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.app_id, "999")
        self.assertEqual(cfg.install_id, "888")
        self.assertEqual(cfg.key_path, "/app/key.pem")
        self.assertEqual(cfg.source, ".env.github-app")

    def test_load_bot_config_from_file_dot_env_github(self) -> None:
        """設定ファイルからの読み込み (.env.github へのフォールバック)."""
        env_file = self.tmp_path / ".env.github"
        env_file.write_text(
            "GITHUB_BOT_APP_ID=777\n"
            "GITHUB_BOT_INSTALL_ID=666\n"
            "GITHUB_APP_PRIVATE_KEY_PATH=/app/fallback.pem\n",
            encoding="utf-8",
        )

        cfg = load_bot_config(self.tmp_path)
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(cfg.app_id, "777")
        self.assertEqual(cfg.install_id, "666")
        self.assertEqual(cfg.key_path, "/app/fallback.pem")
        self.assertEqual(cfg.source, ".env.github")

    def test_load_bot_config_none(self) -> None:
        """設定が一切存在しない場合は None を返す."""
        self.assertIsNone(load_bot_config(self.tmp_path))

    def test_fetch_installation_token_success(self) -> None:
        """トークン取得 API 呼び出しの成功テスト."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"token": "ghs_test_token_abc"}
        ).encode("utf-8")

        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response

        token = fetch_installation_token("jwt.dummy.token", "12345", opener=mock_opener)
        self.assertEqual(token, "ghs_test_token_abc")

    def test_fetch_installation_token_http_error(self) -> None:
        """トークン取得 API が HTTP エラーを返した場合."""
        mock_opener = MagicMock()
        err = urllib.error.HTTPError(
            url="https://api.github.com/...",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),
            fp=io.BytesIO(b'{"message":"Bad credentials"}'),
        )
        mock_opener.open.side_effect = err

        with self.assertRaises(RuntimeError) as cm:
            fetch_installation_token("jwt.dummy.token", "12345", opener=mock_opener)
        self.assertIn("HTTP 401", str(cm.exception))

    def test_fetch_installation_token_missing_token_field(self) -> None:
        """レスポンス JSON に token がない場合."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"message": "success but no token"}'
        mock_opener = MagicMock()
        mock_opener.open.return_value = mock_response

        with self.assertRaises(RuntimeError) as cm:
            fetch_installation_token("jwt.dummy.token", "12345", opener=mock_opener)
        self.assertIn("token が含まれていません", str(cm.exception))

    def test_obtain_token_no_config(self) -> None:
        """設定が一切ない場合、personal モードでエラーなしを返す."""
        res = obtain_token(self.tmp_path)
        self.assertEqual(res.mode, "personal")
        self.assertIsNone(res.token)
        self.assertIsNone(res.error)

    def test_obtain_token_incomplete_config_stops_with_error(self) -> None:
        """Bot 設定が不完全な場合、個人認証へフォールバックせずエラーになる."""
        os.environ["GITHUB_BOT_APP_ID"] = "123"

        res = obtain_token(self.tmp_path)
        self.assertEqual(res.mode, "bot")
        self.assertIsNone(res.token)
        self.assertIsNotNone(res.error)
        assert res.error is not None
        self.assertIn("不足があります", res.error)
        self.assertIn("GITHUB_BOT_INSTALL_ID", res.error)
        self.assertIn("GITHUB_APP_PRIVATE_KEY_PATH", res.error)

    def test_obtain_token_missing_key_file_stops_with_error(self) -> None:
        """秘密鍵ファイルが存在しない場合、個人認証へフォールバックせずエラーになる."""
        os.environ["GITHUB_BOT_APP_ID"] = "123"
        os.environ["GITHUB_BOT_INSTALL_ID"] = "456"
        os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = str(
            self.tmp_path / "nonexistent.pem"
        )

        res = obtain_token(self.tmp_path)
        self.assertEqual(res.mode, "bot")
        self.assertIsNone(res.token)
        self.assertIsNotNone(res.error)
        assert res.error is not None
        self.assertIn("秘密鍵ファイルが見つかりません", res.error)

    def test_obtain_token_success(self) -> None:
        """設定と秘密鍵ファイルが揃っている場合、正常にトークンを取得する."""
        key_path = self.tmp_path / "bot.pem"
        key_path.write_bytes(self.test_rsa_pem)

        os.environ["GITHUB_BOT_APP_ID"] = "123"
        os.environ["GITHUB_BOT_INSTALL_ID"] = "456"
        os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = str(key_path)

        with patch("scripts.lib.github_app_auth.fetch_installation_token") as mock_fetch:
            mock_fetch.return_value = "ghs_mocked_token"
            res = obtain_token(self.tmp_path)

        self.assertEqual(res.mode, "bot")
        self.assertEqual(res.token, "ghs_mocked_token")
        self.assertIsNone(res.error)

    def test_main_cli_personal(self) -> None:
        """設定なし時、exit 0 で stderr にメッセージ出力、stdout は空."""
        with patch("sys.argv", ["github_app_auth.py", str(self.tmp_path)]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                    code = cli_main()

        self.assertEqual(code, 0)
        self.assertEqual(mock_stdout.getvalue(), "")
        self.assertIn("GitHub App 設定なし", mock_stderr.getvalue())

    def test_main_cli_bot_success(self) -> None:
        """Bot 認証成功時、exit 0 で stdout にトークンを出力."""
        key_path = self.tmp_path / "bot.pem"
        key_path.write_bytes(self.test_rsa_pem)

        os.environ["GITHUB_BOT_APP_ID"] = "123"
        os.environ["GITHUB_BOT_INSTALL_ID"] = "456"
        os.environ["GITHUB_APP_PRIVATE_KEY_PATH"] = str(key_path)

        with patch("scripts.lib.github_app_auth.fetch_installation_token") as mock_fetch:
            mock_fetch.return_value = "ghs_valid_app_token"
            with patch("sys.argv", ["github_app_auth.py", str(self.tmp_path)]):
                with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                    with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                        code = cli_main()

        self.assertEqual(code, 0)
        self.assertEqual(mock_stdout.getvalue(), "ghs_valid_app_token\n")
        self.assertEqual(mock_stderr.getvalue(), "")

    def test_main_cli_error(self) -> None:
        """Bot 設定不備時、exit 1 で stderr にエラーメッセージを出力."""
        os.environ["GITHUB_BOT_APP_ID"] = "123"

        with patch("sys.argv", ["github_app_auth.py", str(self.tmp_path)]):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
                    code = cli_main()

        self.assertEqual(code, 1)
        self.assertEqual(mock_stdout.getvalue(), "")
        self.assertIn("ERROR:", mock_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
