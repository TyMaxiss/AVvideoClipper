import json

from avclipper.cli import main


def test_cli_dry_run_json(basic_video, tmp_path, capsys):
    path, truth = basic_video
    code = main([str(path), "--dry-run", "--json", "--pad", "0", "-o", str(tmp_path)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report) == 1
    segs = report[0]["segments"]
    assert len(segs) == len(truth)
    for seg, (a, b) in zip(segs, truth):
        assert abs(seg["start"] - a) < 0.5 and abs(seg["end"] - b) < 0.5
    assert report[0]["outputs"][0].endswith("basic_clean.mp4")
    assert not list(tmp_path.iterdir())


def test_cli_export(basic_video, tmp_path, capsys):
    path, _ = basic_video
    code = main([str(path), "-o", str(tmp_path), "--separate", "--suffix", "_video"])
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["basic_video_01.mp4", "basic_video_02.mp4"]


def test_cli_missing_file(tmp_path, capsys):
    code = main([str(tmp_path / "nope.mp4")])
    assert code == 1
