#!/usr/bin/env python3
"""
End-to-end test script to verify audio stitching at the waveform level in livesim.

This script:
1. Builds livesim2
2. Creates a temporary directory with test assets
3. Starts livesim server
4. Plays the DASH stream and transcodes to WAV
5. Creates a baseline WAV file
6. Compares using wav_compare
"""

import argparse
import sys
import shutil
import time
import json
import urllib.request
from typing import List, Optional, Tuple
from pathlib import Path

from util import AsyncCommand, run_command, run_command_async, Style, write_style

s = Style

LIVESIM_REPO = (Path(__file__).parent / "..").resolve()
LIVESIM_PORT_LIVE = 9999
LIVESIM_PORT_VOD = 9998
PYTHON_ENV_PATH = Path("C:/Users/LuizMonad/miniconda3/envs/py-tmp/python.exe")
FFMPEG_PATH = Path("D:/extern/tools/ffmpeg/ffmpeg.exe")
FFPROBE_PATH = Path("D:/extern/tools/ffmpeg/ffprobe.exe")
VLC_PATH = Path("C:/Program Files/VideoLAN/VLC/vlc.exe")
GO_PATH = Path("go")
E2E_TEST_DIR = LIVESIM_REPO / "e2e-test"
E2E_DATA_DIR = LIVESIM_REPO / "e2e-test" / "data"

PLAYLIST_REPO = Path("C:/Users/LuizMonad/Desktop/radio_test")
TEST_ASSET_COUNT = 3
TEST_ASSET_SRC = PLAYLIST_REPO / "rtst_10th" / "dash"
TEST_ASSET_MANIFEST = "track.mpd"
TEST_ASSET_INI_FILES = ["track_init_iamf.mp4", "track_init_opus.mp4"]
TEST_ASSET_SEG_FILES = ["track_iamf_{n}.m4s", "track_opus_{n}.m4s"]
TEST_ASSET_SEG_DUR = 4
TEST_ASSET_SEGMENTS = None

# PLAYLIST_REPO = LIVESIM_REPO / "cmd" / "livesim2" / "app" / "testdata"
# TEST_ASSET_COUNT = 3
# TEST_ASSET_SRC = PLAYLIST_REPO / "assets" / "test_fixseg_edtlst"
# TEST_ASSET_MANIFEST = "combined.mpd"
# TEST_ASSET_INI_FILES = ["aac/init.mp4", "video25fps/init.mp4"]
# TEST_ASSET_SEG_FILES = ["aac/{n}.m4s", "video25fps/{n}.m4s"]
# TEST_ASSET_SEG_DUR = 2
# TEST_ASSET_SEGMENTS = [
#     [0, 95232, 191488, 287744],  # aac
#     [0, 25600, 51200, 76800],  # video25fps
# ]


def setup_test_assets(data_dir: Path) -> Tuple[str, List[str]]:
    """Copy test assets to temporary directory with required structure for concatenation."""
    write_style(s.subtitle, "Setting up test assets")

    def copy_file(src: Path, dst: Path):
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    def copy_track(
        src_asset: Path,
        dest_dir: Path,
        manifest: str,
        init_files: list[str],
        segment_files: list[str],
        segments: Optional[list[list[int]]],
    ):
        dest_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

        src = src_asset / manifest
        dst = dest_dir / manifest
        copy_file(src, dst)

        for init in init_files:
            src = src_asset / init
            dst = dest_dir / init
            copy_file(src, dst)

        for trk_ix, seg in enumerate(segment_files):
            seg_list = segments[trk_ix] if segments else None
            k = 0
            kmax = 100 if not seg_list else len(seg_list)
            while k < kmax:
                k = k + 1
                n = k if not seg_list else seg_list[k - 1]
                src = src_asset / seg.format(n=n)
                dst = dest_dir / seg.format(n=n)
                if not src.exists():
                    break
                copy_file(src, dst)

    # Get the first asset directory from radio_test
    radio_assets = list(TEST_ASSET_SRC.iterdir())
    if not radio_assets:
        raise RuntimeError(f"No assets found in {TEST_ASSET_SRC}")

    src_assets = radio_assets[0:TEST_ASSET_COUNT]
    manifests: List[str] = []

    if len(src_assets) < TEST_ASSET_COUNT:
        src_assets = src_assets * TEST_ASSET_COUNT

    for dir in src_assets:
        copy_track(
            dir,
            dest_dir=data_dir / "combined" / dir.name,
            manifest=TEST_ASSET_MANIFEST,
            init_files=TEST_ASSET_INI_FILES,
            segment_files=TEST_ASSET_SEG_FILES,
            segments=TEST_ASSET_SEGMENTS,
        )
        manifests.append(f"combined/{dir.name}/{TEST_ASSET_MANIFEST}")

    combined = f"combined/{TEST_ASSET_MANIFEST}"
    return combined, manifests


