"""Cutting the kept fragments out of the source file with ffmpeg."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from .media import MediaInfo, available_encoders, run_with_progress
from .segments import Segment

MODE_AUTO, MODE_REENCODE, MODE_COPY = "auto", "reencode", "copy"
LAYOUT_JOINED, LAYOUT_SEPARATE = "joined", "separate"

# Containers ffmpeg can write that we keep as-is in stream-copy mode.
_COPY_CONTAINERS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts",
    ".mts", ".flv", ".3gp", ".wmv", ".asf", ".dv", ".vob", ".ogv",
}
# Maximum inputs per ffmpeg call when re-encoding (keeps the command line short).
_GROUP_SIZE = 20


@dataclass
class ExportSettings:
    mode: str = MODE_AUTO          # auto | reencode | copy
    layout: str = LAYOUT_JOINED    # joined | separate
    output_dir: str = ""           # empty: next to the source file
    suffix: str = "_clean"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExportSettings":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class ExportJob:
    output: str
    segments: List[Segment]
    copy: bool

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


def uses_copy(info: MediaInfo, settings: ExportSettings) -> bool:
    if settings.mode == MODE_COPY:
        return True
    if settings.mode == MODE_REENCODE:
        return False
    return info.intra_only


def _unique(path: Path, taken: set) -> Path:
    candidate, n = path, 2
    while candidate.exists() or str(candidate).lower() in taken:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    taken.add(str(candidate).lower())
    return candidate


def plan_export(info: MediaInfo, segments: Sequence[Segment],
                settings: ExportSettings) -> List[ExportJob]:
    """Decide output files for the enabled segments."""
    segs = [s for s in segments if s.enabled and s.duration > 0.01]
    if not segs:
        return []
    src = Path(info.path)
    copy = uses_copy(info, settings)
    ext = src.suffix.lower() if copy else ".mp4"
    if copy and ext not in _COPY_CONTAINERS:
        ext = ".mkv"
    out_dir = Path(settings.output_dir) if settings.output_dir else src.parent
    suffix = settings.suffix or "_clean"
    taken = {str(src).lower()}
    if settings.layout == LAYOUT_SEPARATE and len(segs) > 1:
        return [ExportJob(str(_unique(out_dir / f"{src.stem}{suffix}_{i:02d}{ext}", taken)), [s], copy)
                for i, s in enumerate(segs, 1)]
    return [ExportJob(str(_unique(out_dir / f"{src.stem}{suffix}{ext}", taken)), segs, copy)]


def _video_codec_args(settings: ExportSettings) -> List[str]:
    enc = available_encoders()
    if "libx264" in enc:
        return ["-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
                "-pix_fmt", "yuv420p"]
    if "libopenh264" in enc:
        return ["-c:v", "libopenh264", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"]


def _ts(seconds: float) -> str:
    return f"{max(0.0, seconds):.3f}"


def reencode_args(info: MediaInfo, segments: Sequence[Segment], output: str,
                  settings: ExportSettings, pcm_audio: bool = False) -> List[str]:
    """One ffmpeg call: every segment is a separately seeked input, joined by concat."""
    args: List[str] = []
    for seg in segments:
        args += ["-ss", _ts(seg.start), "-t", _ts(seg.duration), "-i", info.path]
    audio = info.has_audio
    labels = "".join(f"[{i}:{info.video_index}]" + (f"[{i}:{info.audio_index}]" if audio else "")
                     for i in range(len(segments)))
    graph = f"{labels}concat=n={len(segments)}:v=1:a={int(audio)}[v]" + ("[a]" if audio else "")
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "[a]"]
    args += _video_codec_args(settings)
    if audio:
        args += ["-c:a", "pcm_s16le"] if pcm_audio else ["-c:a", "aac", "-b:a", settings.audio_bitrate]
    if output.lower().endswith((".mp4", ".mov", ".m4v")):
        args += ["-movflags", "+faststart"]
    return args + ["-y", output]


def _concat_line(path: str) -> str:
    return "file '" + Path(path).resolve().as_posix().replace("'", "'\\''") + "'\n"


def copy_args(info: MediaInfo, segments: Sequence[Segment], output: str,
              list_path: str) -> List[str]:
    """Lossless cut without re-encoding (precise only on keyframes)."""
    if len(segments) == 1:
        seg = segments[0]
        args = ["-ss", _ts(seg.start), "-i", info.path, "-t", _ts(seg.duration)]
    else:
        # The concat demuxer takes raw file timestamps, while our times (like -ss)
        # count from the start of the file.
        offset = info.start_time
        with open(list_path, "w", encoding="utf-8") as fh:
            fh.write("ffconcat version 1.0\n")
            for seg in segments:
                fh.write(_concat_line(info.path))
                fh.write(f"inpoint {seg.start + offset:.3f}\noutpoint {seg.end + offset:.3f}\n")
        args = ["-f", "concat", "-safe", "0", "-i", list_path]
    args += ["-map", f"0:{info.video_index}"]
    if info.has_audio:
        args += ["-map", "0:a?"]
    args += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    return args + ["-y", output]


class _Progress:
    """Maps progress of individual ffmpeg runs onto the whole export."""

    def __init__(self, total: float, callback: Optional[Callable[[float], None]]):
        self.total = max(total, 1e-6)
        self.done = 0.0
        self.callback = callback

    def part(self, weight: float) -> Callable[[float], None]:
        base = self.done

        def report(fraction: float) -> None:
            if self.callback:
                self.callback(min((base + weight * fraction) / self.total, 1.0))
        return report

    def advance(self, weight: float) -> None:
        self.done += weight
        if self.callback:
            self.callback(min(self.done / self.total, 1.0))


def export(info: MediaInfo, segments: Sequence[Segment], settings: ExportSettings,
           progress: Optional[Callable[[float], None]] = None,
           cancel: Optional[threading.Event] = None) -> List[str]:
    """Write the enabled segments; returns the created files."""
    jobs = plan_export(info, segments, settings)
    if not jobs:
        return []
    Path(jobs[0].output).parent.mkdir(parents=True, exist_ok=True)
    total = sum(job.duration for job in jobs)
    tracker = _Progress(total * 1.02, progress)
    work_dir = tempfile.mkdtemp(prefix=".avclipper_", dir=str(Path(jobs[0].output).parent))
    created: List[str] = []
    try:
        for job in jobs:
            try:
                _run_job(info, job, settings, work_dir, tracker, cancel)
            except BaseException:
                if os.path.exists(job.output):
                    os.remove(job.output)
                raise
            created.append(job.output)
        tracker.advance(total * 0.02)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return created


def _run_job(info: MediaInfo, job: ExportJob, settings: ExportSettings, work_dir: str,
             tracker: _Progress, cancel: Optional[threading.Event]) -> None:
    if job.copy:
        list_path = os.path.join(work_dir, "list.ffconcat")
        run_with_progress(copy_args(info, job.segments, job.output, list_path),
                          job.duration, tracker.part(job.duration), cancel)
        tracker.advance(job.duration)
        return
    groups = [job.segments[i:i + _GROUP_SIZE] for i in range(0, len(job.segments), _GROUP_SIZE)]
    if len(groups) == 1:
        run_with_progress(reencode_args(info, job.segments, job.output, settings),
                          job.duration, tracker.part(job.duration), cancel)
        tracker.advance(job.duration)
        return
    # Many fragments: encode groups to temporary files, then join them losslessly.
    parts = []
    for n, group in enumerate(groups):
        part = os.path.join(work_dir, f"part{n:03d}.mkv")
        weight = sum(s.duration for s in group)
        run_with_progress(reencode_args(info, group, part, settings, pcm_audio=True),
                          weight, tracker.part(weight), cancel)
        tracker.advance(weight)
        parts.append(part)
    list_path = os.path.join(work_dir, "parts.ffconcat")
    with open(list_path, "w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n")
        for part in parts:
            fh.write(_concat_line(part))
    args = ["-f", "concat", "-safe", "0", "-i", list_path, "-map", "0:v"]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "aac", "-b:a", settings.audio_bitrate]
    args += ["-c:v", "copy", "-movflags", "+faststart", "-y", job.output]
    run_with_progress(args, job.duration, None, cancel)
