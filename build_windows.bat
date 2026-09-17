@echo off
setlocal
cd /d "%~dp0"
py -3.11 -m pip install --upgrade pyinstaller -r requirements.txt
py -3.11 -m PyInstaller --noconfirm --clean --onefile --windowed --name "YoKaiWatchBank" ^
  --add-data "data;data" --add-data "vendor/yw_save;vendor/yw_save" ^
  --paths "vendor/yw_save" --hidden-import yw_save --hidden-import gamefix.gansohonke ^
  --hidden-import gamefix.shinuchi --hidden-import gamefix.yw3 app.py
echo Built dist\YoKaiWatchBank.exe
pause
