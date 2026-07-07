import os
from pathlib import Path
import subprocess

path = Path(r"D:\OneDrive\win\桌面\MAA.exe.lnk")

def run() -> None:
    if not path.exists():
        raise FileNotFoundError("MAA shortcut not found.")

    subprocess.run(
        ["taskkill", "/F", "/T", "/IM", "MAA.exe"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    os.startfile(path)