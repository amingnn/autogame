"""测试森空岛账号登录和凭据回退逻辑。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console
from rich.panel import Panel

# 允许从项目根目录直接执行：uv run .\tests\test_skyland_sign.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autogame.config import Config, SkylandAccountConfig
from autogame.tasks.skyland_sign.client import (
    CRED_CODE_URL,
    GRANT_CODE_URL,
    PASSWORD_TOKEN_URL,
    SkylandAuthenticationError,
    SkylandClient,
)
from autogame.tasks.skyland_sign.service import run_sign_in
from autogame.tasks.skyland_sign.token_store import TokenStore


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class _Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses = [
            _Response({"status": 0, "data": {"token": "passport-token"}}),
            _Response({"status": 0, "data": {"code": "grant-code"}}),
            _Response({"code": 0, "data": {"token": "sign-token", "cred": "cred"}}),
        ]

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _SignSession:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        status_code: int = 200,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.payload = payload or {"code": 0, "data": {"awards": []}}
        self.status_code = status_code

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append((url, kwargs))
        return _Response(self.payload, self.status_code)


class SkylandSignTests(unittest.TestCase):
    @staticmethod
    def _print_business_result(title: str, lines: list[str]) -> None:
        """直接运行测试文件时打印业务结果预览，不输出敏感凭据。"""

        if __name__ != "__main__":
            return
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
        Console(width=110).print(
            "\n",
            Panel("\n".join(lines), title=title, border_style="bright_blue")
        )

    def test_config_loads_account_password_as_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "tasks:\n"
                "  skyland_sign:\n"
                "    account:\n"
                "      phone: '13800000000'\n"
                "      password: 'dummy-password'\n",
                encoding="utf-8",
            )

            account = Config.load(path).tasks["skyland_sign"].account

            self.assertIsNotNone(account)
            assert account is not None
            self.assertTrue(account.is_complete)
            self.assertEqual(account.password.get_secret_value(), "dummy-password")
            self.assertNotIn("dummy-password", str(account))

    def test_password_login_uses_original_project_flow(self) -> None:
        session = _Session()
        with (
            patch("autogame.tasks.skyland_sign.client.requests.Session", return_value=session),
            patch("autogame.tasks.skyland_sign.client.get_d_id", return_value="device-id"),
        ):
            client = SkylandClient.from_password("13800000000", "dummy-password")

        self.assertEqual(
            [url for url, _ in session.calls],
            [PASSWORD_TOKEN_URL, GRANT_CODE_URL, CRED_CODE_URL],
        )
        self.assertEqual(
            session.calls[0][1]["json"],
            {"phone": "13800000000", "password": "dummy-password"},
        )
        self.assertEqual(client._headers["cred"], "cred")
        self._print_business_result(
            "森空岛手机号密码登录结果",
            [
                "登录方式：手机号 + 密码",
                "登录链路：token_by_phone_password → grant_code → cred",
                "结果：登录成功，已获得森空岛签到凭据",
            ],
        )

    def test_arknights_signing_sends_the_exact_signed_json_body(self) -> None:
        session = _SignSession()
        client = SkylandClient.__new__(SkylandClient)
        client._session = session
        client._timeout_seconds = 20.0
        client._device_id = "device-id"
        client._signature_token = "sign-token"
        client._headers = {"cred": "cred"}

        messages = client._sign_arknights(
            {
                "gameId": "1",
                "uid": "2",
                "gameName": "明日方舟",
                "nickName": "测试角色",
                "channelName": "官服",
            }
        )

        _, request = session.calls[0]
        self.assertEqual(request["data"], '{"gameId":"1","uid":"2"}')
        self.assertNotIn("json", request)
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertIn("签到成功", messages[0])
        self._print_business_result(
            "森空岛明日方舟签到请求体校验",
            [
                "签名请求体：紧凑 JSON",
                "实际发送请求体：与签名内容完全一致",
                "结果：请求体校验通过",
            ],
        )

    def test_arknights_duplicate_sign_is_a_successful_result(self) -> None:
        session = _SignSession(
            {"code": 10001, "message": "请勿重复签到"},
            status_code=403,
        )
        client = SkylandClient.__new__(SkylandClient)
        client._session = session
        client._timeout_seconds = 20.0
        client._device_id = "device-id"
        client._signature_token = "sign-token"
        client._headers = {"cred": "cred"}

        messages = client._sign_arknights(
            {"gameId": "1", "uid": "2", "gameName": "明日方舟"}
        )

        self.assertTrue(messages[0].endswith("今日已签到"))
        self._print_business_result(
            "森空岛重复签到结果",
            [
                "HTTP 状态：403",
                "业务码：10001（请勿重复签到）",
                "结果：按已完成处理，不再报告任务失败",
            ],
        )

    def test_endfield_duplicate_sign_is_a_successful_result(self) -> None:
        session = _SignSession(
            {"code": 10001, "message": "请勿重复签到"},
            status_code=403,
        )
        client = SkylandClient.__new__(SkylandClient)
        client._session = session
        client._timeout_seconds = 20.0
        client._device_id = "device-id"
        client._signature_token = "sign-token"
        client._headers = {"cred": "cred"}

        messages = client._sign_endfield(
            {
                "appCode": "endfield",
                "gameName": "终末地",
                "roles": [{"roleId": "1", "serverId": "2", "nickname": "测试角色"}],
            }
        )

        self.assertTrue(messages[0].endswith("今日已签到"))
        self._print_business_result(
            "森空岛终末地重复签到结果",
            [
                "HTTP 状态：403",
                "业务码：10001（请勿重复签到）",
                "结果：按已完成处理，不再报告任务失败",
            ],
        )

    def test_valid_cached_token_is_used_without_password_login(self) -> None:
        account = SkylandAccountConfig(phone="13800000000", password="dummy-password")
        fake_client = Mock()
        fake_client.sign_all.return_value = ["签到成功"]

        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.txt"
            token_path.write_text("cached-token\n", encoding="utf-8")
            with patch("autogame.tasks.skyland_sign.service.SkylandClient") as client:
                client.return_value = fake_client
                result = run_sign_in(TokenStore(token_path), account)
            self.assertEqual(token_path.read_text(encoding="utf-8"), "cached-token\n")

        self.assertTrue(result.success)
        self.assertEqual(result.messages, ("签到成功",))
        client.assert_called_once_with("cached-token")
        client.from_password.assert_not_called()
        self._print_business_result(
            "森空岛缓存 Token 签到结果",
            [
                "缓存 Token：有效，直接使用",
                "手机号密码登录：未调用",
                f"签到结果：{result.messages[0]}",
            ],
        )

    def test_expired_cached_token_is_refreshed_and_saved(self) -> None:
        account = SkylandAccountConfig(phone="13800000000", password="dummy-password")
        refreshed_client = Mock()
        refreshed_client.passport_token = "fresh-token"
        refreshed_client.sign_all.return_value = ["刷新后签到成功"]

        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.txt"
            token_path.write_text("expired-token\n", encoding="utf-8")
            with patch("autogame.tasks.skyland_sign.service.SkylandClient") as client:
                client.side_effect = SkylandAuthenticationError("expired")
                client.from_password.return_value = refreshed_client
                result = run_sign_in(TokenStore(token_path), account)

            self.assertEqual(token_path.read_text(encoding="utf-8"), "fresh-token\n")

        self.assertTrue(result.success)
        self.assertEqual(result.messages, ("刷新后签到成功",))
        client.from_password.assert_called_once_with("13800000000", "dummy-password")
        self._print_business_result(
            "森空岛 Token 失效重登结果",
            [
                "缓存 Token：已失效",
                "处理方式：使用手机号密码重新登录",
                f"签到结果：{result.messages[0]}",
                "缓存更新：已写入新 Token",
            ],
        )

    def test_missing_cached_token_logs_in_and_saves_token(self) -> None:
        account = SkylandAccountConfig(phone="13800000000", password="dummy-password")
        refreshed_client = Mock()
        refreshed_client.passport_token = "first-token"
        refreshed_client.sign_all.return_value = ["首次登录签到成功"]

        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token.txt"
            with patch("autogame.tasks.skyland_sign.service.SkylandClient") as client:
                client.from_password.return_value = refreshed_client
                result = run_sign_in(TokenStore(token_path), account)

            self.assertEqual(token_path.read_text(encoding="utf-8"), "first-token\n")

        self.assertTrue(result.success)
        self.assertEqual(result.messages, ("首次登录签到成功",))
        client.from_password.assert_called_once_with("13800000000", "dummy-password")
        self._print_business_result(
            "森空岛首次登录签到结果",
            [
                "缓存 Token：不存在",
                "处理方式：使用手机号密码登录",
                f"签到结果：{result.messages[0]}",
                "缓存写入：已保存登录后的 Token",
            ],
        )

    def test_partial_account_configuration_is_rejected(self) -> None:
        account = SkylandAccountConfig(phone="13800000000")

        result = run_sign_in(TokenStore(Path("missing-token.txt")), account)

        self.assertFalse(result.success)
        self.assertIn("phone 和 password", result.messages[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
