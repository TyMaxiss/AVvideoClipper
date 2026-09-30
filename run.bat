@echo off
chcp 65001 >nul
rem Запуск AV Video Clipper из исходников. При первом запуске создаёт .venv и ставит зависимости.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run
echo Первый запуск: устанавливаю зависимости, это займёт пару минут...
where py >nul 2>nul
if not errorlevel 1 (py -3 -m venv .venv) else (python -m venv .venv)
if not exist ".venv\Scripts\python.exe" (
    echo Не найден Python. Установите Python 3.10 или новее: https://www.python.org/downloads/
    echo При установке отметьте галочку "Add python.exe to PATH".
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить зависимости. Проверьте подключение к интернету.
    rmdir /s /q .venv
    pause
    exit /b 1
)
:run
start "" ".venv\Scripts\pythonw.exe" -m avclipper %*
