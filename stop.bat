@echo off
REM KG-QA-IE one-click stop
REM   1) kill processes on :8000 and :5173
REM   2) docker compose down (volumes preserved)

setlocal ENABLEEXTENSIONS
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "COMPOSE_FILE=%ROOT%\deploy\docker-compose.yml"

echo ==========================================
echo   KG-QA-IE  stop
echo ==========================================

echo [1/2] killing processes on :8000 and :5173 ...
for %%P in (8000 5173) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo     killing PID %%A on port %%P
        taskkill /F /PID %%A >/dev/null 2>&1
    )
)

echo [2/2] docker compose down ^(volumes preserved^) ...
docker compose -f "%COMPOSE_FILE%" down

echo.
echo   stopped.
echo.
pause
endlocal
