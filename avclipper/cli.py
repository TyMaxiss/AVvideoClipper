"""Command-line interface: avclipper-cli video.mp4 [more files...]"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import APP_NAME, __version__
from .analysis import analyze
from .export import LAYOUT_JOINED, LAYOUT_SEPARATE, ExportSettings, export, plan_export
from .media import Cancelled, FFmpegError, FFmpegNotFound, probe
from .segments import DetectionSettings, find_segments, kept_duration
from .timefmt import format_time


def _bar(label: str):
    last = [-1]

    def report(fraction: float) -> None:
        pct = int(fraction * 100)
        if pct != last[0]:
            last[0] = pct
            sys.stderr.write(f"\r  {label}: {pct:3d}%")
            sys.stderr.flush()
            if pct >= 100:
                sys.stderr.write("\n")
    return report


def build_parser() -> argparse.ArgumentParser:
    d = DetectionSettings()
    p = argparse.ArgumentParser(
        prog="avclipper-cli",
        description=f"{APP_NAME} {__version__}: вырезает белый шум (\"снег\") из AV-видеозаписей.",
    )
    p.add_argument("files", nargs="+", help="видеофайлы для обработки")
    p.add_argument("-o", "--output-dir", default="", help="папка для результатов (по умолчанию рядом с исходником)")
    p.add_argument("--suffix", default="_clean", help="суффикс имени результата (по умолчанию _clean)")
    p.add_argument("--separate", action="store_true", help="сохранить каждый фрагмент отдельным файлом")
    p.add_argument("--mode", choices=["auto", "reencode", "copy"], default="auto",
                   help="auto: без перекодирования для MJPEG/DV, иначе H.264; reencode: всегда H.264 (точно); "
                        "copy: без перекодирования (быстро, границы по ключевым кадрам)")
    p.add_argument("--crf", type=int, default=18, help="качество H.264, меньше = лучше (по умолчанию 18)")
    p.add_argument("--threshold", type=float, default=d.threshold,
                   help=f"порог сигнала 0..1: кадры выше порога считаются видео (по умолчанию {d.threshold})")
    p.add_argument("--min-segment", type=float, default=d.min_segment,
                   help=f"отбрасывать фрагменты видео короче N секунд (по умолчанию {d.min_segment})")
    p.add_argument("--merge-gap", type=float, default=d.merge_gap,
                   help=f"не вырезать шум короче N секунд внутри видео (по умолчанию {d.merge_gap})")
    p.add_argument("--pad", type=float, default=d.pad_before,
                   help=f"запас в секундах до и после каждого фрагмента (по умолчанию {d.pad_before})")
    p.add_argument("--keep-blank", action="store_true",
                   help="не вырезать однотонные кадры (чёрный/синий экран)")
    p.add_argument("--dry-run", action="store_true", help="только найти фрагменты, файлы не создавать")
    p.add_argument("--json", action="store_true", help="вывести результат в формате JSON")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    detection = DetectionSettings(
        threshold=args.threshold, min_segment=args.min_segment, merge_gap=args.merge_gap,
        pad_before=args.pad, pad_after=args.pad, blank_is_noise=not args.keep_blank,
    )
    settings = ExportSettings(
        mode=args.mode, layout=LAYOUT_SEPARATE if args.separate else LAYOUT_JOINED,
        output_dir=args.output_dir, suffix=args.suffix, crf=args.crf,
    )
    quiet = args.json
    report = []
    failed = 0
    for path in args.files:
        entry = {"file": path}
        try:
            if not quiet:
                print(f"{path}")
            info = probe(path)
            result = analyze(path, info, progress=None if quiet else _bar("анализ"))
            segments = find_segments(result, detection)
            entry["duration"] = round(result.duration, 3)
            entry["segments"] = [{"start": round(s.start, 3), "end": round(s.end, 3)} for s in segments]
            if not quiet:
                if segments:
                    for i, s in enumerate(segments, 1):
                        print(f"  фрагмент {i}: {format_time(s.start)} – {format_time(s.end)}"
                              f"  ({format_time(s.duration)})")
                    print(f"  оставить {format_time(kept_duration(segments))} из {format_time(result.duration)}")
                else:
                    print("  видео не найдено: вся запись похожа на шум")
            if args.dry_run:
                entry["outputs"] = [job.output for job in plan_export(info, segments, settings)]
            else:
                entry["outputs"] = export(info, segments, settings,
                                          progress=None if quiet else _bar("сохранение"))
                if not quiet:
                    for out in entry["outputs"]:
                        print(f"  -> {out}")
        except (FFmpegError, FFmpegNotFound, OSError) as exc:
            failed += 1
            entry["error"] = str(exc)
            if not quiet:
                print(f"  ошибка: {exc}", file=sys.stderr)
        except (KeyboardInterrupt, Cancelled):
            print("\nПрервано.", file=sys.stderr)
            return 130
        report.append(entry)
    if quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
