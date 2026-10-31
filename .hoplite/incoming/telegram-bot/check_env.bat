@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo === SpirittVPN: проверка окружения ===

set "PY="
where py >nul 2>nul && (py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12")
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo [ОШИБКА] Python не найден. Установите Python 3.12 с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^).
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)"
if errorlevel 1 (
  echo [ОШИБКА] Нужен Python 3.12 или новее. Сейчас:
  %PY% --version
  exit /b 1
)
echo [OK] Python найден: %PY%

if not exist ".venv\Scripts\python.exe" (
  echo Создаю виртуальное окружение .venv ...
  %PY% -m venv .venv || (echo [ОШИБКА] Не удалось создать .venv & exit /b 1)
)
echo Устанавливаю зависимости ^(первый раз — несколько минут^) ...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt
if errorlevel 1 (echo [ОШИБКА] Не удалось установить зависимости. Проверьте интернет. & exit /b 1)
echo [OK] Зависимости установлены

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [ВНИМАНИЕ] Создан файл .env из .env.example. Откройте его в Блокноте, заполните BOT_TOKEN, ADMIN_IDS, DATABASE_URL, SESSION_SECRET и запустите снова.
  exit /b 1
)
findstr /b /c:"BOT_TOKEN=" ".env" | findstr /r /c:"BOT_TOKEN=.." >nul || (echo [ОШИБКА] В .env не заполнен BOT_TOKEN & exit /b 1)
findstr /r /c:"^SESSION_SECRET=................................" ".env" >nul || (echo [ОШИБКА] SESSION_SECRET в .env должен быть не короче 32 символов & exit /b 1)
echo [OK] .env найден

".venv\Scripts\python.exe" scripts\check_db.py
if errorlevel 1 (
  echo [ОШИБКА] Нет подключения к PostgreSQL. Проверьте, что служба PostgreSQL запущена и DATABASE_URL в .env верный. См. README, раздел "PostgreSQL на Windows".
  exit /b 1
)
echo [OK] PostgreSQL доступен

if not exist "frontend\dist\index.html" (
  where pnpm >nul 2>nul && (
    echo Собираю Mini App и админ-панель ...
    pushd frontend && call pnpm install && call pnpm build & popd
  ) || (
    echo [ВНИМАНИЕ] Node.js/pnpm не найдены — веб-интерфейс не собран. Бот работать будет. Установите Node.js 22 и выполните: npm i -g pnpm
  )
)
echo === Проверка завершена ===
exit /b 0
