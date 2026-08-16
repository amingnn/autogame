"""封装森空岛认证、签名和签到 HTTP 接口。"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib import parse

import requests

from autogame.tasks.skyland_sign.security_sm import get_d_id


APP_CODE = "4ca99fa6b56cc2ba"
BINDING_URL = "https://zonai.skland.com/api/v1/game/player/binding"
GRANT_CODE_URL = "https://as.hypergryph.com/user/oauth2/v2/grant"
CRED_CODE_URL = "https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code"
PASSWORD_TOKEN_URL = "https://as.hypergryph.com/user/auth/v1/token_by_phone_password"
SIGN_URLS = {
    "arknights": "https://zonai.skland.com/api/v1/game/attendance",
    "endfield": "https://zonai.skland.com/web/v1/game/endfield/attendance",
}
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12; SM-A5560 Build/V417IR; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/101.0.4951.61 Safari/537.36; SKLand/1.52.1"
)


class SkylandAuthenticationError(RuntimeError):
    """表示森空岛通行证或签到凭据已经失效。"""


class SkylandClient:
    """使用一个通行证 Token 完成森空岛签到。"""

    def __init__(self, passport_token: str, timeout_seconds: float = 20.0) -> None:
        self._session = requests.Session()
        self._timeout_seconds = timeout_seconds
        self._device_id = get_d_id()
        self._initialize_credential(passport_token)

    @classmethod
    def from_password(
        cls,
        phone: str,
        password: str,
        timeout_seconds: float = 20.0,
    ) -> "SkylandClient":
        """使用手机号和密码登录并创建签到客户端。"""

        session = requests.Session()
        device_id = get_d_id()
        response = session.post(
            PASSWORD_TOKEN_URL,
            json={"phone": phone, "password": password},
            headers=cls._login_headers(device_id),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != 0:
            raise RuntimeError(f"手机号密码登录失败：{payload.get('msg', payload)}")
        try:
            passport_token = str(payload["data"]["token"])
        except (KeyError, TypeError) as exc:
            raise RuntimeError(f"手机号密码登录返回数据无效：{payload}") from exc

        client = cls.__new__(cls)
        client._session = session
        client._timeout_seconds = timeout_seconds
        client._device_id = device_id
        client._initialize_credential(passport_token)
        return client

    @staticmethod
    def _login_headers(device_id: str) -> dict[str, str]:
        """生成手机号登录和通行证换取接口共用的请求头。"""

        return {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Connection": "close",
            "dId": device_id,
            "X-Requested-With": "com.hypergryph.skland",
        }

    def _initialize_credential(self, passport_token: str) -> None:
        """用通行证 Token 初始化森空岛签名凭据。"""

        self._passport_token = passport_token
        credential = self._get_credential(passport_token)
        self._signature_token = str(credential["token"])
        self._headers = {
            "cred": str(credential["cred"]),
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip",
            "Connection": "close",
            "X-Requested-With": "com.hypergryph.skland",
        }

    def sign_all(self) -> list[str]:
        """为当前账号下支持的全部角色签到。"""

        messages: list[str] = []
        for binding in self._get_bindings():
            app_code = binding.get("appCode")
            if app_code == "arknights":
                messages.extend(self._sign_arknights(binding))
            elif app_code == "endfield":
                messages.extend(self._sign_endfield(binding))
        return messages

    def _get_credential(self, passport_token: str) -> dict[str, object]:
        """把通行证 Token 换取森空岛凭据。"""

        login_headers = self._login_headers(self._device_id)
        grant_response = self._session.post(
            GRANT_CODE_URL,
            json={"appCode": APP_CODE, "token": passport_token, "type": 0},
            headers=login_headers,
            timeout=self._timeout_seconds,
        )
        self._raise_for_status(grant_response, "通行证 Token")
        grant = grant_response.json()
        if grant.get("status") != 0:
            raise RuntimeError(f"获得认证代码失败：{grant.get('msg', grant)}")

        credential_response = self._session.post(
            CRED_CODE_URL,
            json={"code": grant["data"]["code"], "kind": 1},
            headers=login_headers,
            timeout=self._timeout_seconds,
        )
        self._raise_for_status(credential_response, "森空岛凭据")
        credential = credential_response.json()
        if credential.get("code") != 0:
            raise RuntimeError(f"获得森空岛凭据失败：{credential.get('message', credential)}")
        return credential["data"]

    def _get_bindings(self) -> list[dict[str, object]]:
        """读取当前账号绑定的可签到角色。"""

        response = self._session.get(
            BINDING_URL,
            headers=self._signed_headers(BINDING_URL, "get", None),
            timeout=self._timeout_seconds,
        )
        self._raise_for_status(response, "角色列表")
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"读取角色列表失败：{payload.get('message', payload)}")

        result: list[dict[str, object]] = []
        for game in payload["data"]["list"]:
            app_code = game.get("appCode")
            if app_code not in SIGN_URLS:
                continue
            for binding in game.get("bindingList", []):
                item = dict(binding)
                item["appCode"] = app_code
                result.append(item)
        return result

    def _sign_arknights(self, binding: dict[str, object]) -> list[str]:
        """签到明日方舟角色并返回展示文本。"""

        body = {"gameId": binding.get("gameId"), "uid": binding.get("uid")}
        url = SIGN_URLS["arknights"]
        body_text = json.dumps(body, separators=(",", ":"))
        headers = self._signed_headers(url, "post", body)
        headers["Content-Type"] = "application/json"
        response = self._session.post(
            url,
            headers=headers,
            data=body_text,
            timeout=self._timeout_seconds,
        )
        label = self._role_label(binding)
        payload = response.json()
        if response.status_code == 403 and payload.get("code") == 10001:
            return [f"{label}今日已签到"]
        self._raise_for_status(response, "明日方舟签到")
        if payload.get("code") != 0:
            return [f"{label}签到失败：{payload.get('message', payload)}"]
        awards = "".join(
            f"{item['resource']['name']}×{item.get('count') or 1}"
            for item in payload["data"]["awards"]
        )
        return [f"{label}签到成功，获得了{awards}"]

    def _sign_endfield(self, binding: dict[str, object]) -> list[str]:
        """签到终末地账号下的全部角色。"""

        messages: list[str] = []
        url = SIGN_URLS["endfield"]
        for role in binding.get("roles", []):
            headers = self._signed_headers(url, "post", None)
            headers.update(
                {
                    "Content-Type": "application/json",
                    "sk-game-role": f"3_{role['roleId']}_{role['serverId']}",
                    "referer": "https://game.skland.com/",
                    "origin": "https://game.skland.com/",
                }
            )
            response = self._session.post(
                url,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            payload = response.json()
            role_binding = dict(binding)
            role_binding["nickName"] = role.get("nickname", "")
            label = self._role_label(role_binding)
            if response.status_code == 403 and payload.get("code") == 10001:
                messages.append(f"{label}今日已签到")
                continue
            self._raise_for_status(response, "终末地签到")
            if payload.get("code") != 0:
                messages.append(f"{label}签到失败：{payload.get('message', payload)}")
                continue
            info_map = payload["data"]["resourceInfoMap"]
            awards = [
                f"{info_map[item['id']]['name']}×{info_map[item['id']]['count']}"
                for item in payload["data"]["awardIds"]
            ]
            messages.append(f"{label}签到成功，获得了：{','.join(awards)}")
        return messages

    @property
    def passport_token(self) -> str:
        """返回当前客户端使用的通行证 Token，供成功刷新后缓存。"""

        return self._passport_token

    @staticmethod
    def _raise_for_status(response: requests.Response, action: str) -> None:
        """把 401 转换成可触发密码刷新的明确异常。"""

        if response.status_code == 401:
            raise SkylandAuthenticationError(f"{action}认证已失效（HTTP 401）")
        response.raise_for_status()

    def _signed_headers(
        self,
        url: str,
        method: str,
        body: dict[str, object] | None,
    ) -> dict[str, str]:
        """生成森空岛接口要求的签名请求头。"""

        parsed = parse.urlparse(url)
        body_or_query = (
            parsed.query
            if method.lower() == "get"
            else json.dumps(body, separators=(",", ":")) if body is not None else ""
        )
        timestamp = str(int(time.time()) - 2)
        signed = {
            "platform": "3",
            "timestamp": timestamp,
            "dId": self._device_id,
            "vName": "1.0.0",
        }
        signed_json = json.dumps(signed, separators=(",", ":"))
        raw = parsed.path + body_or_query + timestamp + signed_json
        digest = hmac.new(
            self._signature_token.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signature = hashlib.md5(digest.encode("utf-8")).hexdigest()
        headers = dict(self._headers)
        headers.update(signed)
        headers["sign"] = signature
        return headers

    @staticmethod
    def _role_label(binding: dict[str, object]) -> str:
        """生成用于通知的角色名称。"""

        game = binding.get("gameName", "未知游戏")
        nickname = binding.get("nickName") or ""
        channel = binding.get("channelName", "")
        return f"[{game}]角色{nickname}({channel})"
