"""ffmpeg discovery, media probing and subprocess helpers."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence


class FFmpegNotFound(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised when a long operation was cancelled by the user."""


# Codecs where every frame is a keyframe: stream copy cuts them frame-accurately.
INTRA_ONLY_CODECS = frozenset({
    "mjpeg", "mjpegb", "ljpeg", "jpeg2000", "rawvideo", "dvvideo", "huffyuv", "ffvhuff",
    "ffv1", "utvideo", "magicyuv", "prores", "dnxhd", "v210", "v308", "v408", "v410",
    "r210", "r10k", "y41p", "yuv4", "ayuv", "lagarith", "cllc", "png", "cfhd", "hqx",
    "hq_hqa", "zlib", "mszh",
})

_ffmpeg_path: Optional[str] = None
_encoders: Optional[set] = None


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def _candidate_paths() -> Iterable[str]:
    env = os.environ.get("AVCLIPPER_FFMPEG")
    if env:
        yield env
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    roots.append(Path(__file__).resolve().parent.parent)
    for root in roots:
        for sub in ("", "ffmpeg", "bin", os.path.join("ffmpeg", "bin")):
            yield str(root / sub / _exe("ffmpeg"))
    # A system ffmpeg goes before the imageio-ffmpeg binary: the static Linux build
    # shipped by imageio-ffmpeg crashes on MPEG-TS files.
    found = shutil.which("ffmpeg")
    if found:
        yield found
    try:
        import imageio_ffmpeg

        yield imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass


def find_ffmpeg() -> str:
    """Return a path to a working ffmpeg executable."""
    global _ffmpeg_path
    if _ffmpeg_path and os.path.isfile(_ffmpeg_path):
        return _ffmpeg_path
    for path in _candidate_paths():
        if path and os.path.isfile(path):
            _ffmpeg_path = path
            return path
    raise FFmpegNotFound(
        "Не найден ffmpeg. Установите пакет imageio-ffmpeg (pip install imageio-ffmpeg), "
        "положите ffmpeg рядом с программой или укажите путь в переменной AVCLIPPER_FFMPEG."
    )


def is_crash(code: int) -> bool:
    """Killed by a signal (POSIX) or an exception status such as 0xC0000005 (Windows)."""
    return code < 0 or code > 255


def crash_message(code: int) -> str:
    return (f"ffmpeg аварийно завершился (код {code}). Попробуйте другую сборку ffmpeg: "
            f"укажите путь к ней в переменной окружения AVCLIPPER_FFMPEG.")


