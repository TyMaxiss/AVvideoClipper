"""GUI entry point."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from . import theme
    from .main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("AVvideoClipper")
    app.setWindowIcon(theme.app_icon())
    theme.apply_theme(app)
    window = MainWindow(files=[a for a in argv[1:] if not a.startswith("-")])
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
