@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "FOUND_ANY=0"

call :stopPort 8000 "Essay API"
call :stopPort 8001 "Quiz API"
call :stopPort 8002 "PRQ API"
call :stopPort 8080 "API Gateway"

if "%FOUND_ANY%"=="0" (
  echo No running API listeners were found on ports 8080, 8000, 8001, 8002.
) else (
  echo Done. Requested shutdown for all running API listeners.
)

echo.
echo You can now close any leftover cmd windows if they are still open.
pause
exit /b 0

:stopPort
set "PORT=%~1"
set "LABEL=%~2"
set "PORT_FOUND=0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  set "PORT_FOUND=1"
  set "FOUND_ANY=1"
  echo Stopping !LABEL! listener on port %PORT% (PID %%P)...
  taskkill /PID %%P /T >nul 2>&1
  if errorlevel 1 taskkill /PID %%P /T /F >nul 2>&1
)

if "!PORT_FOUND!"=="0" (
  echo !LABEL! listener not running on port %PORT%.
)

exit /b 0
