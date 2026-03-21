param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "[Build] Installing PyInstaller..."
& $Python -m pip install --upgrade pyinstaller

Write-Host "[Build] Building SpotifyAIMPBridge.exe..."
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name SpotifyAIMPBridge `
    main.py

Write-Host ""
Write-Host "[Build] Done."
Write-Host "        EXE: bridge\dist\SpotifyAIMPBridge.exe"
