@echo off
rem Double-click launcher for the OT Range control panel on Windows
rem 10/11, when this repo lives inside WSL2 (see README.md's "Windows"
rem section for why: Docker Desktop and this range's router container
rem both need a real Linux kernel underneath regardless of host OS).
rem
rem Works when this .bat is reached through Explorer's WSL view
rem (\\wsl.localhost\<distro>\...\ot-range\start-panel.bat) or a
rem shortcut pointing there. Requires WSL2 installed with a default
rem distro, and Docker Desktop's "WSL integration" enabled for it.
rem
rem Best-effort: the underlying start-panel.sh is fully tested (it's
rem identical to native Linux inside WSL); this specific .bat wrapper
rem could not be tested on a real Windows machine in this project's
rem development environment.
setlocal
for /f "delims=" %%i in ('wsl wslpath "%~dp0"') do set WSL_REPO_PATH=%%i
wsl bash -lc "cd '%WSL_REPO_PATH%' && ./start-panel.sh"
pause