def popen_flags() -> dict:
    """Extra Popen kwargs: never flash a console window on Windows."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def available_encoders() -> set:
    global _encoders
    if _encoders is None:
        try:
            out = subprocess.run(
                [find_ffmpeg(), "-hide_banner", "-nostdin", "-encoders"],
                capture_output=True, timeout=30, **popen_flags(),
            ).stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            out = ""
        names = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
                names.add(parts[1])
        _encoders = names
    return _encoders


@dataclass
class MediaInfo:
    path: str
    duration: float            # seconds, 0.0 if unknown
    start_time: float
    width: int
    height: int
    fps: float                 # 0.0 if unknown
    video_codec: str
    video_index: int           # absolute stream index of the video stream
    audio_codec: str = ""
    audio_index: int = -1      # absolute stream index of the first audio stream, -1 if none
    sar: float = 1.0           # sample (pixel) aspect ratio

    @property
    def has_audio(self) -> bool:
        return self.audio_index >= 0

    @property
    def display_aspect(self) -> float:
        if self.height <= 0:
            return 4 / 3
        return self.width * self.sar / self.height

    @property
    def intra_only(self) -> bool:
        return self.video_codec in INTRA_ONLY_CODECS


_RE_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_RE_START = re.compile(r"start:\s*(-?\d+(?:\.\d+)?)")
_RE_STREAM = re.compile(r"Stream #(\d+):(\d+)[^:]*:\s*(Video|Audio|Subtitle|Data|Attachment):\s*(.*)")
_RE_SIZE = re.compile(r"(?:^|[\s,])(\d{2,5})x(\d{2,5})(?=[\s,\[]|$)")
_RE_SAR = re.compile(r"SAR (\d+):(\d+)")
_RE_FPS = re.compile(r"([\d.]+)(k?) fps")
_RE_TBR = re.compile(r"([\d.]+)(k?) tbr")


def _rate(match) -> float:
    value = float(match.group(1))
    return value * 1000 if match.group(2) == "k" else value


def parse_probe_output(path: str, text: str) -> MediaInfo:
    """Parse the stream summary that `ffmpeg -i <file>` prints to stderr."""
    duration = 0.0
    m = _RE_DURATION.search(text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    start = 0.0
    m = _RE_START.search(text)
    if m:
        start = float(m.group(1))

    video = None
    audio = None
    for line in text.splitlines():
        m = _RE_STREAM.search(line)
        if not m:
            continue
        index = int(m.group(2))
        kind, rest = m.group(3), m.group(4)
        if kind == "Video" and video is None and "attached pic" not in rest:
            video = (index, rest)
        elif kind == "Audio" and audio is None:
            audio = (index, rest)

    if video is None:
        raise FFmpegError("В файле нет видеодорожки.")

    vindex, vrest = video
    codec = re.split(r"[\s,(]", vrest.strip(), maxsplit=1)[0]
    width = height = 0
    m = _RE_SIZE.search(vrest)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    sar = 1.0
    m = _RE_SAR.search(vrest)
    if m and int(m.group(2)) > 0 and int(m.group(1)) > 0:
        sar = int(m.group(1)) / int(m.group(2))
    fps = 0.0
    m = _RE_FPS.search(vrest) or _RE_TBR.search(vrest)
    if m:
        fps = _rate(m)

    info = MediaInfo(
        path=path, duration=duration, start_time=start, width=width, height=height,
        fps=fps, video_codec=codec, video_index=vindex, sar=sar,
    )
    if audio is not None:
        info.audio_index = audio[0]
        info.audio_codec = re.split(r"[\s,(]", audio[1].strip(), maxsplit=1)[0]
    return info


def probe(path: str) -> MediaInfo:
    """Read basic stream parameters of a media file."""
    if not os.path.isfile(path):
        raise FFmpegError(f"Файл не найден: {path}")
    proc = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", path],
        capture_output=True, timeout=120, **popen_flags(),
    )
    text = proc.stderr.decode("utf-8", "replace")
    if is_crash(proc.returncode):
        raise FFmpegError(crash_message(proc.returncode))
    if "Stream #" not in text:
        tail = "\n".join(text.strip().splitlines()[-3:])
        raise FFmpegError(f"Не удалось прочитать файл как видео.\n{tail}")
    return parse_probe_output(path, text)


class _StderrCollector(threading.Thread):
    """Drains a pipe so the child never blocks on a full stderr buffer."""

    def __init__(self, stream, keep: int = 40):
        super().__init__(daemon=True)
        self.stream = stream
        self.lines: deque = deque(maxlen=keep)

    def run(self) -> None:
        for raw in iter(self.stream.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.lines.append(line)

    def text(self) -> str:
        return "\n".join(self.lines)


class FFmpegProcess:
    """ffmpeg child process with stderr draining and cooperative cancellation."""

    def __init__(self, args: Sequence[str], cancel: Optional[threading.Event] = None,
                 stdout=subprocess.PIPE):
        self.cmd = [find_ffmpeg(), "-hide_banner", "-nostdin"] + list(args)
        self.cancel = cancel
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE,
            **popen_flags(),
        )
        self.stderr = _StderrCollector(self.proc.stderr)
        self.stderr.start()
        self._killed = False
        self._watch = threading.Thread(target=self._watch_cancel, daemon=True)
        self._watch.start()

    def _watch_cancel(self) -> None:
        if self.cancel is None:
            return
        while self.proc.poll() is None:
            if self.cancel.wait(0.2):
                self.kill()
                return

    @property
    def stdout(self):
        return self.proc.stdout

    def kill(self) -> None:
        if self.proc.poll() is None:
            self._killed = True
            try:
                self.proc.kill()
            except OSError:
                pass

    def wait(self) -> int:
        code = self.proc.wait()
        self.stderr.join(timeout=5)
        if self.proc.stdout:
            self.proc.stdout.close()
        return code

    def check_cancelled(self) -> None:
        if self._killed or (self.cancel is not None and self.cancel.is_set()):
            self.kill()
            raise Cancelled()


def run_with_progress(args: Sequence[str], duration: float,
                      on_progress: Optional[Callable[[float], None]] = None,
                      cancel: Optional[threading.Event] = None) -> None:
    """Run ffmpeg, reporting completion (0..1) from its -progress output."""
    proc = FFmpegProcess(["-progress", "pipe:1", "-nostats", "-loglevel", "error"] + list(args),
                         cancel=cancel)
    try:
        for raw in iter(proc.stdout.readline, b""):
            key, _, value = raw.decode("utf-8", "replace").strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and on_progress and duration > 0:
                try:
                    seconds = int(value) / 1e6
                except ValueError:
                    continue
                on_progress(min(max(seconds / duration, 0.0), 1.0))
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.check_cancelled()
    if is_crash(code):
        raise FFmpegError(crash_message(code))
    if code != 0:
        raise FFmpegError(proc.stderr.text() or f"ffmpeg завершился с кодом {code}")
