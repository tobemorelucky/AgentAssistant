@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ====================================
echo Starting SuperBizAgent
echo ====================================
echo.

if not exist logs mkdir logs

set "PYTHON=.venv\Scripts\python.exe"

echo [1/7] Checking tools...
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker not found. Please start Docker Desktop first.
    goto FAIL
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [INFO] uv not found, will use pip
    set "USE_UV=0"
) else (
    echo [OK] uv found
    set "USE_UV=1"
)
echo.

echo [2/7] Checking Python environment...
if not exist "%PYTHON%" (
    echo [ERROR] .venv not found.
    echo Please run install-windows.bat first.
    goto FAIL
)

echo [OK] Python environment ready
echo.

echo [3/7] Starting Milvus stack...
docker compose -f vector-database.yml up -d --pull never
if errorlevel 1 (
    echo [ERROR] Failed to start Milvus stack.
    echo [HINT] Make sure required images already exist locally:
    echo        milvusdb/milvus:v2.5.10
    echo        minio/minio:RELEASE.2023-03-20T20-16-18Z
    echo        quay.io/coreos/etcd:v3.5.18
    echo        zilliz/attu:v2.5
    goto FAIL
)

echo [INFO] Waiting for Milvus health...
call :WaitUrl "http://127.0.0.1:9091/healthz" 120 "Milvus"
if errorlevel 1 (
    echo [ERROR] Milvus is not healthy.
    docker compose -f vector-database.yml ps
    docker logs milvus-standalone --tail 80
    goto FAIL
)
echo.

echo [4/7] Starting CLS Server...
call :WaitPort 8003 1 "CLS Server"
if not errorlevel 1 (
    echo [INFO] CLS Server already running on port 8003
) else (
    start "CLS Server" /min cmd /c ""%PYTHON%" "mcp_servers\cls_server.py" 1>>"logs\cls_server.log" 2>&1"
    call :WaitPort 8003 30 "CLS Server"
    if errorlevel 1 (
        echo [ERROR] CLS Server failed to start.
        call :PrintLog "logs\cls_server.log" 80
        goto FAIL
    )
)
echo.

echo [5/7] Starting Monitor Server...
call :WaitPort 8004 1 "Monitor Server"
if not errorlevel 1 (
    echo [INFO] Monitor Server already running on port 8004
) else (
    start "Monitor Server" /min cmd /c ""%PYTHON%" "mcp_servers\monitor_server.py" 1>>"logs\monitor_server.log" 2>&1"
    call :WaitPort 8004 30 "Monitor Server"
    if errorlevel 1 (
        echo [ERROR] Monitor Server failed to start.
        call :PrintLog "logs\monitor_server.log" 80
        goto FAIL
    )
)
echo.

echo [6/7] Starting FastAPI...
call :WaitPort 9900 1 "FastAPI"
if not errorlevel 1 (
    echo [INFO] FastAPI already running on port 9900
) else (
    start "FastAPI Main" /min cmd /c ""%PYTHON%" -m uvicorn app.main:app --host 0.0.0.0 --port 9900 1>>"logs\fastapi.log" 2>&1"
)

echo [INFO] Waiting for FastAPI health...
call :WaitUrl "http://127.0.0.1:9900/health" 90 "FastAPI"
if errorlevel 1 (
    echo [ERROR] FastAPI is not healthy.
    call :PrintLog "logs\fastapi.log" 120
    goto FAIL
)
echo.

echo [7/7] Service status...
docker compose -f vector-database.yml ps
echo.

echo ====================================
echo ALL SERVICES STARTED
echo ====================================
echo Web UI:   http://127.0.0.1:9900
echo API Docs: http://127.0.0.1:9900/docs
echo Health:   http://127.0.0.1:9900/health
echo Attu:     http://127.0.0.1:8000
echo ====================================
pause
exit /b 0

:WaitUrl
set "URL=%~1"
set "MAX=%~2"
set "NAME=%~3"
set /a WAITED=0

:WaitUrlLoop
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '!URL!' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo [OK] !NAME! ready
    exit /b 0
)

if !WAITED! GEQ !MAX! (
    echo [ERROR] !NAME! not ready after !MAX! seconds
    exit /b 1
)

timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto WaitUrlLoop

:WaitPort
set "PORT=%~1"
set "MAX=%~2"
set "NAME=%~3"
set /a WAITED=0

:WaitPortLoop
powershell -NoProfile -ExecutionPolicy Bypass -Command "$c = New-Object Net.Sockets.TcpClient; try { $iar = $c.BeginConnect('127.0.0.1', !PORT!, $null, $null); if ($iar.AsyncWaitHandle.WaitOne(1000, $false)) { $c.EndConnect($iar); $c.Close(); exit 0 } else { $c.Close(); exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo [OK] !NAME! port !PORT! ready
    exit /b 0
)

if !WAITED! GEQ !MAX! (
    echo [ERROR] !NAME! port !PORT! not ready after !MAX! seconds
    exit /b 1
)

timeout /t 2 /nobreak >nul
set /a WAITED+=2
goto WaitPortLoop

:PrintLog
set "LOG_FILE=%~1"
set "LINES=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path '!LOG_FILE!') { Get-Content '!LOG_FILE!' -Tail !LINES! } else { Write-Host '[INFO] Log file not found: !LOG_FILE!' }"
exit /b 0

:FAIL
echo.
echo ====================================
echo START FAILED
echo ====================================
echo Please check logs under ./logs
echo.
pause
exit /b 1