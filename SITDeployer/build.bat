@echo off
cd /d "%~dp0"

python -m pip install --upgrade pyinstaller

python -m PyInstaller --noconfirm --onefile --windowed --name SITCompareLauncher sit_compare_launcher.py

echo.
echo Done. EXE: %~dp0dist\SITCompareLauncher.exe
pause
