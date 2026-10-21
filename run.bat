@echo off
rem Launch the workbench inside the Ubuntu WSL distro, from this repo root.
setlocal
cd /d "%~dp0"
wsl.exe -d Ubuntu -u root -- bash -lc "cd \"$(wslpath '%CD%')\" && PORT=8000 bash run.sh"
