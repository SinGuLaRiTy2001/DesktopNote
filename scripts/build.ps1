# Build a standalone DesktopNote.exe with PyInstaller.
# Usage (from the repository root): powershell -ExecutionPolicy Bypass -File scripts\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller

# Render the app icon to .ico from the built-in SVG.
.\.venv\Scripts\python -c "import sys; sys.path.insert(0, '.'); from PySide6.QtWidgets import QApplication; a = QApplication([]); from desktopnote import icons; icons.pixmap('app', '#000', 256, 1.0).save('build_icon.ico')"

# QtQml and QtQuick are not used; excluding them keeps the executable smaller.
.\.venv\Scripts\pyinstaller --noconfirm --clean --windowed --onefile `
    --name DesktopNote --icon build_icon.ico `
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    DesktopNote.pyw

Remove-Item build_icon.ico -ErrorAction SilentlyContinue
Write-Host "Built: dist\DesktopNote.exe"
