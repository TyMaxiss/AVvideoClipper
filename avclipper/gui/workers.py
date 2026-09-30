"""Background work for the GUI: analysis, export and preview frames."""
from __future__ import annotations

import subprocess
import threading
import traceback
from collections import OrderedDict
from typing import List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage

from ..analysis import analyze
from ..export import ExportSettings, export
from ..media import Cancelled, MediaInfo, find_ffmpeg, popen_flags, probe
from ..segments import Segment


class JobSignals(QObject):
    progress = Signal(int, float)       # item id, 0..1
    finished = Signal(int, object)      # item id, result
    failed = Signal(int, str)           # item id, message
    cancelled = Signal(int)


class _Job(QRunnable):
    def __init__(self, item_id: int):
        super().__init__()
        self.setAutoDelete(True)
        self.item_id = item_id
        self.cancel = threading.Event()
        self.signals = JobSignals()
        self._last = -1

    def report(self, fraction: float) -> None:
        pct = int(fraction * 1000)
        if pct != self._last:
            self._last = pct
            self.signals.progress.emit(self.item_id, fraction)

    def run(self) -> None:
        try:
            result = self.work()
        except Cancelled:
            self.signals.cancelled.emit(self.item_id)
        except Exception as exc:  # reported to the user, never crash the UI
            traceback.print_exc()
            message = str(exc).strip() or exc.__class__.__name__
            self.signals.failed.emit(self.item_id, message)
        else:
            self.signals.finished.emit(self.item_id, result)

    def work(self):
        raise NotImplementedError


class AnalyzeJob(_Job):
    kind = "analyze"

    def __init__(self, item_id: int, path: str):
        super().__init__(item_id)
        self.path = path

    def work(self):
        info = probe(self.path)
        return analyze(self.path, info, progress=self.report, cancel=self.cancel)


class ExportJob(_Job):
    kind = "export"

    def __init__(self, item_id: int, info: MediaInfo, segments: List[Segment], settings: ExportSettings):
        super().__init__(item_id)
        self.info = info
        self.segments = [Segment(s.start, s.end, s.enabled) for s in segments]
        self.settings = ExportSettings.from_dict(settings.to_dict())

    def work(self):
        return export(self.info, self.segments, self.settings, progress=self.report, cancel=self.cancel)


class PreviewLoader(QObject):
    """Grabs single frames with ffmpeg on a background thread.

    Only the most recent request is served; results are cached.
    """

    frameReady = Signal(str, int, QImage)   # path, time key (1/10 s), image

    HEIGHT = 360

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cond = threading.Condition()
        self._request: Optional[Tuple[MediaInfo, int]] = None
        self._cache: "OrderedDict[Tuple[str, int], QImage]" = OrderedDict()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    @staticmethod
    def key(t: float) -> int:
        return int(round(t * 10))

    def request(self, info: MediaInfo, t: float) -> None:
        key = self.key(t)
        with self._cond:
            cached = self._cache.get((info.path, key))
        if cached is not None:
            self.frameReady.emit(info.path, key, cached)
            return
        with self._cond:
            self._request = (info, key)
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._request is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                info, key = self._request
                self._request = None
            image = self._grab(info, key / 10.0)
            if image is None:
                continue
            with self._cond:
                self._cache[(info.path, key)] = image
                while len(self._cache) > 300:
                    self._cache.popitem(last=False)
            self.frameReady.emit(info.path, key, image)

    def _grab(self, info: MediaInfo, t: float) -> Optional[QImage]:
        h = self.HEIGHT if info.height <= 0 else min(self.HEIGHT, max(info.height, 120))
        w = int(round(h * info.display_aspect / 2)) * 2
        cmd = [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error",
               "-ss", f"{max(t, 0.0):.3f}", "-i", info.path, "-map", f"0:{info.video_index}",
               "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=bicubic,format=rgb24",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=20, **popen_flags()).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        if len(out) < w * h * 3:
            return None
        return QImage(out[: w * h * 3], w, h, w * 3, QImage.Format_RGB888).copy()
