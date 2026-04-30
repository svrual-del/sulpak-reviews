@echo off
REM ============================================================
REM  Sulpak daily report — обёртка для Windows Task Scheduler
REM  Использование:
REM    run_report.cmd            — отчёт за сегодня
REM    run_report.cmd 20260422   — отчёт за указанную дату
REM ============================================================

setlocal

cd /d "%~dp0"

echo [%date% %time%] === git pull (report) === >> git_pull.log
git pull --ff-only >> git_pull.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] git pull failed - запускаюсь со старым кодом >> git_pull.log
)

REM Запустить отчёт. Если %1 пуст — daily_report.py возьмёт сегодняшнюю дату.
py daily_report.py %1

endlocal
