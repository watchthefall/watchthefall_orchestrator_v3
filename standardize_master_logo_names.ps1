# Phase 2: Standardize logo naming to {BrandName}_logo.png
# Target: C:\Users\Jamie\OneDrive\Desktop\WTF_MASTER_ASSETS\Branding\Logos\Circle

$circleLogosDir = "C:\Users\Jamie\OneDrive\Desktop\WTF_MASTER_ASSETS\Branding\Logos\Circle"

Write-Host "=== PHASE 2: LOGO NAMING STANDARDIZATION ===" -ForegroundColor Cyan
Write-Host "Target: $circleLogosDir" -ForegroundColor Yellow
Write-Host ""

# Mapping: Current filename → Standard {BrandName}_logo.png
$renameMap = @{
    "BritainWTF logo.png" = "BritainWTF_logo.png"
    "canada.png" = "CanadaWTF_logo.png"
    "england-wtf-logo_1.png" = "EnglandWTF_logo.png"
    "EnglandWTF Logo.png" = "EnglandWTF_logo.png"
    "EuropeWTF Logo.png" = "EuropeWTF_logo.png"
    "France WTF Logo.png" = "FranceWTF_logo.png"
    "ireland-wtf-logo_1.png" = "IrelandWTF_logo.png"
    "IrelandWTF Logo.png" = "IrelandWTF_logo.png"
    "netherlands-wtf-logo_1.png" = "NetherlandsWTF_logo.png"
    "Northern Ireland WTF Logo.png" = "NorthernIrelandWTF_logo.png"
    "northern-ireland-wtf-logo_1.png" = "NorthernIrelandWTF_logo.png"
    "poland-wtf-logo_1.png" = "PolandWTF_logo.png"
    "PolandWTF Logo.png" = "PolandWTF_logo.png"
    "Scotland WTF Logo.png" = "ScotlandWTF_logo.png"
    "Spain WTF Logo.png" = "SpainWTF_logo.png"
    "spain-wtf-logo_1.png" = "SpainWTF_logo.png"
    "sweden-wtf-logo.png" = "SwedenWTF_logo.png"
    "the-west-wtf.png" = "TheWestWTF_logo.png"
    "wales-wtf-logo_1.png" = "WalesWTF_logo.png"
}

Write-Host "Processing Circle logos..." -ForegroundColor Cyan
Write-Host ""

$renamed = 0
$skipped = 0
$conflicts = @()

foreach ($old in $renameMap.Keys) {
    $new = $renameMap[$old]
    $oldPath = Join-Path $circleLogosDir $old
    $newPath = Join-Path $circleLogosDir $new
    
    if (-not (Test-Path $oldPath)) {
        Write-Host "  SKIP: $old (not found)" -ForegroundColor Yellow
        $skipped++
        continue
    }
    
    # Check for conflicts (if target already exists and is different file)
    if ((Test-Path $newPath) -and ($oldPath -ne $newPath)) {
        Write-Host "  CONFLICT: $old -> $new (target exists)" -ForegroundColor Magenta
        $conflicts += $old
        continue
    }
    
    # Skip if already correct name
    if ($old -eq $new) {
        Write-Host "  OK: $old (already correct)" -ForegroundColor Gray
        continue
    }
    
    try {
        Rename-Item -Path $oldPath -NewName $new -Force
        Write-Host "  RENAMED: $old -> $new" -ForegroundColor Green
        $renamed++
    } catch {
        Write-Host "  ERROR: $old - $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== PHASE 2 SUMMARY ===" -ForegroundColor Cyan
Write-Host "Renamed: $renamed" -ForegroundColor Green
Write-Host "Skipped: $skipped" -ForegroundColor Yellow
Write-Host "Conflicts: $($conflicts.Count)" -ForegroundColor $(if ($conflicts.Count -gt 0) { "Red" } else { "Green" })

if ($conflicts.Count -gt 0) {
    Write-Host ""
    Write-Host "Files with naming conflicts:" -ForegroundColor Red
    $conflicts | ForEach-Object { Write-Host "  - $_" }
    Write-Host ""
    Write-Host "Manual resolution required for conflicts" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== PHASE 2 COMPLETE ===" -ForegroundColor Cyan
