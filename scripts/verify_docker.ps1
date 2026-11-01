<#
Builds and starts the ClearClaim Docker stack, waits for health, smoke-tests the
app THROUGH the nginx frontend (SPA routes + API proxy + legacy API paths), and
tears it down. Requires Docker Desktop to be running.

    powershell -ExecutionPolicy Bypass -File scripts\verify_docker.ps1            # build, test, stop
    powershell -ExecutionPolicy Bypass -File scripts\verify_docker.ps1 -KeepRunning

A random database password is generated for the run and passed only through the
process environment; it is never written to disk or printed.
#>
param([switch]$KeepRunning, [int]$Port = 8080)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "Docker engine is not running. Start Docker Desktop and wait until it says 'Engine running'." }

if (-not $env:POSTGRES_PASSWORD) {
  $env:POSTGRES_PASSWORD = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
}
$env:WEB_PORT = "$Port"
$env:CORS_ORIGINS = "http://localhost:$Port"
$project = "clearclaim_verify"

function Check($name, $url, $expect) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -Headers @{ Accept = $expect } -TimeoutSec 30
    $ok = $r.StatusCode -eq 200
    Write-Host ("{0,-46} {1}  {2}" -f $name, $r.StatusCode, ($(if ($ok) { "OK" } else { "FAIL" })))
    return $r
  } catch { Write-Host ("{0,-46} FAIL  {1}" -f $name, $_.Exception.Message); $script:failed = $true }
}

$failed = $false
try {
  docker compose -p $project up --build -d
  Write-Host "Waiting for the backend to become healthy (first start seeds rules and processes 200 demo cases)..."
  $deadline = (Get-Date).AddMinutes(6)
  do {
    Start-Sleep -Seconds 5
    $state = docker inspect -f "{{.State.Health.Status}}" (docker compose -p $project ps -q backend)
  } until ($state -eq "healthy" -or (Get-Date) -gt $deadline)
  if ($state -ne "healthy") { docker compose -p $project logs backend --tail 80; throw "backend did not become healthy" }

  $base = "http://localhost:$Port"
  $h = Check "API health via nginx" "$base/api/v1/health" "application/json"
  if ($h) { Write-Host "   $($h.Content)" }
  $o = Check "Overview data via nginx" "$base/api/v1/overview" "application/json"
  if ($o) { $j = $o.Content | ConvertFrom-Json; Write-Host "   cases: $($j.total_cases)"; if ($j.total_cases -lt 1) { $failed = $true } }
  $spa = Check "SPA route /claims (HTML)" "$base/claims" "text/html"
  if ($spa -and $spa.Content -notmatch '<div id="root">') { Write-Host "   /claims did not return the SPA"; $failed = $true }
  Check "SPA route /policies/list (HTML)" "$base/policies/list" "text/html" | Out-Null
  $pol = Check "Legacy API /policies (JSON)" "$base/policies" "application/json"
  if ($pol -and $pol.Content.TrimStart()[0] -ne '[') { Write-Host "   /policies did not return JSON"; $failed = $true }
  Check "Case detail via nginx" "$base/api/v1/cases/1" "application/json" | Out-Null
  $gt = docker compose -p $project exec -T backend sh -c "ls /app/data/synthetic/demo/ground_truth 2>/dev/null | wc -l"
  if ([int]$gt -ne 0) { Write-Host "ground truth present in image: FAIL"; $failed = $true } else { Write-Host ("{0,-46} OK" -f "Ground truth absent from backend image") }
  docker compose -p $project restart backend | Out-Null
  Start-Sleep -Seconds 20
  $o2 = Check "Persistence after backend restart" "$base/api/v1/overview" "application/json"
  if ($o2 -and $o -and (($o2.Content | ConvertFrom-Json).total_cases -ne ($o.Content | ConvertFrom-Json).total_cases)) { $failed = $true }
}
finally {
  if (-not $KeepRunning) { docker compose -p $project down -v | Out-Null; Write-Host "Stack stopped and volume removed." }
  else { Write-Host "Stack left running at http://localhost:$Port  (stop: docker compose -p $project down -v)" }
}
if ($failed) { Write-Error "Docker verification FAILED" } else { Write-Host "`nDocker verification PASSED" }
