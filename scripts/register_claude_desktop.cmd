@echo off
REM Register Altium Agent in the Claude desktop app (backs up the config first).
"%~dp0..\.venv\Scripts\python.exe" "%~dp0register_claude_desktop.py"
pause
