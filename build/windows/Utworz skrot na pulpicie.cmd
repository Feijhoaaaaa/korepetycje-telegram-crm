@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Utworz skrot na pulpicie.ps1"
if errorlevel 1 pause
