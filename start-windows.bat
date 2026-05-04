@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ====================================
echo Starting SuperBizAgent
echo ====================================
echo.

echo [1/6] Checking Package Manager...
set USE_UV=0
where uv >nul 2>&1
if not errorlevel 1 (
    echo [OK] uv found
    set USE_UV=1
) else (
    echo [INFO] uv not found, using pip
)
echo.

echo [2/6] Setting up Venv...
if not exist .venv\Scripts\python.exe (
    echo [INFO] Creating venv...
    if "%USE_UV%"=="1" (
        uv venv
        uv pip install -e .
    ) else (
        python -m venv .venv
        .venv\Scripts\python.exe -m pip install -e .
    )
) else (
    echo [OK] Venv exists
)
echo.

echo [3/6] Starting Milvus...
docker ps --format "{{.Names}}" | findstr "milvus-standalone" >nul 2>&1
if errorlevel 1 (
    docker compose -f vector-database.yml up -d
    echo [INFO] Waiting 10s for Milvus...
    timeout /t 10 /nobreak >nul
)
echo [OK] Milvus ready
echo.

echo [4/6] Starting CLS Server...
start "CLS Server" /min .venv\Scripts\python.exe mcp_servers/cls_server.py
timeout /t 2 /nobreak >nul
echo [OK] CLS Started
echo.

echo [5/6] Starting Monitor Server...
start "Monitor Server" /min .venv\Scripts\python.exe mcp_servers/monitor_server.py
timeout /t 2 /nobreak >nul
echo [OK] Monitor Started
echo.

echo [6/6] Starting FastAPI...
start "FastAPI Main" .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 9900
echo [INFO] Waiting 15s...
timeout /t 15 /nobreak >nul
echo.

echo ====================================
echo ALL SERVICES STARTED!
echo ====================================
echo Web UI: http://localhost:9900
echo API Docs: http://localhost:9900/docs
echo ====================================
pause