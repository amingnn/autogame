"""实现森空岛签到业务入口。"""

from __future__ import annotations

from dataclasses import dataclass

from autogame.config import SkylandAccountConfig
from autogame.logger import mlog
from autogame.tasks.skyland_sign.client import (
    SkylandAuthenticationError,
    SkylandClient,
)
from autogame.tasks.skyland_sign.token_store import TokenStore


@dataclass(frozen=True)
class SignInResult:
    """表示一次多账号签到结果。"""

    success: bool
    messages: tuple[str, ...]


def run_sign_in(
    token_store: TokenStore,
    account: SkylandAccountConfig | None = None,
) -> SignInResult:
    """优先使用缓存 Token，仅在认证失效时用手机号密码刷新。"""

    tokens = token_store.load()
    messages: list[str] = []
    valid_tokens: list[str] = []
    expired_tokens = 0
    success = True

    for token in tokens:
        try:
            client_messages = SkylandClient(token).sign_all()
            messages.extend(client_messages or ["当前账号没有可签到角色"])
            valid_tokens.append(token)
        except SkylandAuthenticationError:
            expired_tokens += 1
        except Exception as exc:
            success = False
            message = f"森空岛签到失败：{exc}"
            messages.append(message)
            mlog.exception(message)

    if expired_tokens == 0 and tokens:
        return SignInResult(success, tuple(messages))

    if account is None or not account.is_complete:
        if account is not None and account.has_any_value:
            messages.append("森空岛账号配置不完整：phone 和 password 必须同时填写")
        elif expired_tokens:
            messages.append("森空岛 Token 已失效，请配置 tasks.skyland_sign.account")
        else:
            messages.append(f"未配置森空岛 Token：{token_store.path}")
        return SignInResult(False, tuple(messages))

    try:
        refreshed_client = SkylandClient.from_password(
            account.phone.strip(),
            account.password.get_secret_value(),
        )
        refreshed_messages = refreshed_client.sign_all()
        messages.extend(refreshed_messages or ["当前账号没有可签到角色"])
        token_store.save([*valid_tokens, refreshed_client.passport_token])
        return SignInResult(True, tuple(messages))
    except Exception as exc:
        message = f"森空岛 Token 刷新或签到失败：{exc}"
        messages.append(message)
        mlog.exception(message)
        return SignInResult(False, tuple(messages))
