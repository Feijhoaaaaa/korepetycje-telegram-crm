param([switch]$SkipFrontend)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

Write-Host "== Korepetycje CRM: Windows build ==" -ForegroundColor Cyan

if (-not $SkipFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm nie jest dostępny. Zainstaluj Node.js LTS."
    }
    Push-Location (Join-Path $Root "frontend")
    try {
        npm ci
        npm run build
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
    throw "Brak frontend\dist\index.html. Uruchom build bez -SkipFrontend."
}

$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "Nie znaleziono Python Launcher (py.exe). Zainstaluj Python 3.12 x64."
}

$Venv = Join-Path $Root ".venv-windows"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) {
    py -3.12 -m venv $Venv
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --require-hashes -r (Join-Path $Root "build\windows\requirements.lock")
& $VenvPython -m pip install --no-deps -e "."

$Vendor = Join-Path $Root "vendor\windows"
New-Item -ItemType Directory -Force -Path $Vendor | Out-Null
$Cloudflared = Join-Path $Vendor "cloudflared.exe"
$CloudflaredVersion = "2026.9.1"
$CloudflaredSha256 = "2837888cc0f5d58f15b6dc478376de90b4d3ba5241c7947455d1e0a0df429712"
if (-not (Test-Path $Cloudflared)) {
    Write-Host "Pobieranie cloudflared.exe..."
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/download/$CloudflaredVersion/cloudflared-windows-amd64.exe" -OutFile $Cloudflared
}
$ActualCloudflaredSha256 = (Get-FileHash -Algorithm SHA256 $Cloudflared).Hash.ToLowerInvariant()
if ($ActualCloudflaredSha256 -ne $CloudflaredSha256) {
    throw "Nieprawidłowa suma SHA-256 cloudflared.exe. Oczekiwano $CloudflaredSha256, otrzymano $ActualCloudflaredSha256."
}

& $VenvPython -m PyInstaller --noconfirm --clean (Join-Path $Root "build\windows\KorepetycjeCRM.spec")

$Output = Join-Path $Root "dist\KorepetycjeCRM"
Copy-Item (Join-Path $Root ".env.example") (Join-Path $Output ".env.example") -Force
Copy-Item (Join-Path $Root "build\windows\README-WINDOWS.txt") (Join-Path $Output "README-WINDOWS.txt") -Force
Copy-Item (Join-Path $Root "build\windows\Uruchom Korepetycje CRM.bat") $Output -Force
Copy-Item (Join-Path $Root "build\windows\Utworz skrot na pulpicie.ps1") $Output -Force
Copy-Item (Join-Path $Root "build\windows\Utworz skrot na pulpicie.cmd") $Output -Force

Remove-Item (Join-Path $Output ".env") -Force -ErrorAction SilentlyContinue

$Zip = Join-Path $Root "dist\KorepetycjeCRM-Windows-x64.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Compress-Archive -Path (Join-Path $Output "*") -DestinationPath $Zip -CompressionLevel Optimal

Write-Host "Gotowe: $Zip" -ForegroundColor Green
