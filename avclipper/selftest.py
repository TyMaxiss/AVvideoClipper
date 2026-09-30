"""End-to-end self-check: `AVvideoClipper --self-test [log.txt]`.

Generates a short recording with snow, finds the video in it and saves the
result.  Used to verify packaged builds; exit code 0 means everything works.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
import traceback
from typing import List, Optional

from . import __version__
from .analysis import analyze
from .export import MODE_REENCODE, ExportSettings, export
from .media import available_encoders, find_ffmpeg, probe
from .segments import DetectionSettings, find_segments, kept_duration
from .testmedia import MJPEG, make_test_video


def _check(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def run(log_path: Optional[str] = None) -> int:
    lines: List[str] = [f"AV Video Clipper {__version__}, Python {platform.python_version()}, {platform.platform()}"]
    code = 1
    try:
        lines.append(f"ffmpeg: {find_ffmpeg()}")
        lines.append(f"libx264: {'libx264' in available_encoders()}")
        with tempfile.TemporaryDirectory() as tmp:
            pieces = [("snow", 2), ("video", 3), ("snow", 4), ("video", 3), ("snow", 2)]
            for name, vcodec, acodec in (("selftest.mp4", None, None),
                                         ("selftest.avi", MJPEG, ("-c:a", "pcm_s16le"))):
                src = os.path.join(tmp, name)
                kwargs = {"border": 10}
                if vcodec:
                    kwargs.update(vcodec=vcodec, acodec=acodec)
                truth = make_test_video(src, pieces, **kwargs)
                info = probe(src)
                result = analyze(src, info)
                segments = find_segments(result, DetectionSettings(pad_before=0, pad_after=0))
                found = [(round(s.start, 2), round(s.end, 2)) for s in segments]
                lines.append(f"{name}: truth={truth} found={found}")
                _check(len(segments) == len(truth), "wrong number of fragments")
                for seg, (a, b) in zip(segments, truth):
                    _check(abs(seg.start - a) < 0.5 and abs(seg.end - b) < 0.5, "fragment boundaries are off")
                for mode in ("auto", MODE_REENCODE):
                    out_dir = os.path.join(tmp, mode)
                    outputs = export(info, segments, ExportSettings(mode=mode, output_dir=out_dir))
                    duration = probe(outputs[0]).duration
                    lines.append(f"  export {mode}: {os.path.basename(outputs[0])} {duration:.2f}s")
                    _check(abs(duration - kept_duration(segments)) < 0.3, "exported duration is off")
        lines.append("OK")
        code = 0
    except Exception:
        lines.append("FAILED")
        lines.append(traceback.format_exc())
    text = "\n".join(lines) + "\n"
    if log_path:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    elif sys.stdout is not None:
        sys.stdout.write(text)
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
