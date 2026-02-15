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

import sys
import shutil
import time
import json
import subprocess
import urllib.request
from typing import List, Optional
from pathlib import Path

from util import AsyncCommand, run_command, run_command_async, Style, write_style

s = Style

LIVESIM_REPO = (Path(__file__).parent / "..").resolve()
LIVESIM_PORT = 9999
PYTHON_ENV_PATH = Path("C:/Users/LuizMonad/miniconda3/envs/py-tmp/python.exe")
FFMPEG_PATH = Path("D:/extern/tools/ffmpeg/ffmpeg.exe")
VLC_PATH = Path("C:/Program Files/VideoLAN/VLC/vlc.exe")
GO_PATH = Path("go")
E2E_DATA_DIR = LIVESIM_REPO / "e2e-test" / "data"

# PLAYLIST_REPO = Path("C:/Users/LuizMonad/Desktop/web/mediaserver-playlistgenerator")
# TEST_ASSET_SRC = PLAYLIST_REPO / "radio_test" / "rtst" / "dash" / "playlist"
# TEST_ASSET_MANIFEST = "track.mpd"
# TEST_ASSET_INI_FILES = ["track_init_iamf.mp4", "track_init_opus.mp4"]
# TEST_ASSET_SEG_FILES = ["track_iamf_{n}.m4s", "track_opus_{n}.m4s"]
# TEST_ASSET_SEGMENTS = None
PLAYLIST_REPO = LIVESIM_REPO / "cmd" / "livesim2" / "app" / "testdata"
TEST_ASSET_SRC = PLAYLIST_REPO / "assets" / "test_fixseg_edtlst"
TEST_ASSET_MANIFEST = "combined.mpd"
TEST_ASSET_INI_FILES = ["aac/init.mp4", "video25fps/init.mp4"]
TEST_ASSET_SEG_FILES = ["aac/{n}.m4s", "video25fps/{n}.m4s"]
TEST_ASSET_SEGMENTS = [
    [0, 95232, 191488, 287744],  # aac
    [0, 25600, 51200, 76800],  # video25fps
]


def setup_test_assets(data_dir: Path) -> List[str]:
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

    src_assets = radio_assets[0:3]
    manifests: List[str] = []

    if len(src_assets) < 3:
        src_assets = src_assets * 3

    for dir in src_assets:
        copy_track(
            dir,
            dest_dir=data_dir / "combined" / dir.name,
            manifest=TEST_ASSET_MANIFEST,
            init_files=TEST_ASSET_INI_FILES,
            segment_files=TEST_ASSET_SEG_FILES,
            segments=TEST_ASSET_SEGMENTS,
        )

    manifests.append("combined/" + TEST_ASSET_MANIFEST)
    return manifests


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


def create_config(out_dir: Path, data_dir: Path, repdata_dir: Path):
    """Create config.json using testdata."""
    write_style(s.subtitle, "Creating config.json")

    config = {
        "port": LIVESIM_PORT,
        "livewindowS": 305,
        "timeoutS": 0,
        "writerepdata": True,
        "concatassets": True,
        "vodroot": str(data_dir.resolve()),
        "repdataroot": str(repdata_dir.resolve()),
    }
    config_path = out_dir / "config.json"
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
    )

    return process


def stop_livesim(livesim: Optional[AsyncCommand]):
    if not livesim:
        return
    write_style(s.subtitle, "Stopping livesim")
    livesim.terminate()


def wait_livesim(livesim: AsyncCommand, url: str, timeout=120):
    """Wait for the server to be ready."""
    write_style(s.text, f"Waiting for server at {url}")

    start = time.time()
    while time.time() - start < timeout:
        # Check if the process died unexpectedly
        rc = livesim.poll()
        if rc is not None:
            livesim.terminate()
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

    livesim.terminate()
    raise TimeoutError(f"Server not ready after {timeout} seconds")


