"""Turning per-frame scores into the list of fragments to keep."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import List

import numpy as np

from .analysis import AnalysisResult


@dataclass
class DetectionSettings:
    threshold: float = 0.45       # minimal signal score of a "picture" frame (0..1)
    min_segment: float = 2.0      # drop picture fragments shorter than this, s
    merge_gap: float = 2.0        # keep noise gaps shorter than this inside the video, s
    pad_before: float = 0.3       # extra time kept before each fragment, s
    pad_after: float = 0.3        # extra time kept after each fragment, s
    blank_is_noise: bool = True   # cut uniform black/blue "no signal" screens as well

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DetectionSettings":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class Segment:
    start: float
    end: float
    enabled: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def picture_frames(result: AnalysisResult, settings: DetectionSettings) -> np.ndarray:
    """Boolean per analysed frame: True where there is a real picture."""
    picture = result.score >= settings.threshold
    if settings.blank_is_noise:
        picture &= ~result.blank
    else:
        picture |= result.blank
    return picture


def _runs(mask: np.ndarray):
    """(start, end) index pairs of consecutive True values, end exclusive."""
    if len(mask) == 0:
        return []
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    edges = np.diff(padded)
    starts = np.nonzero(edges == 1)[0]
    ends = np.nonzero(edges == -1)[0]
    return list(zip(starts.tolist(), ends.tolist()))


def find_segments(result: AnalysisResult, settings: DetectionSettings) -> List[Segment]:
    """Fragments of real video to keep, in seconds, sorted and non-overlapping."""
    rate = result.rate
    total = result.duration if result.duration > 0 else result.frame_count / rate
    spans = [[a / rate, b / rate] for a, b in _runs(picture_frames(result, settings))]

    merged: List[List[float]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= settings.merge_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    out: List[Segment] = []
    for start, end in merged:
        if end - start < settings.min_segment:
            continue
        start = max(0.0, start - settings.pad_before)
        end = min(total, end + settings.pad_after)
        if out and start <= out[-1].end:
            out[-1].end = max(out[-1].end, end)
        else:
            out.append(Segment(start, end))
    return out


def kept_duration(segments: List[Segment]) -> float:
    return sum(s.duration for s in segments if s.enabled)
