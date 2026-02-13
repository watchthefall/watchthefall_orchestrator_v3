# Validate brand coverage: ensure every brand has matching watermark + logo
# Pattern check: {Brand}_watermark.png and {Brand}_logo.png

$brandingRoot = "WTF_MASTER_ASSETS\Branding"
$logosDir = Join-Path $brandingRoot "Logos\Circle"
$watermarksDir = Join-Path $brandingRoot "Watermarks"

Write-Host "=== BRAND ASSET COVERAGE VALIDATION ===" -ForegroundColor Cyan
Write-Host ""

# Load brand_config.json to get official brand list
$brandConfigPath = "portal\brand_config.json"
if (-not (Test-Path $brandConfigPath)) {
    Write-Host "ERROR: brand_config.json not found at $brandConfigPath" -ForegroundColor Red
    exit 1
}

$brandConfig = Get-Content $brandConfigPath -Raw | ConvertFrom-Json
$brands = $brandConfig.PSObject.Properties.Name

Write-Host "Found $($brands.Count) brands in brand_config.json" -ForegroundColor Green
Write-Host ""

# Check logo coverage
Write-Host "=== LOGO COVERAGE (Logos/Circle) ===" -ForegroundColor Yellow
$missingLogos = @()
foreach ($brand in $brands) {
    $logoPath = Join-Path $logosDir "${brand}_logo.png"
    if (Test-Path $logoPath) {
        Write-Host "  OK: ${brand}_logo.png" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: ${brand}_logo.png" -ForegroundColor Red
        $missingLogos += $brand
    }
}

Write-Host ""

# Check watermark coverage (Vertical_HD as reference orientation)
Write-Host "=== WATERMARK COVERAGE (Vertical_HD) ===" -ForegroundColor Yellow
$watermarkVerticalDir = Join-Path $watermarksDir "Vertical_HD"
$missingWatermarks = @()
foreach ($brand in $brands) {
    # Try common patterns
    $patterns = @(
        "${brand}_watermark.png",
        "$($brand.Replace('WTF', ''))_watermark.png",
        "$($brand.Replace('WTF', '').ToLower())_watermark.png"
    )
    
    $found = $false
    foreach ($pattern in $patterns) {
        $watermarkPath = Join-Path $watermarkVerticalDir $pattern
        if (Test-Path $watermarkPath) {
            Write-Host "  OK: $pattern" -ForegroundColor Green
            $found = $true
            break
        }
    }
    
    if (-not $found) {
        Write-Host "  MISSING: ${brand}_watermark.png (tried variants)" -ForegroundColor Red
        $missingWatermarks += $brand
    }
}

Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor Cyan
Write-Host "Total brands: $($brands.Count)"
Write-Host "Missing logos: $($missingLogos.Count)" -ForegroundColor $(if ($missingLogos.Count -eq 0) { "Green" } else { "Red" })
Write-Host "Missing watermarks: $($missingWatermarks.Count)" -ForegroundColor $(if ($missingWatermarks.Count -eq 0) { "Green" } else { "Red" })

if ($missingLogos.Count -gt 0) {
    Write-Host ""
    Write-Host "Brands without logos:" -ForegroundColor Red
    $missingLogos | ForEach-Object { Write-Host "  - $_" }
}

if ($missingWatermarks.Count -gt 0) {
    Write-Host ""
    Write-Host "Brands without watermarks:" -ForegroundColor Red
    $missingWatermarks | ForEach-Object { Write-Host "  - $_" }
}

Write-Host ""
if ($missingLogos.Count -eq 0 -and $missingWatermarks.Count -eq 0) {
    Write-Host "VALIDATION PASSED: Full brand coverage" -ForegroundColor Green
} else {
    Write-Host "VALIDATION FAILED: Missing assets" -ForegroundColor Red
}
