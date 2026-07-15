param (
    [switch]$Down = $false
)

if ($Down) {
    Write-Host "Tearing down LAI containers and volumes..." -ForegroundColor Cyan
    docker compose down -v
    exit 0
}

Write-Host "Starting LAI containers..." -ForegroundColor Cyan
Set-Location $PSScriptRoot
docker compose up -d

Write-Host "Waiting for PostgreSQL to become ready..." -ForegroundColor Yellow
$maxAttempts = 30
$attempt = 0
$ready = $false

while ($attempt -lt $maxAttempts -and -not $ready) {
    $status = docker exec lai_postgres_main pg_isready -U lai_user -d lai_db 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
    } else {
        Start-Sleep -Seconds 2
        $attempt++
        Write-Host "." -NoNewline
    }
}
Write-Host ""

if (-not $ready) {
    Write-Host "Error: PostgreSQL did not become ready in time." -ForegroundColor Red
    exit 1
}

Write-Host "PostgreSQL is ready. Ensuring database exists and running migrations..." -ForegroundColor Green

# Create database if it doesn't exist (ignore errors if it already exists)
docker exec -i lai_postgres_main psql -U lai_user -d postgres -c "CREATE DATABASE lai_db;" 2>$null

$sqlPath = Join-Path $PSScriptRoot "scripts\db\migrations\001_corpus_pgvector.sql"
Get-Content -Path $sqlPath | docker exec -i lai_postgres_main psql -U lai_user -d lai_db

Write-Host "Setup complete!" -ForegroundColor Green
