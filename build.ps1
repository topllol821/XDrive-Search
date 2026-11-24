$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -3.14 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $python -m PyInstaller --noconfirm --onefile --noconsole --name XDriveSearch --icon x_search.ico --collect-all tkinterdnd2 --collect-all pypdfium2 --collect-all pypdfium2_raw --hidden-import win32clipboard --hidden-import win32con --hidden-import openpyxl --hidden-import send2trash --exclude-module numpy XDriveSearch.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Output 'Build output: dist\XDriveSearch.exe'
