$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path .env) { throw ".env exists; edit it without replacing your secrets." }
function New-HexSecret {
  $bytes = New-Object byte[] 32
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  $rng.GetBytes($bytes); $rng.Dispose()
  return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}
Write-Host "Choose your dashboard password in the Caddy prompt. Copy its bcrypt output."
docker run --rm -it caddy:2-alpine@sha256:953131cfea8e12bfe1c631a36308e9660e4389f0c3dfb3be957044d3ac92d446 caddy hash-password
if ($LASTEXITCODE -ne 0) { throw "Password hashing failed." }
$hash = Read-Host "Paste the bcrypt hash (starts with `$2)"
if (-not $hash.StartsWith('$2')) { throw "Expected bcrypt hash." }
$content = (Get-Content .env.example -Raw).Replace("replace-with-random-hex", "PLACEHOLDER")
$content = $content.Replace("POSTGRES_PASSWORD=PLACEHOLDER", "POSTGRES_PASSWORD=$(New-HexSecret)")
$content = $content.Replace("INTERNAL_API_TOKEN=PLACEHOLDER", "INTERNAL_API_TOKEN=$(New-HexSecret)")
$content = $content.Replace("replace-with-caddy-bcrypt-hash", $hash)
[IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $content)
Write-Host "Created .env. Run docker compose up --build -d"
