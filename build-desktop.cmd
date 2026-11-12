@echo off
chcp 65001 >nul
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-desktop.ps1" %*
exit /b %errorlevel%
