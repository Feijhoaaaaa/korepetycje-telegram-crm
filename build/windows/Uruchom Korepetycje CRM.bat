@echo off
chcp 65001 >nul
cd /d "%~dp0"
KorepetycjeCRM.exe
if errorlevel 1 (
  echo.
  echo Program zakonczyl sie bledem. Nacisnij dowolny klawisz.
  pause >nul
)
