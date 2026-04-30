@echo off
REM ============================================================
REM  Sulpak review moderator — обёртка для Windows Task Scheduler
REM  1) переходим в каталог скрипта
REM  2) подтягиваем свежий код из GitHub (fast-forward, безопасно)
REM  3) запускаем модерацию
REM ============================================================

setlocal

REM Перейти в каталог, где лежит этот .cmd-файл
cd /d "%~dp0"

REM Подтянуть свежий код. --ff-only: если будет конфликт, pull откажется
REM и скрипт всё равно запустится — со старой версией. Логи в git_pull.log.
echo [%date% %time%] === git pull === >> git_pull.log
git pull --ff-only >> git_pull.log 2>&1
if errorlevel 1 (
    echo [%date% %time%] git pull failed - запускаюсь со старым кодом >> git_pull.log
)

REM Запустить модерацию
py sulpak_review_moderator.py

endlocal
