"""Entry point.

    python -m avclipper                    graphical interface
    python -m avclipper --cli FILE...      console version (see --cli --help)
    python -m avclipper --self-test [LOG]  quick end-to-end check
"""
import sys


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        from .cli import main as cli_main

        return cli_main(args[1:])
    if args and args[0] == "--self-test":
        from .selftest import run

        return run(args[1] if len(args) > 1 else None)
    from .gui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
