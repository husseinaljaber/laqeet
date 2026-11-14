@echo off
title Laqeet
cd /d "%~dp0"
echo.
echo   ==============================
echo     Laqeet - starting...
echo   ==============================
echo.

rem ---- 1. Find Python 3.10 or newer ----
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

rem ---- 2. First run: install the libraries (once) ----
if exist ".venv\ready.txt" goto libs_ok
echo   [1/3] First run: installing libraries. This takes 3-5 minutes and needs internet...
if exist .venv rmdir /s /q .venv
%PY% -m venv .venv || goto failinstall
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto failinstall
echo ok> ".venv\ready.txt"
echo   Done.
echo.
:libs_ok

rem ---- 3. First run: ask for the Telegram bot token (once) ----
if exist .env goto env_ok
echo   [2/3] Telegram bot setup
echo   Open @BotFather in Telegram, copy your bot token, and paste it here.
echo   (To paste: right-click in this window. To skip the bot: just press Enter.)
echo.
set "TOKEN="
set /p "TOKEN=  Bot token: "
set "ADMINPASS=laqeet%RANDOM%%RANDOM%%RANDOM%"
> .env echo # Laqeet settings. Keep this file private. Do not send it to anyone.
>> .env echo TELEGRAM_BOT_TOKEN=%TOKEN%
>> .env echo ADMIN_TOKEN=%ADMINPASS%
>> .env echo PUBLIC_URL=http://localhost:8000
>> .env echo ANTHROPIC_API_KEY=
>> .env echo UPDATE_MINUTES=30
echo.
echo   Saved. Your admin page password is: %ADMINPASS%
echo   (It is also written in the file .env inside this folder.)
echo.
:env_ok

rem ---- Load the settings from .env ----
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if /i not "%%a"=="SITE_NAME" set "%%a=%%b"

rem ---- Build the database if it is missing ----
if not exist "data\laqeet.db" (
  echo   Building the database from saved posts, 1-2 minutes...
  ".venv\Scripts\python.exe" -m app.collect --offline
)

rem ---- 4. Start the collector + bot in a second, minimized window ----
taskkill /fi "WINDOWTITLE eq Laqeet bot*" /f >nul 2>nul
start "Laqeet bot" /min ".venv\Scripts\python.exe" -m app.run

rem ---- 5. Start the website and open the browser ----
echo   [3/3] The website is running at:  http://localhost:8000
echo.
echo   Keep this window open. Closing it stops the website.
echo.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn app.api:app --port 8000
echo.
echo   The website stopped. If you see an error above, take a screenshot and send it.
taskkill /fi "WINDOWTITLE eq Laqeet bot*" /f >nul 2>nul
pause
exit /b

:nopython
echo   Python is not installed (or "Add python.exe to PATH" was not ticked).
echo.
echo   1. The Python download page will open now. Download Python 3.12 and run it.
echo   2. On the FIRST screen, tick the box "Add python.exe to PATH", then click "Install Now".
echo   3. When it finishes, double-click run.bat again.
echo.
start https://www.python.org/downloads/
pause
exit /b

:failinstall
echo.
echo   Installing failed. Check your internet, then double-click run.bat again.
echo   If it fails again, take a screenshot of this window and send it.
if exist .venv rmdir /s /q .venv
pause
exit /b