def build_livesim(out_dir: Path) -> Path:
    out_dir.mkdir(exist_ok=True)
    livesim_app = out_dir / "livesim2"
    if sys.platform == "win32":
        livesim_app = livesim_app.with_suffix(".exe")
    if livesim_app.exists():
        return livesim_app

    write_style(s.text, "Building livesim2")

    cmd = [str(GO_PATH), "build", "-o", str(livesim_app), "./cmd/livesim2/main.go"]
    result = run_command("go", cmd, cwd=LIVESIM_REPO)
    if result.returncode != 0:
        raise RuntimeError("livesim failed to build")

    return livesim_app


def create_config(
    out_dir: Path, data_dir: Path, repdata_dir: Path, port: int, concat=True
):
    """Create config.json using testdata."""
    write_style(s.subtitle, "Creating config.json")

    config = {
        "port": port,
        "livewindowS": 300,
        "timeoutS": 0,
        "writerepdata": True,
        "concatassets": concat,
        "vodroot": str(data_dir.resolve()),
        "repdataroot": str(repdata_dir.resolve()),
    }
    kind = "live" if concat else "vod"
    config_path = out_dir / f"config_{kind}.json"
    config_path.write_text(json.dumps(config, indent=2))

    write_style(s.text, f"Config created: {config_path}")
    write_style(s.text, f"VOD root: {data_dir.resolve()}")
    return config_path


def start_livesim(livesim_app: Path, config_path: Path) -> AsyncCommand:
    """Start livesim server."""
    write_style(s.subtitle, "Starting livesim server")

    import os

    env = os.environ.copy()
    livesim = [str(livesim_app), "--cfg", str(config_path)]
    process = run_command_async(
        "livesim",
        livesim,
        cwd=config_path.parent,
        env=env,
        detach=True,
        use_wt=False,
    )

    return process


def stop_livesim(livesim: Optional[AsyncCommand]):
    if not livesim:
        return None
    write_style(s.subtitle, "Stopping livesim")
    livesim.terminate()
    return None


def wait_livesim(
    livesim: AsyncCommand,
    url: str,
    timeout=120,
):
    """Wait for the server to be ready."""
    write_style(s.text, f"Waiting for server at {url}")

    start = time.time()
    while time.time() - start < timeout:
        # Check if the process died unexpectedly
        rc = livesim.poll()
        if rc is not None:
            raise RuntimeError(f"livesim exited unexpectedly (code {rc})")

        try:
            response = urllib.request.urlopen(url, timeout=5)
            write_style(s.subtitle, f"Server is ready! Status: {response.status}")
            return True
        except Exception as e:
            write_style(
                s.text, f"  Waiting... ({time.time() - start:.1f}s) {type(e).__name__}"
            )
            time.sleep(2)

    raise TimeoutError(f"Server not ready after {timeout} seconds")


def get_audio_duration(file: Path):
    """Get the duration of an audio file."""

    ffprobe = [
        str(FFPROBE_PATH),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_entries",
        "stream=duration",
        str(file),
    ]
    result = run_command("ffprobe", ffprobe, log=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed with code {result.returncode}")

    data = json.loads(result.stdout)
    duration = float(data["streams"][0]["duration"])
    return duration


def trim_audio_duration(input_wav: Path, output_wav: Path, target_duration_sec: float):
    """Trim audio to target duration using ffmpeg."""

    ffmpeg = [
        str(FFMPEG_PATH),
        "-y",
        "-i",
        str(input_wav),
        "-t",
        str(target_duration_sec),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_wav),
    ]
    result = run_command("ffmpeg", ffmpeg)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg trim failed with code {result.returncode}")

    return output_wav


