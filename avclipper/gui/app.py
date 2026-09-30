"""GUI entry point."""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

LOG_PATH = os.path.join(tempfile.gettempdir(), "avclipper-error.log")


def _write_log(text: str) -> None:
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def _fatal(text: str) -> None:
    """Report a startup failure even without a console (pythonw / packaged exe)."""
    _write_log(text)
    message = f"Не удалось запустить программу.\n\n{text}\n\nПодробности: {LOG_PATH}"
    if sys.stderr is not None:
        sys.stderr.write(message + "\n")
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "AV Video Clipper", 0x10)
        except Exception:
            pass


def _install_excepthook() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        _write_log(text)
        if sys.stderr is not None:
            sys.stderr.write(text)
        if QApplication.instance() is not None:
            QMessageBox.critical(None, "AV Video Clipper",
                                 f"Непредвиденная ошибка:\n\n{exc}\n\nПодробности записаны в {LOG_PATH}")

    sys.excepthook = hook


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtWidgets import QApplication

        from .. import APP_NAME
        from . import theme
        from .main_window import MainWindow
    except Exception:
        _fatal(traceback.format_exc())
        return 1

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AVvideoClipper")
    app.setWindowIcon(theme.app_icon())
    theme.apply_theme(app)
    _install_excepthook()
    window = MainWindow(files=[a for a in argv[1:] if not a.startswith("-")])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
