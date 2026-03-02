@echo off
setlocal enabledelayedexpansion

:: ══════════════════════════════════════════════════════════════════
::  DENSEWEALTH DEPLOY SCRIPT
::  Usage: deploy.bat [command]
::
::  Commands:
::    (no args)  - Full deploy (code + restart)
::    code       - Upload code only (no restart)
::    env        - Upload .env only
::    restart    - Restart services only
::    status     - Check server status
::    logs       - Tail live logs
::    ssh        - Open SSH session
:: ══════════════════════════════════════════════════════════════════

set SERVER=89.167.68.109
set USER=root
set KEY=%USERPROFILE%\.ssh\id_ed25519
set LOCAL_DIR=%~dp0
set REMOTE_DIR=/opt/densewealth

:: Colors (Windows 10+)
set GREEN=[92m
set YELLOW=[93m
set RED=[91m
set CYAN=[96m
set RESET=[0m

echo.
echo %CYAN%══════════════════════════════════════════════════════════════════%RESET%
echo %CYAN%  DENSEWEALTH DEPLOY%RESET%
echo %CYAN%══════════════════════════════════════════════════════════════════%RESET%
echo.

:: Check SSH key exists
if not exist "%KEY%" (
    echo %RED%ERROR: SSH key not found at %KEY%%RESET%
    echo Run: ssh-keygen -t ed25519
    exit /b 1
)

:: Parse command
set CMD=%1
if "%CMD%"=="" set CMD=full

if "%CMD%"=="code" goto :upload_code
if "%CMD%"=="env" goto :upload_env
if "%CMD%"=="restart" goto :restart
if "%CMD%"=="status" goto :status
if "%CMD%"=="logs" goto :logs
if "%CMD%"=="ssh" goto :ssh
if "%CMD%"=="full" goto :full
if "%CMD%"=="help" goto :help

echo %RED%Unknown command: %CMD%%RESET%
goto :help

:: ─────────────────────────────────────────────────────────────────
:full
echo %YELLOW%[1/4]%RESET% Uploading Python files...
call :upload_code_silent
if errorlevel 1 goto :error

echo %YELLOW%[2/4]%RESET% Uploading templates...
call :upload_templates_silent
if errorlevel 1 goto :error

echo %YELLOW%[3/4]%RESET% Setting permissions...
ssh -i "%KEY%" %USER%@%SERVER% "chown -R densewealth:densewealth %REMOTE_DIR%/*.py %REMOTE_DIR%/templates/* 2>/dev/null"

echo %YELLOW%[4/4]%RESET% Restarting services...
call :restart_silent
if errorlevel 1 goto :error

echo.
echo %GREEN%Deploy complete!%RESET%
echo.
call :status_silent
goto :end

:: ─────────────────────────────────────────────────────────────────
:upload_code
echo %YELLOW%Uploading Python files...%RESET%
scp -i "%KEY%" "%LOCAL_DIR%*.py" %USER%@%SERVER%:%REMOTE_DIR%/
if errorlevel 1 goto :error
ssh -i "%KEY%" %USER%@%SERVER% "chown -R densewealth:densewealth %REMOTE_DIR%/*.py"
echo %GREEN%Done! Run 'deploy restart' to apply changes.%RESET%
goto :end

:upload_code_silent
scp -i "%KEY%" -q "%LOCAL_DIR%*.py" %USER%@%SERVER%:%REMOTE_DIR%/ 2>nul
exit /b %errorlevel%

:upload_templates_silent
scp -i "%KEY%" -q "%LOCAL_DIR%templates\*" %USER%@%SERVER%:%REMOTE_DIR%/templates/ 2>nul
exit /b %errorlevel%

:: ─────────────────────────────────────────────────────────────────
:upload_env
echo %YELLOW%Uploading .env...%RESET%
scp -i "%KEY%" "%LOCAL_DIR%.env" %USER%@%SERVER%:%REMOTE_DIR%/.env
if errorlevel 1 goto :error
ssh -i "%KEY%" %USER%@%SERVER% "chown densewealth:densewealth %REMOTE_DIR%/.env && chmod 600 %REMOTE_DIR%/.env"
echo %GREEN%Done! Run 'deploy restart' to apply changes.%RESET%
goto :end

:: ─────────────────────────────────────────────────────────────────
:restart
echo %YELLOW%Restarting services...%RESET%
call :restart_silent
echo %GREEN%Services restarted!%RESET%
echo.
call :status_silent
goto :end

:restart_silent
ssh -i "%KEY%" %USER%@%SERVER% "systemctl restart densewealth && pkill -f 'python web.py' 2>/dev/null; sleep 1; cd %REMOTE_DIR% && nohup %REMOTE_DIR%/venv/bin/python web.py --host 0.0.0.0 > /var/log/densewealth-web.log 2>&1 &"
exit /b %errorlevel%

:: ─────────────────────────────────────────────────────────────────
:status
call :status_silent
goto :end

:status_silent
echo.
echo %CYAN%─── Service Status ───%RESET%
ssh -i "%KEY%" %USER%@%SERVER% "systemctl is-active densewealth && echo 'Bot: RUNNING' || echo 'Bot: STOPPED'"
ssh -i "%KEY%" %USER%@%SERVER% "ss -tlnp | grep -q 8050 && echo 'Dashboard: RUNNING (http://%SERVER%:8050)' || echo 'Dashboard: STOPPED'"
echo.
echo %CYAN%─── Recent Activity ───%RESET%
ssh -i "%KEY%" %USER%@%SERVER% "journalctl -u densewealth --since '2 min ago' --no-pager 2>/dev/null | tail -5"
exit /b 0

:: ─────────────────────────────────────────────────────────────────
:logs
echo %YELLOW%Tailing logs (Ctrl+C to exit)...%RESET%
ssh -i "%KEY%" %USER%@%SERVER% "journalctl -u densewealth -f"
goto :end

:: ─────────────────────────────────────────────────────────────────
:ssh
echo %YELLOW%Opening SSH session...%RESET%
ssh -i "%KEY%" %USER%@%SERVER%
goto :end

:: ─────────────────────────────────────────────────────────────────
:help
echo.
echo %CYAN%Usage:%RESET% deploy.bat [command]
echo.
echo %CYAN%Commands:%RESET%
echo   (no args)  Full deploy (upload code + restart)
echo   code       Upload Python files only
echo   env        Upload .env config only
echo   restart    Restart bot and dashboard
echo   status     Check if services are running
echo   logs       Tail live bot logs
echo   ssh        Open SSH session to server
echo   help       Show this help
echo.
echo %CYAN%Examples:%RESET%
echo   deploy.bat           # Full deploy
echo   deploy.bat code      # Just upload code
echo   deploy.bat restart   # Just restart
echo   deploy.bat logs      # Watch logs
echo.
goto :end

:: ─────────────────────────────────────────────────────────────────
:error
echo.
echo %RED%ERROR: Command failed!%RESET%
echo Check SSH connection: ssh -i "%KEY%" %USER%@%SERVER%
exit /b 1

:end
echo.
