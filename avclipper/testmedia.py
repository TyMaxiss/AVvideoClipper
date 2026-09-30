"""Synthetic recordings for tests and the self-check: snow, test pattern, black screen."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Sequence, Tuple, Union

from .media import find_ffmpeg, popen_flags

X264 = ("-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-pix_fmt", "yuv420p")
MJPEG = ("-c:v", "mjpeg", "-q:v", "4", "-pix_fmt", "yuvj420p")


def make_test_video(path: Union[str, Path], pieces: Sequence[Tuple[str, float]], *,
                    size: str = "320x240", fps: int = 25, vcodec: Sequence[str] = X264,
                    acodec: Sequence[str] = ("-c:a", "aac"), audio: bool = True, border: int = 0,
                    double_fps: bool = False) -> List[Tuple[float, float]]:
    """Write a recording built from ("snow" | "video" | "black", seconds) pieces.

    `border` adds static black side bars, `double_fps` duplicates every frame.
    Returns the true (start, end) spans of the "video" pieces.
    """
    args = [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    truth, t = [], 0.0
    for kind, dur in pieces:
        if kind == "snow":
            src = f"color=c=gray:s={size}:r={fps}:d={dur},noise=alls=100:allf=t+u"
        elif kind == "video":
            src = f"testsrc2=s={size}:r={fps}:d={dur}"
            truth.append((t, t + dur))
        elif kind == "black":
            src = f"color=c=black:s={size}:r={fps}:d={dur}"
        else:
            raise ValueError(kind)
        args += ["-f", "lavfi", "-i", src]
        t += dur
    n = len(pieces)
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={t}"]
    chain = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0"
    if border:
        chain += f",pad=iw+{2 * border}:ih:{border}:0:black"
    if double_fps:
        chain += f",fps={2 * fps}"
    chain += "[v]"
    args += ["-filter_complex", chain, "-map", "[v]"]
    if audio:
        args += ["-map", f"{n}:a"] + list(acodec)
    args += list(vcodec) + [str(path)]
    subprocess.run(args, check=True, capture_output=True, **popen_flags())
    return truth
