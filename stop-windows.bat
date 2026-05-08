@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ====================================
echo Stopping SuperBizAgent
echo ====================================
echo.

echo [1/4] Stopping FastAPI...
call :KillPort 9900 "FastAPI"
taskkill /FI "WINDOWTITLE eq FastAPI Main*" /F >nul 2>&1
echo.

echo [2/4] Stopping CLS Server...
call :KillPort 8003 "CLS Server"
taskkill /FI "WINDOWTITLE eq CLS Server*" /F >nul 2>&1
echo.

echo [3/4] Stopping Monitor Server...
call :KillPort 8004 "Monitor Server"
taskkill /FI "WINDOWTITLE eq Monitor Server*" /F >nul 2>&1
echo.

echo [4/4] Stopping Milvus stack and releasing container names...
docker compose -f vector-database.yml down
if errorlevel 1 (
    echo [WARN] docker compose down failed. Please check Docker Desktop.
) else (
    echo [OK] Milvus stack stopped. Container names released. Data is preserved.
)
echo.

echo ====================================
echo ALL SERVICES STOPPED
echo Data preserved in:
echo   volumes/
echo   uploads/
echo   data/
echo   logs/
echo ====================================
pause
exit /b 0

:KillPort
set "PORT=%~1"
set "NAME=%~2"
set "FOUND=0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":!PORT! .*LISTENING"') do (
    set "FOUND=1"
    echo [INFO] Killing !NAME! on port !PORT!, PID %%P
    taskkill /PID %%P /F >nul 2>&1
)

if "!FOUND!"=="0" (
    echo [INFO] !NAME! not running on port !PORT!
) else (
    echo [OK] !NAME! stopped
)

exit /b 0