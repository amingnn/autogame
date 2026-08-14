"""实现森空岛签到业务入口。"""

from __future__ import annotations

from dataclasses import dataclass

from autogame.logger import mlog
from autogame.tasks.skyland_sign.client import SkylandClient
from autogame.tasks.skyland_sign.token_store import TokenStore


@dataclass(frozen=True)
class SignInResult:
    """表示一次多账号签到结果。"""

    success: bool
    messages: tuple[str, ...]


def run_sign_in(token_store: TokenStore) -> SignInResult:
    """读取全部账号 Token 并依次完成签到。"""

    tokens = token_store.load()
    if not tokens:
        return SignInResult(
            False,
            (f"未配置森空岛 Token：{token_store.path}",),
        )

    success = True
    messages: list[str] = []
    for token in tokens:
        try:
            account_messages = SkylandClient(token).sign_all()
            messages.extend(account_messages or ["当前账号没有可签到角色"])
        except Exception as exc:
            success = False
            message = f"森空岛签到失败：{exc}"
            messages.append(message)
            mlog.exception(message)
    return SignInResult(success, tuple(messages))
