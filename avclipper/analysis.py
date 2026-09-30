"""Frame-by-frame detection of analog "snow" (white noise) in a recording.

How it works
------------
ffmpeg decodes the video, downsamples every frame to a tiny 160x120 grayscale
image and pipes it here.  The key signal is the *temporal correlation* of
consecutive frames at a coarse 40x30 scale:

* snow is random, so two consecutive snow frames are uncorrelated (~0);
* a real picture changes little between frames (~0.7..1.0), even with fast
  camera motion, heavy compression, a dark scene or a partly broken-up signal.

Things that are identical in every frame (black borders of the capture card,
pillarbox bars, receiver/DVR OSD text) would make snow frames look correlated,
so pixels that never change while the image is changing are detected and
excluded.  Exact duplicate frames (frame-rate conversion, e.g. 25 fps recorded
in a 30/50/60 fps file) carry no information and are skipped.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from .media import FFmpegError, FFmpegProcess, MediaInfo, crash_message, is_crash, probe

FINE_W, FINE_H = 160, 120
BLOCK = 4
COARSE_W, COARSE_H = FINE_W // BLOCK, FINE_H // BLOCK
FRAME_BYTES = FINE_W * FINE_H
CHUNK_FRAMES = 128
# Upper bound on analysed frames (memory: 1.2 kB per frame).
MAX_ANALYSIS_FRAMES = 200_000

# A pixel "changes" between frames if it moves by more than this (0..255 scale).
_CHANGE_LEVEL = 8
# Frames where at least this share of pixels changes are used to find static pixels.
_BUSY_FRAME_SHARE = 0.2
# Mean absolute coarse difference below which a frame counts as a duplicate.
_DUPLICATE_DIFF = 0.35
# Coarse variance below which a frame is treated as flat (no structure at all).
_FLAT_VAR = 1.0
# Blank frame: this share of the active area lies within +-_BLANK_LEVEL of the median.
_BLANK_SHARE = 0.9
_BLANK_LEVEL = 4


@dataclass
class AnalysisResult:
    path: str
    rate: float                 # analysed frames per second
    score: np.ndarray           # smoothed signal score per frame: 0 = snow, 1 = picture
    raw_score: np.ndarray       # the same before temporal smoothing
    blank: np.ndarray           # uniform frame (black / blue "no signal" screen)
    duration: float             # media duration in seconds
    static_share: float = 0.0   # share of the frame ignored as static borders / OSD
    info: Optional[MediaInfo] = field(default=None, repr=False)

    @property
    def frame_count(self) -> int:
        return len(self.score)

    def times(self) -> np.ndarray:
        return np.arange(self.frame_count, dtype=np.float64) / self.rate

    def score_at(self, t: float) -> float:
        if self.frame_count == 0:
            return 0.0
        i = int(min(max(t * self.rate, 0), self.frame_count - 1))
        return float(self.score[i])


def choose_rate(fps: float, duration: float) -> float:
    """Analysis frame rate: the native rate, halved for 50/60 fps sources."""
    if not fps or fps <= 0 or fps > 480:
        fps = 25.0
    rate = fps
    while rate > 31:
        rate /= 2
    if duration > 0 and duration * rate > MAX_ANALYSIS_FRAMES:
        rate = max(MAX_ANALYSIS_FRAMES / duration, 2.0)
    return rate


class _Accumulator:
    """Collects per-frame data while frames are streamed from ffmpeg."""

    def __init__(self):
        self.coarse: List[np.ndarray] = []
        self.prev: Optional[np.ndarray] = None
        self.busy_sum = np.zeros((FINE_H, FINE_W), np.float64)
        self.busy_frames = 0
        self.all_sum = np.zeros((FINE_H, FINE_W), np.float64)
        self.all_frames = 0
        self.count = 0

    def add(self, fine: np.ndarray) -> None:
        n = len(fine)
        cur = fine.astype(np.int16)
        seq = cur if self.prev is None else np.concatenate([self.prev, cur])
        if len(seq) > 1:
            diff = np.abs(seq[1:] - seq[:-1])
            self.all_sum += diff.sum(axis=0)
            self.all_frames += len(diff)
            busy = (diff > _CHANGE_LEVEL).mean(axis=(1, 2)) >= _BUSY_FRAME_SHARE
            if busy.any():
                self.busy_sum += diff[busy].sum(axis=0)
                self.busy_frames += int(busy.sum())
        self.prev = cur[-1:]
        coarse = fine.reshape(n, COARSE_H, BLOCK, COARSE_W, BLOCK).mean(axis=(2, 4), dtype=np.float32)
        self.coarse.append(np.rint(coarse).astype(np.uint8))
        self.count += n

    def activity(self) -> np.ndarray:
        """Mean absolute frame-to-frame change per pixel."""
        if self.busy_frames >= 10:
            return self.busy_sum / self.busy_frames
        return self.all_sum / max(self.all_frames, 1)


def static_mask(activity: np.ndarray) -> np.ndarray:
    """Coarse pixels that stay constant while the rest of the frame changes.

    `activity` is the per-pixel mean absolute change at the fine resolution.
    Returns a (COARSE_H, COARSE_W) bool array, True = ignore this pixel.
    """
    ref = float(np.percentile(activity, 90))
    if ref < 1.0:
        return np.zeros((COARSE_H, COARSE_W), bool)
    fine_static = activity < 0.5 * ref
    counts = fine_static.reshape(COARSE_H, BLOCK, COARSE_W, BLOCK).sum(axis=(1, 3))
    mask = counts >= 2
    if (~mask).sum() < 0.25 * mask.size:
        mask[:] = False
    return mask


def _ffill_limited(x: np.ndarray, limit: int) -> np.ndarray:
    out = x.copy()
    n = len(x)
    idx = np.where(np.isnan(x), -1, np.arange(n))
    last = np.maximum.accumulate(idx)
    fill = np.isnan(out) & (last >= 0) & (np.arange(n) - last <= limit)
    out[fill] = x[last[fill]]
    return out


def median_smooth(x: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window)) | 1
    if window <= 1 or len(x) == 0:
        return x.copy()
    pad = window // 2
    padded = np.pad(x, pad, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.median(view, axis=1).astype(x.dtype)


def compute_scores(coarse: np.ndarray, mask: np.ndarray, rate: float):
    """Signal score and blank flags for stored coarse frames.

    coarse: (N, COARSE_H, COARSE_W) uint8, mask: static pixels to ignore.
    Returns (raw_score, score, blank).
    """
    n = len(coarse)
    keep = ~mask
    tc = np.full(n, np.nan, np.float32)
    spatial = np.zeros(n, np.float32)
    blank = np.zeros(n, bool)
    keep_f = keep.astype(np.float32)
    kept = float(keep_f.sum())
    step = 4096
    for s in range(0, n, step):
        e = min(n, s + step)
        lo = max(0, s - 1)
        frames = coarse[lo:e].astype(np.float32)          # (m, H, W)
        x = frames[:, keep]                                # (m, P)
        xc = x - x.mean(axis=1, keepdims=True)
        var = (xc * xc).mean(axis=1)
        # Correlation of each frame with the previous one (frame `lo` has no pair).
        num = (xc[1:] * xc[:-1]).mean(axis=1)
        den = np.sqrt(var[1:] * var[:-1])
        diff = np.abs(x[1:] - x[:-1]).mean(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nan)
        corr[(diff < _DUPLICATE_DIFF) | (var[1:] < _FLAT_VAR)] = np.nan
        tc[lo + 1:e] = corr

        # Spatial structure (fallback for long frozen stretches).
        own = frames[1:] if lo < s else frames
        own_mean = (own * keep_f).sum(axis=(1, 2)) / kept
        z = (own - own_mean[:, None, None]) * keep_f
        zvar = (z * z).mean(axis=(1, 2))
        sh = (z[:, :, 1:] * z[:, :, :-1]).mean(axis=(1, 2))
        sv = (z[:, 1:, :] * z[:, :-1, :]).mean(axis=(1, 2))
        with np.errstate(invalid="ignore", divide="ignore"):
            spatial[s:e] = np.where(zvar > _FLAT_VAR, (sh + sv) / 2 / np.maximum(zvar, 1e-6), 0.0)

        # Blank frames.
        active = coarse[s:e][:, keep]
        med = np.median(active, axis=1)
        near = np.abs(active.astype(np.int16) - med[:, None]) <= _BLANK_LEVEL
        blank[s:e] = near.mean(axis=1) >= _BLANK_SHARE

    filled = _ffill_limited(tc, max(1, int(round(rate))))
    rest = np.isnan(filled)
    filled[rest] = spatial[rest]
    raw = np.clip(filled, 0.0, 1.0).astype(np.float32)
    score = median_smooth(raw, int(round(rate * 0.4)))
    return raw, score, blank


def analyze(path: str, info: Optional[MediaInfo] = None,
            progress: Optional[Callable[[float], None]] = None,
            cancel: Optional[threading.Event] = None) -> AnalysisResult:
    """Decode `path` and compute a per-frame signal score."""
    if info is None:
        info = probe(path)
    rate = choose_rate(info.fps, info.duration)
    vf = (f"fps={rate:.6f}:start_time=0,"
          f"scale={FINE_W}:{FINE_H}:flags=area,format=gray")
    args = ["-loglevel", "error", "-i", path, "-map", f"0:{info.video_index}",
            "-an", "-sn", "-dn", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    expected = max(1, int(math.ceil(info.duration * rate))) if info.duration > 0 else 0
    acc = _Accumulator()
    proc = FFmpegProcess(args, cancel=cancel)
    try:
        while True:
            buf = proc.stdout.read(CHUNK_FRAMES * FRAME_BYTES)
            proc.check_cancelled()
            n = len(buf) // FRAME_BYTES
            if n:
                acc.add(np.frombuffer(buf[: n * FRAME_BYTES], np.uint8).reshape(n, FINE_H, FINE_W))
                if progress and expected:
                    progress(min(acc.count / expected, 1.0) * 0.97)
            if len(buf) < CHUNK_FRAMES * FRAME_BYTES:
                break
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    proc.check_cancelled()
    if is_crash(code):
        raise FFmpegError(crash_message(code))
    if acc.count == 0:
        raise FFmpegError(proc.stderr.text() or f"Не удалось декодировать видео (код {code}).")

    coarse = np.concatenate(acc.coarse)
    acc.coarse.clear()
    mask = static_mask(acc.activity())
    raw, score, blank = compute_scores(coarse, mask, rate)
    duration = info.duration if info.duration > 0 else len(score) / rate
    if progress:
        progress(1.0)
    return AnalysisResult(
        path=path, rate=rate, score=score, raw_score=raw, blank=blank,
        duration=duration, static_share=float(mask.mean()), info=info,
    )
