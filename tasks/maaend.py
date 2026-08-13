import os
from pathlib import Path
import subprocess

path = Path(r"D:\OneDrive\win\桌面\MaaEnd.exe.lnk")


def run():
    """启动 MAAEnd 程序。"""

    if not path.exists():
        raise FileNotFoundError("MAA shortcut not found.")

    subprocess.run(
        ["taskkill", "/F", "/T", "/IM", "MAA.exe"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    os.startfile(path)

# 后续由脚本自动完成
