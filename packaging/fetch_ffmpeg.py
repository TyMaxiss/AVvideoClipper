"""Put an ffmpeg binary next to the spec file so PyInstaller can bundle it.

Takes the build shipped with the imageio-ffmpeg package and checks that it
can encode H.264 and read MPEG-TS.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg


def main() -> int:
    src = imageio_ffmpeg.get_ffmpeg_exe()
    dst_dir = Path(__file__).resolve().parent / "ffmpeg"
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    shutil.copy2(src, dst)
    print(f"{src} -> {dst}")

    version = subprocess.run([str(dst), "-hide_banner", "-version"], capture_output=True, text=True).stdout
    print(version.splitlines()[0] if version else "unknown ffmpeg version")
    encoders = subprocess.run([str(dst), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "libx264" not in encoders:
        print("ERROR: this ffmpeg has no libx264 encoder", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        ts = os.path.join(tmp, "check.ts")
        subprocess.run([str(dst), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:d=1",
                        "-c:v", "mpeg2video", ts], check=True)
        probe = subprocess.run([str(dst), "-hide_banner", "-i", ts], capture_output=True, text=True)
        if "Stream #0" not in probe.stderr:
            print(f"ERROR: this ffmpeg cannot read MPEG-TS (exit code {probe.returncode})", file=sys.stderr)
            return 1
    print("ffmpeg OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
