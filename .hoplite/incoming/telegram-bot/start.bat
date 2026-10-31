@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0check_env.bat"
if errorlevel 1 (pause & exit /b 1)
echo Запускаю SpirittVPN ^(API + бот^) через run.py. Остановка — Ctrl+C.
rem Локальная БД: миграции применяются автоматически, как раньше.
set AUTO_MIGRATE=true
".venv\Scripts\python.exe" run.py
if errorlevel 1 (echo [ОШИБКА] Запуск завершился с ошибкой — смотрите сообщения выше. & pause & exit /b 1)
pause
