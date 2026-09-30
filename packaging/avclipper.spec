# -*- mode: python ; coding: utf-8 -*-
# Windows build:
#   python packaging/fetch_ffmpeg.py
#   pyinstaller packaging/avclipper.spec --noconfirm
# Result: dist/AVvideoClipper/AVvideoClipper.exe
import os
from pathlib import Path

HERE = Path(SPECPATH).resolve()
ROOT = HERE.parent
FFMPEG = HERE / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
if not FFMPEG.exists():
    raise SystemExit("ffmpeg is missing: run packaging/fetch_ffmpeg.py first")

a = Analysis(
    [str(HERE / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[(str(FFMPEG), ".")],
    datas=[],
    hiddenimports=[],
    excludes=["imageio_ffmpeg", "tkinter", "matplotlib", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AVvideoClipper",
    console=False,
    icon=str(HERE / "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AVvideoClipper", upx=False)
