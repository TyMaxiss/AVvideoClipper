import pytest

from avclipper.media import FFmpegError, parse_probe_output
from avclipper.timefmt import format_time, parse_time


@pytest.mark.parametrize("seconds,text", [
    (0, "0:00.0"), (12.34, "0:12.3"), (59.96, "1:00.0"), (3725.5, "1:02:05.5"),
])
def test_format_time(seconds, text):
    assert format_time(seconds) == text


@pytest.mark.parametrize("text,seconds", [
    ("0:12.3", 12.3), ("1:02:05.5", 3725.5), ("75,5", 75.5), (" 83.4 ", 83.4), ("2:00", 120.0),
])
def test_parse_time(text, seconds):
    assert parse_time(text) == pytest.approx(seconds)


def test_parse_time_rejects_garbage():
    with pytest.raises(ValueError):
        parse_time("abc")


MP4 = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'fpv.mp4':
  Duration: 00:02:37.44, start: 0.000000, bitrate: 1266 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 720x576 [SAR 16:15 DAR 4:3], 1123 kb/s, 25 fps, 25 tbr, 12800 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, mono, fltp, 137 kb/s (default)
At least one output file must be specified
"""

TS = """Input #0, mpegts, from 'vhs.ts':
  Duration: 00:00:58.01, start: 8.889978, bitrate: 26453 kb/s
  Program 1
  Stream #0:0[0x100]: Video: mpeg2video (Main) ([2][0][0][0] / 0x0002), yuv420p(tv, progressive), 720x576 [SAR 1:1 DAR 5:4], 29.97 fps, 29.97 tbr, 90k tbn
  Stream #0:1[0x101]: Audio: mp2 ([3][0][0][0] / 0x0003), 48000 Hz, mono, fltp, 384 kb/s
"""

AVI_NO_AUDIO = """Input #0, avi, from 'dvr.avi':
  Duration: N/A, start: 0.000000, bitrate: N/A
  Stream #0:0: Video: mjpeg (Baseline) (MJPG / 0x47504A4D), yuvj420p(pc, bt470bg/unknown/unknown), 640x480, 30 tbr, 30 tbn
"""

COVER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':
  Duration: 00:00:10.00, start: 0.000000, bitrate: 500 kb/s
  Stream #0:0[0x1](und): Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s (default)
  Stream #0:1[0x0]: Video: mjpeg (Baseline), yuvj420p(pc), 600x600 [SAR 1:1 DAR 1:1], 90k tbr, 90k tbn (attached pic)
  Stream #0:2[0x2](und): Video: h264 (Main) (avc1 / 0x31637661), yuv420p, 1280x720, 1000 kb/s, 59.94 fps, 59.94 tbr, 60k tbn
"""


def test_probe_mp4():
    info = parse_probe_output("fpv.mp4", MP4)
    assert info.duration == pytest.approx(157.44)
    assert (info.width, info.height, info.fps) == (720, 576, 25.0)
    assert info.video_codec == "h264" and info.video_index == 0
    assert info.has_audio and info.audio_index == 1 and info.audio_codec == "aac"
    assert info.sar == pytest.approx(16 / 15)
    assert info.display_aspect == pytest.approx(4 / 3)
    assert not info.intra_only


def test_probe_ts_with_start_offset():
    info = parse_probe_output("vhs.ts", TS)
    assert info.start_time == pytest.approx(8.889978)
    assert info.fps == pytest.approx(29.97)
    assert info.video_codec == "mpeg2video"


def test_probe_avi_without_audio_and_duration():
    info = parse_probe_output("dvr.avi", AVI_NO_AUDIO)
    assert info.duration == 0.0
    assert info.fps == 30.0
    assert not info.has_audio
    assert info.intra_only


def test_probe_skips_cover_art():
    info = parse_probe_output("x.mp4", COVER)
    assert info.video_index == 2 and info.video_codec == "h264"
    assert info.audio_index == 0


def test_probe_without_video():
    with pytest.raises(FFmpegError):
        parse_probe_output("a.mp3", "Input #0, mp3\n  Stream #0:0: Audio: mp3, 44100 Hz, stereo\n")