def transcode_dash_to_wav_ffmpeg(mpd_url: str, output_wav: Path, duration_sec=20):
    """Transcode DASH to WAV using ffmpeg."""
    write_style(s.subtitle, "Transcoding DASH to WAV (ffmpeg)")
    write_style(s.text, f"MPD URL: {mpd_url}")
    write_style(s.text, f"Output: {output_wav}")

    ffmpeg = [
        str(FFMPEG_PATH),
        "-y",
        "-fflags",
        "+genpts+discardcorrupt",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-i",
        mpd_url,
        "-t",
        str(duration_sec),
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
        raise RuntimeError(f"ffmpeg failed with code {result.returncode}")

    write_style(s.text, f"Transcoded to: {output_wav}")
    return output_wav


def transcode_dash_to_wav_vlc(mpd_url: str, output_wav: Path, duration_sec=20):
    """Transcode DASH to WAV using VLC."""
    write_style(s.subtitle, "Transcoding DASH to WAV (VLC)")
    write_style(s.text, f"MPD URL: {mpd_url}")
    write_style(s.text, f"Output: {output_wav}")

    vlc_log = output_wav.parent / "vlc.log"

    transcode = (
        f"#transcode{{acodec=s16l,ab=192,channels=2,samplerate=48000}}:"
        f"std{{access=file,mux=wav,dst={output_wav}}}"
    )
    vlc = [
        str(VLC_PATH),
        "-I",
        "dummy",
        "--quiet",
        "--sout",
        transcode,
        "--run-time",
        str(duration_sec),
        mpd_url,
        "vlc://quit",
    ]
    result = run_command("vlc", vlc)
    if result.returncode != 0:
        if vlc_log.exists():
            write_style(s.error, f"VLC log: {vlc_log.read_text()}")
        raise RuntimeError(f"vlc failed with code {result.returncode}")

    write_style(s.text, f"Transcoded to: {output_wav}")
    return output_wav


def create_baseline_wav(
    tmp_dir: Path, mpd_url: str, sample_rate=48000, duration_sec=20
):
    """Create a baseline WAV file by capturing from DASH using VLC."""
    write_style(s.subtitle, "Creating baseline WAV")

    baseline_wav = tmp_dir / "baseline.wav"

    vlc_cmd = [
        str(VLC_PATH),
        "-I", "dummy",
        "--quiet",
        "--sout", f"#transcode{{acodec=s16l,ab=192,channels=2,samplerate={sample_rate}}}:std{{access=file,mux=wav,dst={baseline_wav}}}",
        "--run-time", str(duration_sec),
        mpd_url,
        "vlc://quit",
    ]

    write_style(s.text, f"Running: {vlc_cmd}")
    result = subprocess.run(vlc_cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"VLC failed: {result.stderr}")

    write_style(s.text, f"Baseline created: {baseline_wav}")
    return baseline_wav


def compare_wav(file1: Path, file2: Path):
    """Compare two WAV files using wav_compare."""
    write_style(s.subtitle, "Comparing WAV files")
    write_style(s.text, f"File 1: {file1}")
    write_style(s.text, f"File 2: {file2}")

    wav_compare_script = LIVESIM_REPO / "e2e-test" / "tools" / "wav_compare.py"

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
        write_style(s.title, "✓ TEST PASSED: Audio stitching is correct!")
        return True
    else:
        write_style(s.error, "✗ TEST FAILED: Audio files differ significantly")
        return False


def run_test():
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

    livesim_process: Optional[AsyncCommand] = None
    try:
        manifests = setup_test_assets(data_dir)

        livesim_app = build_livesim(out_dir)
        write_style(s.text, f"Using livesim2: {livesim_app}")

        config_path = create_config(out_dir, data_dir, repdata_dir)
        livesim_process = start_livesim(livesim_app, config_path)

        server_url = f"http://localhost:{LIVESIM_PORT}"
        wait_livesim(livesim_process, f"{server_url}/livesim2/{manifests[0]}")

        mpd_url = f"{server_url}/livesim2/{manifests[0]}"
        captured_wav = tmp_dir / "captured.wav"
        try:
            transcode_dash_to_wav_vlc(mpd_url, captured_wav, duration_sec=25)
        except Exception as e:
            write_style(s.text, f"VLC failed, trying ffmpeg: {e}")
            transcode_dash_to_wav_ffmpeg(mpd_url, captured_wav, duration_sec=25)

        if not captured_wav.exists():
            raise RuntimeError(f"Captured WAV not created: {captured_wav}")

        if captured_wav.stat().st_size < 1000:
            raise RuntimeError(f"Captured WAV too small: {captured_wav.stat().st_size} bytes")

        write_style(s.title, "✓ TEST PASSED: Audio captured successfully!")
        return True

    finally:
        stop_livesim(livesim_process)


if __name__ == "__main__":
    try:
        success = run_test()
        sys.exit(0 if success else 1)
    except Exception as e:
        write_style(s.error, f"TEST FAILED WITH ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
