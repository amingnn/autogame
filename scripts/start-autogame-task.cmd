@echo off
setlocal

schtasks.exe /Run /TN "\AutoGame"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo Failed to start the AutoGame scheduled task.
    echo Make sure the task named AutoGame exists in Windows Task Scheduler.
    pause
    exit /b %EXIT_CODE%
)

echo AutoGame scheduled task started.
timeout /t 2 /nobreak >nul
exit /b 0
