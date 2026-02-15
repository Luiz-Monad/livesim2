param(
    [switch]$start,
    [switch]$stop,
    [switch]$loop,
    [int]$vodCount = 0
)
# CMAF Ingest Test Script for livesim2
# Requirements: livesim2 and cmaf-ingest-receiver binaries built
#
# Usage:
#   .\test-cmaf-ingest.ps1                           # Run single test with first VOD
#   .\test-cmaf-ingest.ps1 -start                    # Start services and run test
#   .\test-cmaf-ingest.ps1 -start -loop              # Start services and loop through all VODs
#   .\test-cmaf-ingest.ps1 -start -loop -vodCount 3  # Loop through first 3 VODs

$ErrorActionPreference = "Stop"

# Configuration
$livesimPort = 9999
$livesimUrl = "http://localhost:$livesimPort"
$livesimUrlPattern = '/livesim2/([^/]+/[^.]+\.mpd)([&]|$)'
$livesimVodUrl = "$livesimUrl/livesim2/periods_600/continuous_1/"
$livesimIngestUrl = "$livesimUrl/api/cmaf-ingests"
$receiverPort = 6080
$receiverUrl = "http://localhost:$receiverPort"

# Paths - adjust as needed
$projectRoot = "C:/Users/LuizMonad/Desktop/livesim2"
$receiverExe = "$projectRoot/cmd/cmaf-ingest-receiver/cmaf-ingest-receiver.exe"
$storageDir = "$projectRoot/cmd/cmaf-ingest-receiver/storage"
$livesimExe = "$projectRoot/cmd/livesim2/livesim2.exe"
$defaultVods = @("$livesimVodUrl/AU2DY2400085/track.mpd")
$vodRoot = "//?/C:/Users/LuizMonad/Desktop/web/mediaserver-playlistgenerator/radio_test/rtst/dash"

# Ensure storage directory exists
if (!(Test-Path $storageDir)) {
    New-Item -ItemType Directory -Path $storageDir -Force | Out-Null
}

#==============================
#region Helpers
#==============================

# Start in a split pane
function Start-PwshSplit {
    param([scriptblock]$cmd, [hashtable]$vars)
    $fullScript = '&' + $cmd.Ast.Extent.Text
    $vars.GetEnumerator() | ForEach-Object {
        $k = '$' + $_.Key
        $v = "$($_.Value -replace "'","''")"
        $fullScript = $fullScript.Replace($k, $v)
    }
    $scriptBytes = [System.Text.Encoding]::Unicode.GetBytes($fullScript)
    $encodedCmd = [Convert]::ToBase64String($scriptBytes)
    wt -w 0 split-pane -H pwsh -NoExit -NoProfile -EncodedCommand $encodedCmd
}

# Write to the console using a style
function Write-Style {
    param([string]$style, $text)
    $styles = @{
        title    = @{
            color = "Cyan"
            box   = 2
            line  = 0
        }
        subtitle = @{
            color = "Green"
            box   = 1
            line  = 0
        }
        text     = @{
            color = "White"
            box   = 0
            line  = 0
        }
        error    = @{
            color = "Red"
            box   = 0
            line  = 1
        }
    }
    $lines = @{
        2 = "╔═╗║ ║╚═╝"
        1 = "┌─┐│ │└─┘"
    }
    function line {
        param ($line, $text)
        if (-not $line) { return }
        "$($lines[$line][1])" * "$text".Length
    }
    function box {
        param($line, $text)
        if (-not $line) { return $text }
        $l = "$text".Length + 2
        function b($i) { "$($lines[$line][$i])" }
        return `
            "$(b 0)$($(b 1) * $l)$(b 2)`n" + `
            "$(b 3) $(  $text  ) $(b 5)`n" + `
            "$(b 6)$($(b 1) * $l)$(b 8)"
    }
    $ht = $styles[$style]
    Write-Host (line $ht.line $text) -ForegroundColor $ht.color
    Write-Host (box $ht.box $text) -ForegroundColor $ht.color
}

# Start cmaf-ingest-receiver in background
function Start-CmafIngestReceiver {
    Write-Style subtitle "Starting cmaf-ingest-receiver on port $receiverPort..."
    Start-PwshSplit {
        & $receiverExe `
            --storage $storageDir `
            --port $receiverPort `
            --fileserverpath media `
            --loglevel debug
    } @{
        receiverExe  = $receiverExe
        receiverPort = $receiverPort
        storageDir   = $storageDir
    }
}

# Start livesim2 in background
function Start-LiveSim {
    Write-Style subtitle "Starting livesim2 on port $livesimPort..."
    Start-PwshSplit {
        & $livesimExe `
            --port $livesimPort `
            --vodroot $vodRoot `
            --loglevel debug
    } @{
        livesimExe  = $livesimExe
        livesimPort = $livesimPort
        vodRoot     = $vodRoot
    }
}

