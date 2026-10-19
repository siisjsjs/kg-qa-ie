@echo off
REM KG-QA-IE one-click start (dev mode)
REM   1) ensure Docker Desktop daemon
REM   2) docker compose up -d mysql chroma neo4j
REM   3) launch backend (uvicorn :8000 --reload) in a new window
REM   4) launch frontend (vite :5173) in a new window

setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "COMPOSE_FILE=%ROOT%\deploy\docker-compose.yml"
set "DOCKER_DESKTOP=C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo ==========================================
echo   KG-QA-IE  start  ^(root: %ROOT%^)
echo ==========================================

REM ---- 1) Docker daemon ----
docker info >/dev/null 2>&1
if errorlevel 1 (
    echo [1/4] Docker daemon not running. Launching Docker Desktop...
    if not exist "%DOCKER_DESKTOP%" (
        echo     ERROR: Docker Desktop not found at "%DOCKER_DESKTOP%"
        echo     Please start Docker Desktop manually, then re-run this script.
        pause
        exit /b 1
    )
    start "" "%DOCKER_DESKTOP%"
    echo     waiting for daemon ^(up to 120s^)...
    set /a WAIT=0
    :wait_docker
        timeout /t 4 /nobreak >/dev/null
        docker info >/dev/null 2>&1
        if not errorlevel 1 goto docker_ready
        set /a WAIT+=4
        if !WAIT! GEQ 120 (
            echo     ERROR: Docker daemon did not become ready in 120s.
            pause
            exit /b 1
        )
        goto wait_docker
    :docker_ready
    echo     Docker daemon ready.
) else (
    echo [1/4] Docker daemon already running.
)

REM ---- 2) compose up dependencies ----
echo [2/4] docker compose up -d mysql chroma neo4j ...
docker compose -f "%COMPOSE_FILE%" up -d mysql chroma neo4j
if errorlevel 1 (
    echo     WARN: compose up returned non-zero. Continuing.
)

REM ---- 3) backend in a new window ----
echo [3/4] launching backend ^(uvicorn :8000 --reload^) in a new window...
start "kg-qa-ie backend :8000" cmd /k "cd /d %ROOT%\backend && uvicorn app.main:app --reload --port 8000 --host 0.0.0.0"

REM ---- 4) frontend in a new window ----
echo [4/4] launching frontend ^(vite :5173^) in a new window...
start "kg-qa-ie frontend :5173" cmd /k "cd /d %ROOT%\frontend && npm run dev"

echo.
echo ==========================================
echo   started
echo     backend   http://localhost:8000/health
echo     frontend  http://localhost:5173
echo     mysql     localhost:3309
echo     chroma    localhost:8001
echo     neo4j     http://localhost:7476  bolt 7689
echo   to stop:  run stop.bat
echo ==========================================
echo.
pause
endlocal
