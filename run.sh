#!/usr/bin/env sh
# Запуск AV Video Clipper из исходников. При первом запуске создаёт .venv и ставит зависимости.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Первый запуск: устанавливаю зависимости..."
    python3 -m venv .venv || { echo "Нужен Python 3.9+ с модулем venv"; exit 1; }
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt || { rm -rf .venv; exit 1; }
fi
exec .venv/bin/python -m avclipper "$@"
