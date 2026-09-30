import numpy as np
import pytest

from avclipper.analysis import COARSE_H, COARSE_W, analyze, choose_rate, static_mask
from avclipper.segments import DetectionSettings, find_segments

from .conftest import make_video

TOL = 0.5  # seconds


def assert_matches(segments, truth, pad):
    assert len(segments) == len(truth), [(s.start, s.end) for s in segments]
    for seg, (a, b) in zip(segments, truth):
        assert abs(seg.start - max(0.0, a - pad)) <= TOL, (seg, a)
        assert abs(seg.end - (b + pad)) <= TOL, (seg, b)


def test_choose_rate():
    assert choose_rate(25, 60) == 25
    assert choose_rate(59.94, 60) == pytest.approx(29.97)
    assert choose_rate(50, 60) == 25
    assert choose_rate(0, 60) == 25
    assert choose_rate(30, 100 * 3600) < 30  # very long recordings are sampled sparser


def test_basic_detection(basic_video):
    path, truth = basic_video
    result = analyze(str(path))
    assert result.duration == pytest.approx(18.0, abs=0.1)
    t = result.times()
    in_video = np.zeros(len(t), bool)
    in_noise = np.ones(len(t), bool)
    for a, b in truth:
        in_video |= (t > a + 0.5) & (t < b - 0.5)
        in_noise &= ~((t > a - 0.5) & (t < b + 0.5))
    assert result.score[in_video].min() > 0.7
    assert result.score[in_noise].max() < 0.3
    settings = DetectionSettings()
    assert_matches(find_segments(result, settings), truth, settings.pad_before)


def test_static_borders_and_duplicate_frames(media_dir):
    """Black side borders and frame doubling must not make snow look like video."""
    path = media_dir / "borders.mp4"
    truth = make_video(path, [("snow", 3), ("video", 4), ("snow", 5), ("video", 3), ("snow", 2)],
                       border=12, double_fps=True, audio=False)
    result = analyze(str(path))
    assert result.static_share > 0
    settings = DetectionSettings()
    assert_matches(find_segments(result, settings), truth, settings.pad_before)


def test_only_noise(media_dir):
    path = media_dir / "noise.mp4"
    make_video(path, [("snow", 6)], audio=False)
    result = analyze(str(path))
    assert find_segments(result, DetectionSettings()) == []


def test_only_video(media_dir):
    path = media_dir / "clean.mp4"
    make_video(path, [("video", 6)])
    result = analyze(str(path))
    segments = find_segments(result, DetectionSettings())
    assert len(segments) == 1
    assert segments[0].start == pytest.approx(0.0, abs=0.1)
    assert segments[0].end == pytest.approx(6.0, abs=0.2)


def test_blank_screen(media_dir):
    path = media_dir / "blank.mp4"
    truth = make_video(path, [("black", 3), ("snow", 2), ("video", 4), ("snow", 2)])
    result = analyze(str(path))
    assert result.blank[: int(2.5 * result.rate)].all()
    cut = find_segments(result, DetectionSettings(pad_before=0, pad_after=0))
    assert_matches(cut, truth, 0.0)
    kept = find_segments(result, DetectionSettings(pad_before=0, pad_after=0, blank_is_noise=False))
    assert kept[0].start == pytest.approx(0.0, abs=0.1)


def test_static_mask_ignores_constant_columns():
    rng = np.random.default_rng(0)
    activity = rng.uniform(15, 25, (COARSE_H * 4, COARSE_W * 4))
    activity[:, :3] = 0.2       # black border on the left
    activity[:, -2:] = 0.3      # and on the right
    mask = static_mask(activity)
    assert mask[:, 0].all() and mask[:, -1].all()
    assert not mask[:, 2:-2].any()


def test_cancel(basic_video):
    import threading

    from avclipper.media import Cancelled

    path, _ = basic_video
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        analyze(str(path), cancel=cancel)
