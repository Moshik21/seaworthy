@echo off
rem Launcher for Windows (cmd or PowerShell): seaworthy <command>
setlocal
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
python "%~dp0seaworthy" %*
exit /b %ERRORLEVEL%
