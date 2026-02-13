# Convert all webp/jpg logos to PNG and delete originals
# Requires: ImageMagick (magick.exe) or FFmpeg (ffmpeg.exe)

$logosDir = "WTF_MASTER_ASSETS\Branding\Logos\Circle"

Write-Host "=== LOGO FORMAT CONVERSION ===" -ForegroundColor Cyan
Write-Host ""

# Check for conversion tool
$hasMagick = Get-Command magick -ErrorAction SilentlyContinue
$hasFFmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue

if (-not $hasMagick -and -not $hasFFmpeg) {
    Write-Host "ERROR: Neither ImageMagick (magick) nor FFmpeg found" -ForegroundColor Red
    Write-Host "Install ImageMagick: https://imagemagick.org/script/download.php" -ForegroundColor Yellow
    Write-Host "Or ensure FFmpeg is in PATH" -ForegroundColor Yellow
    exit 1
}

$converter = if ($hasMagick) { "magick" } else { "ffmpeg" }
Write-Host "Using converter: $converter" -ForegroundColor Green
Write-Host ""

# Get all non-PNG logos
$nonPngLogos = Get-ChildItem $logosDir -File | Where-Object { $_.Extension -ne ".png" }

if ($nonPngLogos.Count -eq 0) {
    Write-Host "No conversion needed - all logos are already PNG" -ForegroundColor Green
    exit 0
}

Write-Host "Found $($nonPngLogos.Count) logos to convert:" -ForegroundColor Yellow
$nonPngLogos | ForEach-Object { Write-Host "  - $($_.Name)" }
Write-Host ""

foreach ($file in $nonPngLogos) {
    $inputPath = $file.FullName
    $outputPath = [System.IO.Path]::ChangeExtension($inputPath, ".png")
    
    Write-Host "Converting: $($file.Name) -> $([System.IO.Path]::GetFileName($outputPath))" -ForegroundColor Cyan
    
    try {
        if ($converter -eq "magick") {
            # ImageMagick conversion
            & magick convert "$inputPath" "$outputPath" 2>&1 | Out-Null
        } else {
            # FFmpeg conversion
            & ffmpeg -y -i "$inputPath" -vf "format=rgba" "$outputPath" 2>&1 | Out-Null
        }
        
        if (Test-Path $outputPath) {
            Write-Host "  OK: Created $([System.IO.Path]::GetFileName($outputPath))" -ForegroundColor Green
            Remove-Item $inputPath -Force
            Write-Host "  OK: Deleted $($file.Name)" -ForegroundColor Green
        } else {
            Write-Host "  ERROR: Conversion failed" -ForegroundColor Red
        }
    } catch {
        Write-Host "  ERROR: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== CONVERSION COMPLETE ===" -ForegroundColor Cyan
