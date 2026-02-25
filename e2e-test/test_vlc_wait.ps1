
$overshot = 0
$capture = 20 - $overshot
$tout = ($capture + $overshot) * 1.1
$elapses = @()

(1..5) | ForEach-Object {

    Get-Process vlc -ErrorAction Ignore | `
        Stop-Process -ErrorAction Ignore
    Remove-Item captured.wav -ErrorAction Ignore
    Remove-Item vlc.log -ErrorAction Ignore

    $pl = New-Item -ItemType Directory "$env:APPDATA/vlc/lua/intf" -Force
    Copy-Item 'vlc_wait.lua' $pl

    $vlc = 'C:\Program Files\VideoLAN\VLC\vlc.exe'
    $vlc_args = @(
        '--verbose=2'
        '--file-logging'
        '--logfile=vlc.log'
        '--sout'
        '#transcode{acodec=s16l,ab=192,channels=2,samplerate=48000}:std{access=file,mux=wav,dst=captured.wav}'
        '--file-caching=2000'
        '--network-caching=2000'
        '--live-caching=2000'
        '--sout-mux-caching=2000'
        '--adaptive-use-access'
        '--adaptive-logic=highest'
        '-I'
        'luaintf'
        '--lua-intf=vlc_wait'
        '--lua-config="vlc_wait={wait_sec=' + $capture + ', poll_msec=100}"'
        '--no-video'
        'http://localhost:9999/livesim2/combined/combined.mpd'
    )
    $proc = Start-Process -FilePath $vlc -ArgumentList $vlc_args -PassThru
    Write-Host "vlc started: $($proc.Id)"

    try {
        $proc | Wait-Process -Timeout $tout -ErrorAction Stop
    }
    catch {
        Write-Host "VLC did not exit within $tout seconds - killing it"
        $proc | Stop-Process -Force
    }

    Get-Content 'vlc.log' | `
        Select-String 'vlc_wait'

    $duration = & 'D:\extern\tools\ffmpeg\ffprobe.exe' `
        '-v' quiet `
        '-print_format' json `
        '-show_entries' 'stream=duration' `
        'captured.wav' | `
        ConvertFrom-Json | `
        ForEach-Object { $_.streams.duration }
    Write-Host $duration

    $elapses += @($duration)
}

Get-Process vlc -ErrorAction Ignore | `
    Stop-Process -ErrorAction Ignore

Write-Host $elapses
