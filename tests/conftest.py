import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avclipper.testmedia import MJPEG, make_test_video as make_video  # noqa: E402,F401


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def basic_video(media_dir):
    path = media_dir / "basic.mp4"
    truth = make_video(path, [("snow", 3), ("video", 4), ("snow", 6), ("video", 3), ("snow", 2)])
    return path, truth


@pytest.fixture(scope="session")
def mjpeg_video(media_dir):
    path = media_dir / "capture.avi"
    truth = make_video(path, [("snow", 2), ("video", 3), ("snow", 4), ("video", 3), ("snow", 2)],
                       vcodec=MJPEG, acodec=("-c:a", "pcm_s16le"))
    return path, truth


def pytest_collection_modifyitems(config, items):
    if os.environ.get("AVCLIPPER_SKIP_GUI"):
        skip = pytest.mark.skip(reason="GUI tests disabled")
        for item in items:
            if "gui" in item.keywords:
                item.add_marker(skip)
