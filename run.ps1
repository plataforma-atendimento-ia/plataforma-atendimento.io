Set-Location $PSScriptRoot
# Liga também a ponte do WhatsApp por QR Code (se houver Node.js e a pasta bridge instalada).
$bridgeUp = $false
try { $bridgeUp = (Invoke-WebRequest "http://127.0.0.1:8788/health" -UseBasicParsing -TimeoutSec 1).StatusCode -eq 200 } catch {}
if (-not $bridgeUp -and (Get-Command node -ErrorAction SilentlyContinue) -and (Test-Path "bridge\node_modules")) {
  Start-Process -FilePath "node" -ArgumentList "index.js" -WorkingDirectory (Join-Path $PSScriptRoot "bridge") -WindowStyle Hidden `
    -RedirectStandardOutput "$env:TEMP\plat-bridge.log" -RedirectStandardError "$env:TEMP\plat-bridge-err.log"
  Write-Host "Ponte do WhatsApp (QR) iniciada. Log: $env:TEMP\plat-bridge.log"
}
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
