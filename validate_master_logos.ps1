# Phase 3: Validate logo coverage against backend brand config
# Compare master assets vs backend registered brands

$masterCircleDir = "C:\Users\Jamie\OneDrive\Desktop\WTF_MASTER_ASSETS\Branding\Logos\Circle"
$brandConfigPath = "portal\brand_config.json"

Write-Host "=== PHASE 3: LOGO COVERAGE VALIDATION ===" -ForegroundColor Cyan
Write-Host ""

# Load backend brand config
if (-not (Test-Path $brandConfigPath)) {
    Write-Host "ERROR: brand_config.json not found" -ForegroundColor Red
    exit 1
}

$brandConfig = Get-Content $brandConfigPath -Raw | ConvertFrom-Json
$registeredBrands = $brandConfig.PSObject.Properties.Name | Sort-Object

Write-Host "Backend registered brands: $($registeredBrands.Count)" -ForegroundColor Yellow
Write-Host ""

# Get all logos in master Circle folder
$masterLogos = Get-ChildItem $masterCircleDir -Filter "*.png" | Where-Object {
    $_.Name -match "^(.+)_logo\.png$"
} | ForEach-Object {
    $_.Name -replace "_logo\.png$", ""
}

$masterLogos = $masterLogos | Sort-Object -Unique

Write-Host "Master asset logos: $($masterLogos.Count)" -ForegroundColor Yellow
Write-Host ""

# Compare coverage
$present = @()
$missing = @()
$extra = @()

foreach ($brand in $registeredBrands) {
    $logoFile = "${brand}_logo.png"
    $logoPath = Join-Path $masterCircleDir $logoFile
    
    if (Test-Path $logoPath) {
        $present += $brand
        Write-Host "  OK: $logoFile" -ForegroundColor Green
    } else {
        $missing += $brand
        Write-Host "  MISSING: $logoFile" -ForegroundColor Red
    }
}

# Find extra logos not in backend config
foreach ($logo in $masterLogos) {
    if ($logo -notin $registeredBrands) {
        $extra += $logo
    }
}

Write-Host ""
Write-Host "=== COVERAGE REPORT ===" -ForegroundColor Cyan
Write-Host "Registered brands: $($registeredBrands.Count)" -ForegroundColor White
Write-Host "Present logos: $($present.Count)" -ForegroundColor Green
Write-Host "Missing logos: $($missing.Count)" -ForegroundColor $(if ($missing.Count -eq 0) { "Green" } else { "Red" })
Write-Host "Extra unused logos: $($extra.Count)" -ForegroundColor $(if ($extra.Count -eq 0) { "Green" } else { "Yellow" })

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Brands without logos:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - ${_}_logo.png" }
}

if ($extra.Count -gt 0) {
    Write-Host ""
    Write-Host "Extra logos (not in backend config):" -ForegroundColor Yellow
    $extra | ForEach-Object { Write-Host "  - ${_}_logo.png" }
}

Write-Host ""
Write-Host "=== FINAL INVENTORY ===" -ForegroundColor Cyan
Write-Host "All logos in master Circle folder:" -ForegroundColor White
Get-ChildItem $masterCircleDir -Filter "*.png" | Sort-Object Name | ForEach-Object {
    Write-Host "  $($_.Name)" -ForegroundColor Gray
}

Write-Host ""
if ($missing.Count -eq 0) {
    Write-Host "VALIDATION PASSED: Full backend coverage" -ForegroundColor Green
} else {
    Write-Host "VALIDATION INCOMPLETE: $($missing.Count) missing logos" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== PHASE 3 COMPLETE ===" -ForegroundColor Cyan
