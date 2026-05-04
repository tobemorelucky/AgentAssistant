@echo off
chcp 65001 >nul
echo ====================================
echo Stopping SuperBizAgent
echo ====================================
echo.

echo [1/4] Stopping FastAPI...
taskkill /FI "WINDOWTITLE eq FastAPI Main*" /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] FastAPI not running
) else (
    echo [OK] FastAPI stopped
)
echo.

echo [2/4] Stopping CLS Server...
taskkill /FI "WINDOWTITLE eq CLS Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] CLS Server not running
) else (
    echo [OK] CLS Server stopped
)
echo.

echo [3/4] Stopping Monitor Server...
taskkill /FI "WINDOWTITLE eq Monitor Server*" /F >nul 2>&1
if errorlevel 1 (
    echo [INFO] Monitor Server not running
) else (
    echo [OK] Monitor Server stopped
)
echo.

echo [4/4] Stopping Milvus...
docker ps --format "{{.Names}}" | findstr "milvus" >nul 2>&1
if not errorlevel 1 (
    docker compose -f vector-database.yml down
    echo [OK] Milvus stopped
) else (
    echo [INFO] Milvus not running
)
echo.

echo ====================================
echo ALL SERVICES STOPPED
echo ====================================
pause