def concat_wavs(wav_files: List[Path], output_wav: Path) -> Path:
    """Concatenate multiple WAV files using ffmpeg."""
    concat_list_file = output_wav.parent / f"concat_{output_wav.stem}.txt"
    with open(concat_list_file, "w") as f:
        for wav in wav_files:
            f.write(f"file '{wav.resolve()}'\n")

    ffmpeg_concat = [
        str(FFMPEG_PATH),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_file),
        "-acodec",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_wav),
    ]
    result = run_command("ffmpeg_concat", ffmpeg_concat)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed with code {result.returncode}")

    return output_wav


def vlc_transcode_wav(
    url: str,
    tmp_dir: Path,
    output_filename: str,
    target_duration_sec: float,
    overshoot_sec: float = 10,
    segment_sec: float = 4,
) -> Path:
    """Transcode DASH to WAV using VLC with Lua interface for precise timing, then trim to target duration."""
    wait_sec = target_duration_sec - overshoot_sec

    output_wav = tmp_dir / output_filename
    write_style(s.text, f"Output: {output_wav}")

    vlc_log = tmp_dir / f"vlc_{output_filename}.log"
    # todo: install the vlc_wait.lua script to appdata

    transcode = (
        f"#transcode{{acodec=s16l,ab=192,channels=2,samplerate=48000}}:"
        f"std{{access=file,mux=wav,dst={output_wav}}}"
    )
    vlc_wait = f"vlc_wait={{wait_sec={wait_sec}, poll_msec=100}}"
    vlc = [
        str(VLC_PATH),
        "--verbose=2",
        "--file-logging",
        "--logfile",
        str(vlc_log),
        "--sout",
        transcode,
        f"--file-caching={segment_sec * 1000}",
        f"--network-caching={segment_sec * 1000}",
        f"--live-caching={segment_sec * 1000}",
        f"--sout-mux-caching={segment_sec * 1000}",
        "--adaptive-use-access",
        "--adaptive-logic=highest",
        "-I",
        "luaintf",
        "--lua-intf=vlc_wait",
        f"--lua-config={vlc_wait}",
        "--no-video",
        url,
    ]
    result = run_command("vlc", vlc, timeout=int(target_duration_sec + 5))
    if result.returncode != 0:
        if vlc_log.exists():
            for line in vlc_log.read_text().splitlines():
                if "vlc_wait" in line:
                    write_style(s.error, line)
        raise RuntimeError(f"vlc failed with code {result.returncode}")
    if vlc_log.exists():
        for line in vlc_log.read_text().splitlines():
            if "vlc_wait" in line:
                write_style(s.progress, line)

    actual_duration = get_audio_duration(output_wav)
    if actual_duration > target_duration_sec:
        trimmed_wav = tmp_dir / f"trimmed_{output_filename}"
        trim_audio_duration(output_wav, trimmed_wav, target_duration_sec)
        output_wav = trimmed_wav

    write_style(s.text, f"Transcoded to: {output_wav}")
    return output_wav


def create_captured_wav(
    mpd_url: str,
    tmp_dir: Path,
    duration_sec=20,
    overshoot_sec=10,
) -> Path:
    """Transcode DASH to WAV using VLC with Lua interface for precise timing."""
    write_style(s.subtitle, "Transcoding DASH to WAV (VLC)")
    write_style(s.text, f"MPD URL: {mpd_url}")

    return vlc_transcode_wav(
        url=mpd_url,
        tmp_dir=tmp_dir,
        output_filename="captured.wav",
        target_duration_sec=duration_sec,
        overshoot_sec=overshoot_sec,
    )


def create_baseline_vod_wav(
    tmp_dir: Path,
    data_dir: Path,
    live_url: str,
    manifest: str,
) -> Path:
    """Create a baseline WAV by transcoding test assets via livesim in non-concat mode."""

    write_style(s.divider, "")
    write_style(s.text, f"Processing manifest: {manifest}")

    mpd_path = data_dir / manifest
    seg_pattern = TEST_ASSET_SEG_FILES[0]
    seg_template = seg_pattern.format(n="*")
    seg_files = sorted(mpd_path.parent.glob(seg_template))
    actual_duration = len(seg_files) * TEST_ASSET_SEG_DUR
    write_style(s.text, f"Duration: {actual_duration}s ({len(seg_files)} segments)")

    mpd_url = f"{live_url}/{manifest}"
    return vlc_transcode_wav(
        url=mpd_url,
        tmp_dir=tmp_dir,
        output_filename=f"baseline_{Path(manifest).parent.name}.wav",
        target_duration_sec=actual_duration,
        overshoot_sec=0,
    )


