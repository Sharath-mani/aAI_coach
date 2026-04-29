@echo off
setlocal

cd /d "%~dp0"

set "PY_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PY_EXE%" (
  "%PY_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
  if errorlevel 1 (
    echo [WARN] .venv Python is missing required packages. Falling back to system python.
    set "PY_EXE=python"
  )
) else (
  set "PY_EXE=python"
)

"%PY_EXE%" -c "import fastapi, uvicorn" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Could not import fastapi/uvicorn using "%PY_EXE%".
  echo Install dependencies with: pip install -r requirements.txt
  pause
  exit /b 1
)

echo Starting FastAPI servers with "%PY_EXE%"...
start "API Gateway :8080" cmd /k "cd /d ""%~dp0"" & ""%PY_EXE%"" ""%~dp0api_gateway.py"""
start "Essay API :8000" cmd /k "cd /d ""%~dp0"" & ""%PY_EXE%"" ""%~dp0fastapi_app.py"""
start "Quiz API :8001" cmd /k "cd /d ""%~dp0"" & ""%PY_EXE%"" ""%~dp0quiz_fastapi_app.py"""
start "PRQ API :8002" cmd /k "cd /d ""%~dp0"" & ""%PY_EXE%"" ""%~dp0para_read_fastapi.py"""

timeout /t 2 >nul
start "AgenixAI" "%~dp0index.html"

echo.
echo Opened index.html and launched API windows on ports 8080, 8000, 8001, 8002.
echo Keep those API windows open while using the app.
echo To stop all APIs in one click, run stop_servers.bat
pause