"""Smoke test of the Qt window (runs offscreen)."""
import os
import time

import pytest

pytestmark = pytest.mark.gui

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # pragma: no cover - PySide6 or its system libraries missing
    pytest.skip(f"PySide6 unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(["test"])
    yield app


def isolated_settings(tmp_path):
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def wait_for(app, cond, timeout=120):
    start = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.02)
        assert time.time() - start < timeout, "timed out"
    app.processEvents()


def test_window_analyzes_and_saves(qapp, basic_video, tmp_path, monkeypatch):
    from avclipper.gui import main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_check_ffmpeg", lambda self: None)

    path, truth = basic_video
    window = mw.MainWindow(files=[str(path)], store=isolated_settings(tmp_path))
    window.export_settings.output_dir = str(tmp_path)
    window.show()
    item = window.current_item()
    assert item is not None
    wait_for(qapp, lambda: item.state == mw.READY)
    assert len(item.segments) == len(truth)
    assert window.table.rowCount() == len(truth)

    # dragging the threshold line updates the settings panel; a long merge gap joins the fragments
    window._on_timeline_threshold(0.6)
    assert window.threshold_slider.value() == 60
    window.merge_gap_spin.setValue(10.0)
    assert len(item.segments) == 1
    window._reset_detection()
    assert window.threshold_slider.value() == 45
    assert len(item.segments) == len(truth)

    # untick the second fragment and save
    window.table.item(1, 0).setCheckState(mw.Qt.Unchecked)
    assert not item.segments[1].enabled
    window.export_current()
    wait_for(qapp, lambda: item.state in (mw.DONE, mw.ERROR))
    assert item.state == mw.DONE, item.error
    assert [os.path.basename(p) for p in item.outputs] == ["basic_clean.mp4"]
    assert os.path.exists(item.outputs[0])

    # preview frames load in the background
    window._set_playhead(item, 4.0)
    wait_for(qapp, lambda: window._frame_image is not None, timeout=30)
    window.close()


def test_process_all_stop_and_remove(qapp, basic_video, mjpeg_video, media_dir, tmp_path, monkeypatch):
    from avclipper.gui import main_window as mw
    from avclipper.testmedia import make_test_video

    monkeypatch.setattr(mw.MainWindow, "_check_ffmpeg", lambda self: None)

    window = mw.MainWindow(store=isolated_settings(tmp_path))
    window.export_settings.output_dir = str(tmp_path / "out")
    window.show()
    window.add_paths([str(basic_video[0]), str(mjpeg_video[0]), str(basic_video[0])])
    assert len(window.items) == 2  # duplicates are ignored
    window.process_all()
    wait_for(qapp, lambda: all(i.state == mw.DONE for i in window.items.values()))
    names = sorted(os.listdir(tmp_path / "out"))
    assert names == ["basic_clean.mp4", "capture_clean.avi"]

    # a long file: stop in the middle of the analysis, then remove it while it runs again
    long_path = media_dir / "long.mp4"
    make_test_video(long_path, [("snow", 60), ("video", 30), ("snow", 60)], size="640x480")
    window.add_paths([str(long_path)])
    item = [i for i in window.items.values() if i.path.endswith("long.mp4")][0]
    wait_for(qapp, lambda: item.state == mw.ANALYZING)
    window.stop_all()
    wait_for(qapp, lambda: item.state == mw.STOPPED and window.running is None)
    window.process_all()
    wait_for(qapp, lambda: item.state == mw.ANALYZING)
    window.file_list.setCurrentRow(window._row_of(item.id))
    window.remove_current()
    wait_for(qapp, lambda: window.running is None)
    assert item.id not in window.items
    window.close()