def create_baseline_wav(
    tmp_dir: Path,
    asset_wavs: List[Path],
) -> Path:
    """Create a baseline WAV by transcoding test assets via livesim in non-concat mode."""
    write_style(s.subtitle, "Creating baseline WAV from test assets")

    if len(asset_wavs) == 1:
        final_wav = asset_wavs[0]
    else:
        write_style(s.text, "Concatenating asset WAVs")
        final_wav = tmp_dir / "baseline.wav"
        concat_wavs(asset_wavs, final_wav)

    write_style(s.text, f"Baseline WAV created: {final_wav}")
    return final_wav


def compare_wav(file1: Path, file2: Path):
    """Compare two WAV files using wav_compare."""
    write_style(s.subtitle, "Comparing WAV files")
    write_style(s.text, f"File 1: {file1}")
    write_style(s.text, f"File 2: {file2}")

    wav_compare_script = E2E_TEST_DIR / "wav_compare.py"

    compare = [
        str(PYTHON_ENV_PATH),
        str(wav_compare_script),
        str(file1),
        str(file2),
        "--no-plot",
    ]
    result = run_command("compare", compare)
    if result.returncode != 0:
        raise RuntimeError(f"compare failed with code {result.returncode}")

    if "Excellent similarity" in result.stdout or "Good similarity" in result.stdout:
        write_style(s.subtitle, "Result: Audio stitching is correct!")
        return True
    else:
        write_style(s.error, "Result: Audio files differ significantly")
        return False


def run_test(spawn_livesim=False):
    """Main test function."""
    write_style(s.title, "LiveSim audio stitching E2E test")

    repdata_dir = E2E_DATA_DIR / "repdata"
    repdata_dir.mkdir(parents=True, exist_ok=True)

    data_dir = E2E_DATA_DIR / "media"
    data_dir.mkdir(parents=True, exist_ok=True)

    out_dir = E2E_DATA_DIR / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_dir = E2E_DATA_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    manifest, manifests = setup_test_assets(data_dir)

    livesim_app = Path()
    if spawn_livesim:
        livesim_app = build_livesim(out_dir)
        write_style(s.text, f"Using livesim2: {livesim_app}")

    livesim_process: Optional[AsyncCommand] = None
    try:

        port = LIVESIM_PORT_LIVE
        server_url = f"http://localhost:{port}/livesim2"
        mpd_url = f"{server_url}/{manifest}"
        if spawn_livesim:
            config_path = create_config(
                out_dir,
                data_dir,
                repdata_dir,
                port,
            )
            livesim_process = start_livesim(livesim_app, config_path)
            wait_livesim(livesim_process, mpd_url)

        captured_wav = create_captured_wav(mpd_url, tmp_dir, duration_sec=32)
        dur = get_audio_duration(captured_wav)
        write_style(s.text, f"Duration: {dur}")

        if spawn_livesim:
            livesim_process = stop_livesim(livesim_process)

        port = LIVESIM_PORT_VOD
        server_url = f"http://localhost:{port}/vod"
        mpd_url = f"{server_url}/{manifests[0]}"
        if spawn_livesim:
            config_path = create_config(
                out_dir,
                data_dir,
                repdata_dir,
                port,
                concat=False,
            )
            livesim_process = start_livesim(livesim_app, config_path)
            wait_livesim(livesim_process, mpd_url)

        vods = []
        for manifest in manifests:
            baseline_vod_wav = create_baseline_vod_wav(
                tmp_dir, data_dir, server_url, manifest
            )
            vods.append(baseline_vod_wav)

        if spawn_livesim:
            livesim_process = stop_livesim(livesim_process)

        baseline_wav = create_baseline_wav(tmp_dir, vods)
        dur = get_audio_duration(baseline_wav)
        write_style(s.text, f"Duration: {dur}")

        result = compare_wav(baseline_wav, captured_wav)
        if result:
            write_style(s.title, "✓ TEST PASSED: Audio captured successfully!")
        else:
            write_style(s.error, "✗ TEST FAILED: Audio didn't match!")
        return True

    finally:
        stop_livesim(livesim_process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run tests with optional livesim spawning."
    )
    parser.add_argument(
        "--livesim", "-s", action="store_true", help="Enable spawning livesim."
    )
    args = parser.parse_args()

    try:
        success = run_test(spawn_livesim=args.livesim)
        sys.exit(0 if success else 1)
    except Exception as e:
        write_style(s.error, f"TEST FAILED WITH ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
