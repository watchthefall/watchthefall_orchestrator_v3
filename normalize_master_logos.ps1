# Phase 1: Convert all webp/jpg logos to PNG in master assets
# Target: C:\Users\Jamie\OneDrive\Desktop\WTF_MASTER_ASSETS\Branding\Logos

$masterLogosRoot = "C:\Users\Jamie\OneDrive\Desktop\WTF_MASTER_ASSETS\Branding\Logos"

Write-Host "=== PHASE 1: LOGO FORMAT NORMALIZATION ===" -ForegroundColor Cyan
Write-Host "Target: $masterLogosRoot" -ForegroundColor Yellow
Write-Host ""

# Check for FFmpeg
$hasFFmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $hasFFmpeg) {
    Write-Host "ERROR: FFmpeg not found in PATH" -ForegroundColor Red
    exit 1
}

Write-Host "Using converter: ffmpeg" -ForegroundColor Green
Write-Host ""

# Get all subdirectories (Circle, Square, Primary_Logos)
$subdirs = Get-ChildItem $masterLogosRoot -Directory

foreach ($subdir in $subdirs) {
    Write-Host "Processing: $($subdir.Name)" -ForegroundColor Cyan
    
    # Get all non-PNG logos
    $nonPngLogos = Get-ChildItem $subdir.FullName -File | Where-Object { 
        $_.Extension -in @(".webp", ".jpg", ".jpeg") 
    }
    
    if ($nonPngLogos.Count -eq 0) {
        Write-Host "  No conversion needed" -ForegroundColor Green
        continue
    }
    
    Write-Host "  Found $($nonPngLogos.Count) logos to convert" -ForegroundColor Yellow
    
    foreach ($file in $nonPngLogos) {
        $inputPath = $file.FullName
        $outputPath = [System.IO.Path]::ChangeExtension($inputPath, ".png")
        
        Write-Host "    Converting: $($file.Name)" -ForegroundColor Gray
        
        try {
            # FFmpeg conversion preserving alpha
            & ffmpeg -y -i "$inputPath" -vf "format=rgba" "$outputPath" 2>&1 | Out-Null
            
            if (Test-Path $outputPath) {
                $outputSize = (Get-Item $outputPath).Length / 1KB
                Write-Host "      OK: Created $([System.IO.Path]::GetFileName($outputPath)) ($([math]::Round($outputSize, 1))KB)" -ForegroundColor Green
                Remove-Item $inputPath -Force
                Write-Host "      OK: Deleted $($file.Name)" -ForegroundColor Green
            } else {
                Write-Host "      ERROR: Conversion failed" -ForegroundColor Red
            }
        } catch {
            Write-Host "      ERROR: $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    
    Write-Host ""
}

Write-Host "=== PHASE 1 COMPLETE ===" -ForegroundColor Cyan
