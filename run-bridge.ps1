# Liga a ponte NÃO OFICIAL do WhatsApp (QR Code). Mantenha esta janela aberta enquanto usar.
Set-Location (Join-Path $PSScriptRoot "bridge")
if (-not (Test-Path "node_modules")) { npm install --no-audit --no-fund }
node index.js