# Get list of available VODs from livesim2b
function Get-AvailableVods {
    Write-Style subtitle "Fetching available VODs from livesim2..."
    try {
        $html = Invoke-WebRequest -Uri "$livesimUrl/assets" -UseBasicParsing
        $content = $html.Content

        # Parse VOD paths from the HTML - look for hrefs
        $vodPaths = [regex]::Matches($content, 'href="([^"]+)"') | `
            ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique

        $vods = @()
        foreach ($path in $vodPaths) {
            if ($path -match $livesimUrlPattern) {
                $vodUrl = $Matches[1]
                $vods += $vodUrl
            }
        }

        if ($vods.Count -eq 0) {
            Write-Style text "No VODs found, using default test path"
            return $defaultVods
        }

        Write-Style text "Found $($vods.Count) VODs"
        return $vods
    }
    catch {
        Write-Style error "Error fetching VODs: $_"
        return $defaultVods
    }
}

# Create CMAF ingest via API
function Start-CmafIngest {
    param([string]$vodUrl, [string]$channelName)

    Write-Style text "Creating CMAF ingest for $channelName..."

    $body = @{
        destRoot   = "$receiverUrl/upload"
        destName   = $channelName
        livesimURL = $vodUrl
        testNowMS  = 10000
    } | ConvertTo-Json
    Invoke-RestMethod `
        -Uri "$livesimIngestUrl" `
        -Method POST `
        -ContentType "application/json" `
        -Body $body
}

# Step the CMAF ingest via API
function Step-CmafIngest {
    param([string]$id)

    Write-Style text "Stepping CMAF ingest id $id..."

    Invoke-RestMethod `
        -Uri "$livesimIngestUrl/$id/step" `
        -Method GET
}

# Get results of the CMAF ingest via API
function Get-CmafIngestInfo {
    param([string]$id)

    Write-Style text "Getting ingest id $id info..."

    Invoke-RestMethod `
        -Uri "$livesimIngestUrl/$id" `
        -Method GET
}


# Wait for an ingest that is still running
function Wait-CmafIngestRunning {
    param([string]$id)
    Write-Style text "Waiting for segments of id $id to be sent..."
    Start-Sleep -Seconds 2
    try {
        $result = Invoke-RestMethod `
            -Uri "$livesimIngestUrl/$id" `
            -Method GET `
            -ErrorAction SilentlyContinue
        # If we got a response, check if the report contains completion message
        return $null -ne $result
    }
    catch {
    }
}

#endregion

#==============================
#region Test
#==============================

if ($start) {
    Start-CmafIngestReceiver
    Start-LiveSim
}

Write-Style title "Starting CMAF Ingest Test"
Write-Style text "Loop mode: $loop"

$vods = Get-AvailableVods
if ($vodCount -gt 0 -and $vodCount -le $vods.Count) {
    $vods = $vods[0..($vodCount - 1)]
}

function Start-SingleTest {
    param([string]$vod)
    Write-Style subtitle "Running single test with VOD: $vod"

    $vodPath = "$livesimVodUrl$vod"
    $result = Start-CmafIngest -vodUrl $vodPath -channelName "teststream"
    $ingestId = $result.id
    Write-Style text "CMAF Ingest created with ID: $ingestId"

    # Wait
    Wait-CmafIngestRunning -id $ingestId

    # Get ingest info
    Get-CmafIngestInfo -id $ingestId

    # Step the ingest
    foreach ($i in 1..4) {
        Write-Style subtitle "Stepping ingest $i..."
        try {
            Step-CmafIngest -id $ingestId
        }
        catch {
            Write-Styletext "Ingest completed (step failed or no more segments)"
            break
        }

        Wait-CmafIngestRunning -id $ingestId
    }

}

if ($loop) {
    Write-Style text "Running in LOOP mode with $($vods.Count) VODs"
    $vodIndex = 0
    $loopCount = 0

    while ($true) {
        $vod = $vods[$vodIndex]
        $channelName = "loopstream_$vodIndex"
        $loopCount++

        Write-Style text "Loop $($loopCount): VOD $($vodIndex + 1)/$($vods.Count)"

        Start-SingleTest -vod $vod

        # Move to next VOD
        $vodIndex = ($vodIndex + 1) % $vods.Count

        # Small delay between ingests
        Start-Sleep -Seconds 1
    }
}
else {
    $vod = $vods[0]
    Start-SingleTest -vod $vod
}

# Check received files
Write-Style title "Received Files"
if (Test-Path $storageDir) {
    Get-ChildItem -Path $storageDir -Recurse | ForEach-Object {
        Write-Style text "$($_.FullName)"
    }
}
else {
    Write-Style error "Storage directory not found"
}

Write-Style title "Test Complete"

# Cleanup
if ($start -and $stop) {
    Write-Style subtitle "Stopping services..."
    Get-Process -Name (Get-Item $receiverExe).BaseName | `
        Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process -Name (Get-Item $livesimExe).BaseName | `
        Stop-Process -Force -ErrorAction SilentlyContinue
}

Write-Style title "Done!"
