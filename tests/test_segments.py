import numpy as np

from avclipper.analysis import AnalysisResult
from avclipper.segments import DetectionSettings, Segment, find_segments, kept_duration


def result_from(pattern, rate=10.0, blank=None):
    """pattern: list of (score, seconds)."""
    scores = np.concatenate([np.full(int(round(d * rate)), s, np.float32) for s, d in pattern])
    blank_arr = np.zeros(len(scores), bool) if blank is None else blank
    return AnalysisResult(path="x", rate=rate, score=scores, raw_score=scores, blank=blank_arr,
                          duration=len(scores) / rate)


def spans(segments):
    return [(round(s.start, 2), round(s.end, 2)) for s in segments]


NO_PAD = dict(pad_before=0.0, pad_after=0.0)


def test_basic_fragments():
    r = result_from([(0.05, 5), (0.9, 10), (0.05, 20), (0.9, 5), (0.05, 3)])
    assert spans(find_segments(r, DetectionSettings(**NO_PAD))) == [(5.0, 15.0), (35.0, 40.0)]


def test_short_gap_is_bridged_and_long_gap_splits():
    r = result_from([(0.9, 5), (0.1, 1.5), (0.9, 5), (0.1, 3), (0.9, 5)])
    segs = find_segments(r, DetectionSettings(merge_gap=2.0, **NO_PAD))
    assert spans(segs) == [(0.0, 11.5), (14.5, 19.5)]


def test_short_flash_in_noise_is_dropped():
    r = result_from([(0.05, 10), (0.95, 0.5), (0.05, 10), (0.9, 6), (0.05, 4)])
    assert spans(find_segments(r, DetectionSettings(min_segment=2.0, **NO_PAD))) == [(20.5, 26.5)]


def test_padding_is_clamped_and_overlaps_merge():
    r = result_from([(0.9, 3), (0.1, 3), (0.9, 3)])
    segs = find_segments(r, DetectionSettings(merge_gap=0.5, pad_before=1.5, pad_after=1.5, min_segment=1))
    # padding extends both fragments across the 3 s gap: they touch and merge
    assert spans(segs) == [(0.0, 9.0)]
    segs = find_segments(r, DetectionSettings(merge_gap=0.5, pad_before=1.0, pad_after=1.0, min_segment=1))
    assert spans(segs) == [(0.0, 4.0), (5.0, 9.0)]


def test_threshold():
    r = result_from([(0.3, 5), (0.6, 5), (0.3, 5)])
    assert spans(find_segments(r, DetectionSettings(threshold=0.5, **NO_PAD))) == [(5.0, 10.0)]
    assert spans(find_segments(r, DetectionSettings(threshold=0.2, **NO_PAD))) == [(0.0, 15.0)]


def test_blank_frames():
    rate = 10.0
    blank = np.zeros(150, bool)
    blank[:30] = True
    r = result_from([(0.0, 3), (0.05, 4), (0.9, 5), (0.05, 3)], rate=rate, blank=blank)
    assert spans(find_segments(r, DetectionSettings(blank_is_noise=True, **NO_PAD))) == [(7.0, 12.0)]
    kept = find_segments(r, DetectionSettings(blank_is_noise=False, **NO_PAD))
    assert spans(kept) == [(0.0, 3.0), (7.0, 12.0)]


def test_nothing_found():
    r = result_from([(0.05, 30)])
    assert find_segments(r, DetectionSettings()) == []


def test_kept_duration_ignores_disabled():
    segs = [Segment(0, 5), Segment(10, 12, enabled=False), Segment(20, 21)]
    assert kept_duration(segs) == 6


def test_settings_roundtrip():
    s = DetectionSettings(threshold=0.3, min_segment=1.5, blank_is_noise=False)
    assert DetectionSettings.from_dict({**s.to_dict(), "unknown": 1}) == s
