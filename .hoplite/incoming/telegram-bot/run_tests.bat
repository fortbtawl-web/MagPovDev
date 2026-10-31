@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (call "%~dp0check_env.bat" || (pause & exit /b 1))
if "%TEST_DATABASE_URL%"=="" (
  echo Тестам нужна ОТДЕЛЬНАЯ пустая база PostgreSQL ^(она очищается при каждом запуске!^).
  echo Пример: set TEST_DATABASE_URL=postgresql+asyncpg://spiritt:ПАРОЛЬ@localhost:5432/spiritt_test
  pause & exit /b 1
)
".venv\Scripts\python.exe" -m pytest -q
pause
