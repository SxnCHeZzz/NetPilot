@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title NetPilot - Windows build
where py >nul 2>nul
if errorlevel 1 (
 echo [ERROR] Install Python 3.12+ to build this project.
 pause
 exit /b 1
)
py -m pip install -r requirements.txt
if errorlevel 1 (echo [ERROR] Dependencies failed & pause & exit /b 1)
py -m unittest discover -s tests -v
if errorlevel 1 (echo [ERROR] Tests failed & pause & exit /b 1)
py -m PyInstaller --clean --noconfirm --onedir --windowed --name NetPilot --icon assets\netpilot.ico --add-data "assets;assets" --collect-all proxy --collect-all utils --paths src src\app.py
if errorlevel 1 (echo [ERROR] PyInstaller failed & pause & exit /b 1)
if not exist "dist\NetPilot\NetPilot.exe" (echo [ERROR] EXE not found & pause & exit /b 1)
echo.
echo [OK] EXE: dist\NetPilot\NetPilot.exe
where ISCC >nul 2>nul
if not errorlevel 1 (
 ISCC installer\NetPilot.iss
) else if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
 "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" installer\NetPilot.iss
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
 "%ProgramFiles%\Inno Setup 6\ISCC.exe" installer\NetPilot.iss
) else (
 echo [INFO] Inno Setup 6 not found. Install it to produce NetPilotSetup.exe.
)
if exist "dist\installer\NetPilotSetup.exe" echo [OK] Installer: dist\installer\NetPilotSetup.exe
powershell -NoProfile -Command "if(Test-Path 'dist\NetPilot-Portable.zip'){Remove-Item 'dist\NetPilot-Portable.zip' -Force}; Compress-Archive -LiteralPath 'dist\NetPilot','LICENSE','THIRD_PARTY_NOTICES.txt' -DestinationPath 'dist\NetPilot-Portable.zip' -CompressionLevel Optimal"
if exist "dist\NetPilot-Portable.zip" echo [OK] Portable: dist\NetPilot-Portable.zip
echo.
pause
