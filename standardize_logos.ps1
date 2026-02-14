# Standardize logo naming to {BrandName}_logo.png
# Run from repo root

$logosDir = "WTF_MASTER_ASSETS\Branding\Logos\Circle"

# Mapping: Current filename → Standard filename
$renameMap = @{
    "BritainWTF logo.png" = "BritainWTF_logo.png"
    "canada.png" = "CanadaWTF_logo.png"
    "england-wtf-logo_1.png" = "EnglandWTF_logo.png"
    "EnglandWTF Logo.webp" = "EnglandWTF_logo.webp"  # temp
    "EuropeWTF Logo.jpg" = "EuropeWTF_logo.jpg"  # temp
    "France WTF Logo.jpg" = "FranceWTF_logo.jpg"  # temp
    "ireland-wtf-logo_1.png" = "IrelandWTF_logo.png"
    "IrelandWTF Logo.webp" = "IrelandWTF_logo.webp"  # temp
    "netherlands-wtf-logo_1.png" = "NetherlandsWTF_logo.png"
    "Northern Ireland WTF Logo.webp" = "NorthernIrelandWTF_logo.webp"  # temp
    "northern-ireland-wtf-logo_1.png" = "NorthernIrelandWTF_logo.png"
    "poland-wtf-logo_1.png" = "PolandWTF_logo.png"
    "PolandWTF Logo.webp" = "PolandWTF_logo.webp"  # temp
    "Scotland WTF Logo.webp" = "ScotlandWTF_logo.webp"  # temp
    "Spain WTF Logo.jpg" = "SpainWTF_logo.jpg"  # temp
    "spain-wtf-logo_1.png" = "SpainWTF_logo.png"
    "sweden-wtf-logo.jpg" = "SwedenWTF_logo.jpg"  # temp
    "the-west-wtf.jpg" = "TheWestWTF_logo.jpg"  # temp
    "wales-wtf-logo_1.png" = "WalesWTF_logo.png"
    # wtf_logo.png stays as fallback if needed
}

Write-Host "=== LOGO STANDARDIZATION ===" -ForegroundColor Cyan
Write-Host ""

foreach ($old in $renameMap.Keys) {
    $new = $renameMap[$old]
    $oldPath = Join-Path $logosDir $old
    $newPath = Join-Path $logosDir $new
    
    if (Test-Path $oldPath) {
        Rename-Item -Path $oldPath -NewName $new -Force
        Write-Host "OK: $old -> $new" -ForegroundColor Green
    } else {
        Write-Host "SKIP: $old (not found)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== PNG CONVERSION NEEDED ===" -ForegroundColor Cyan
Write-Host "The following files need manual conversion to PNG:" -ForegroundColor Yellow
Get-ChildItem $logosDir -Filter "*.webp" | ForEach-Object { Write-Host "  - $($_.Name)" }
Get-ChildItem $logosDir -Filter "*.jpg" | ForEach-Object { Write-Host "  - $($_.Name)" }
Write-Host ""
Write-Host "After conversion, rename to {BrandName}_logo.png pattern" -ForegroundColor Yellow
