@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
for /f "tokens=1,* delims==" %%a in ('findstr /b "DATABASE_URL=" .env') do set "URL=%%b"
set "URL=%URL:postgresql+asyncpg://=postgresql://%"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
pg_dump --format=custom --no-owner --file="backups\spiritt_%TS%.dump" "%URL%" && echo Резервная копия: backups\spiritt_%TS%.dump
pause
