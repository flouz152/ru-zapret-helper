$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

python -m pip install --upgrade pyinstaller
python -m pip install --upgrade pywebview
python -m pip install --upgrade winotify
python -m PyInstaller --noconfirm --clean --onefile --console --uac-admin --name ru-helper --icon=ruhp.ico --collect-all webview --collect-all winotify --hidden-import webview.platforms.mshtml --hidden-import webview.platforms.winforms --hidden-import ru_helper_gui --hidden-import project_adapters --hidden-import winotify ru_helper.py

Write-Host "Сборка завершена: $projectRoot\dist\ru-helper.exe"
