@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -B -m tools.preview_acceptance --extra-hours 2
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Install Python 3.10 or later, then run this file again.
        pause
        exit /b 1
    )
    python -B -m tools.preview_acceptance --extra-hours 2
)
pause
