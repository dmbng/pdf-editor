@echo off
rem Double-click launcher for PDF Editor Studio.
rem Starts the editor with pythonw so no console window appears.

setlocal
cd /d "%~dp0"

set "PYW=pythonw.exe"
where %PYW% >nul 2>&1 || set "PYW=python.exe"

start "PDF Editor" "%PYW%" -m pdf_editor %*
