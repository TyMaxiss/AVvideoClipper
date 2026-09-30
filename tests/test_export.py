import os

import pytest

from avclipper.analysis import analyze
from avclipper.export import (
    LAYOUT_SEPARATE, MODE_COPY, MODE_REENCODE, ExportSettings, export, plan_export, uses_copy,
)
from avclipper.media import probe
from avclipper.segments import DetectionSettings, Segment, find_segments, kept_duration


def check_clean(path, expected_duration):
    info = probe(path)
    assert info.duration == pytest.approx(expected_duration, abs=0.15)
    result = analyze(path, info)
    segments = find_segments(result, DetectionSettings(pad_before=0, pad_after=0, merge_gap=1.0))
    # the whole output is picture, except the 0.3 s safety margins at the cuts
    assert len(segments) == 1
    assert segments[0].duration > expected_duration - 1.5
    return info


def test_reencode_joined(basic_video, tmp_path):
    path, _ = basic_video
    info = probe(str(path))
    segments = find_segments(analyze(str(path), info), DetectionSettings())
    settings = ExportSettings(mode=MODE_REENCODE, output_dir=str(tmp_path))
    progress = []
    outputs = export(info, segments, settings, progress=progress.append)
    assert outputs == [str(tmp_path / "basic_clean.mp4")]
    assert progress and progress[-1] == pytest.approx(1.0)
    out = check_clean(outputs[0], kept_duration(segments))
    assert out.has_audio and out.video_codec == "h264"
    # nothing left behind besides the result
    assert sorted(os.listdir(tmp_path)) == ["basic_clean.mp4"]


def test_separate_files_and_disabled_segment(basic_video, tmp_path):
    path, _ = basic_video
    info = probe(str(path))
    segments = find_segments(analyze(str(path), info), DetectionSettings())
    assert len(segments) == 2
    settings = ExportSettings(mode=MODE_REENCODE, layout=LAYOUT_SEPARATE, output_dir=str(tmp_path))
    outputs = export(info, segments, settings)
    assert [os.path.basename(p) for p in outputs] == ["basic_clean_01.mp4", "basic_clean_02.mp4"]
    for out, seg in zip(outputs, segments):
        assert probe(out).duration == pytest.approx(seg.duration, abs=0.15)

    segments[1].enabled = False
    outputs = export(info, segments, ExportSettings(mode=MODE_REENCODE, output_dir=str(tmp_path)))
    assert os.path.basename(outputs[0]) == "basic_clean.mp4"
    assert probe(outputs[0]).duration == pytest.approx(segments[0].duration, abs=0.15)


def test_existing_files_are_not_overwritten(basic_video, tmp_path):
    path, _ = basic_video
    info = probe(str(path))
    (tmp_path / "basic_clean.mp4").write_bytes(b"keep me")
    jobs = plan_export(info, [Segment(1, 2)], ExportSettings(output_dir=str(tmp_path)))
    assert os.path.basename(jobs[0].output) == "basic_clean (2).mp4"


def test_auto_mode_copies_mjpeg(mjpeg_video, tmp_path):
    path, _ = mjpeg_video
    info = probe(str(path))
    settings = ExportSettings(output_dir=str(tmp_path))
    assert uses_copy(info, settings)
    segments = find_segments(analyze(str(path), info), DetectionSettings())
    outputs = export(info, segments, settings)
    assert outputs[0].endswith(".avi")
    out = check_clean(outputs[0], kept_duration(segments))
    assert out.video_codec == "mjpeg"


def test_copy_mode_single_segment(mjpeg_video, tmp_path):
    path, truth = mjpeg_video
    info = probe(str(path))
    seg = Segment(truth[0][0], truth[0][1])
    outputs = export(info, [seg], ExportSettings(mode=MODE_COPY, output_dir=str(tmp_path)))
    check_clean(outputs[0], seg.duration)


def test_many_segments_are_grouped(basic_video, tmp_path, monkeypatch):
    """More fragments than one ffmpeg call takes: encoded in groups, then joined."""
    import avclipper.export as ex

    monkeypatch.setattr(ex, "_GROUP_SIZE", 2)
    path, (first, second) = basic_video
    info = probe(str(path))
    segments = [Segment(first[0] + 0.5, first[0] + 1.5), Segment(first[0] + 2.0, first[0] + 3.0),
                Segment(second[0] + 0.5, second[1] - 0.5)]
    outputs = export(info, segments, ExportSettings(mode=MODE_REENCODE, output_dir=str(tmp_path)))
    out = probe(outputs[0])
    assert out.duration == pytest.approx(kept_duration(segments), abs=0.2)
    assert out.has_audio
    assert sorted(os.listdir(tmp_path)) == ["basic_clean.mp4"]


def test_nothing_to_export(basic_video, tmp_path):
    path, _ = basic_video
    info = probe(str(path))
    assert export(info, [], ExportSettings(output_dir=str(tmp_path))) == []
    assert export(info, [Segment(1, 3, enabled=False)], ExportSettings(output_dir=str(tmp_path))) == []
