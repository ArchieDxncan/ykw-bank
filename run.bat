@echo off
setlocal
cd /d "%~dp0"
py -3.11 -c "import Crypto" 2>nul || py -3.11 -m pip install -r requirements.txt
py -3.11 app.py
if errorlevel 1 pause
