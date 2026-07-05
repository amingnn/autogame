import os
from pathlib import Path


def _shortcut_candidates() -> list[Path]:
    configured = os.environ.get("AUTOGAME_MAA_SHORTCUT")
    if configured:
        return [Path(configured)]

    desktop = Path(r"C:\Users\GamerBot\Desktop")
    return [
        desktop / "MAA.exe - 快捷方式.lnk",
        desktop / "MAA - 快捷方式.lnk",
        desktop / "MAA.lnk",
        desktop / "明日方舟小助手.lnk",
    ]


def run() -> None:
    for shortcut in _shortcut_candidates():
        if shortcut.exists():
            os.startfile(shortcut)
            return

    candidates = "\n".join(str(path) for path in _shortcut_candidates())
    raise FileNotFoundError(
        "MAA shortcut not found. Set AUTOGAME_MAA_SHORTCUT or create one of:\n"
        f"{candidates}"
    )