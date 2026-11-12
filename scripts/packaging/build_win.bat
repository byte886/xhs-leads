@echo off
rem XHSLeads Windows one-click packaging (same as build_win.ps1)
rem Log: <repo>\build-win.log
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_win.ps1" %*
echo.
echo If BUILD_OK appeared above, the artifact is Desktop\XHSLeads-win64.zip
pause
