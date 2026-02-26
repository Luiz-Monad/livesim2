import argparse
import librosa
import matplotlib.pyplot as plt
import numpy as np
import os
import sys
import warnings
from scipy import signal
from scipy.io import wavfile

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore

warnings.filterwarnings("ignore")


class WavComparator:
    def __init__(
        self,
        file1,
        file2,
        max_shift_sec=0,
        max_align_channels=4,
        search_offset_sec=None,
    ):
        """
        Initialize WAV comparator with two file paths

        Args:
            file1: Path to first WAV file
            file2: Path to second WAV file
            max_shift_sec: If > 0, search ±this many seconds around search_offset_sec
            max_align_channels: Max channels per file used for alignment (highest-energy).
                                0 = use all channels (slow for 20-ch files).
            search_offset_sec: Where in file1 to centre the search window (seconds).
                               None / 0 = start of file. Use e.g. 210 to find a
                               clip near the end of a 222s file.
        """
        self.file1 = file1
        self.file2 = file2
        self._max_align_channels = max_align_channels

        # Check if files exist
        if not os.path.exists(file1):
            raise FileNotFoundError(f"File not found: {file1}")
        if not os.path.exists(file2):
            raise FileNotFoundError(f"File not found: {file2}")

        try:
            # Load audio files using scipy for numerical comparison
            self.sample_rate1, self.audio1 = wavfile.read(file1)
            self.sample_rate2, self.audio2 = wavfile.read(file2)
        except Exception as e:
            print(f"Error loading WAV files: {e}")
            print("\nTrying alternative loading method with librosa...")
            # Try loading with librosa as fallback
            self.audio1, self.sample_rate1 = librosa.load(file1, sr=None)
            self.audio2, self.sample_rate2 = librosa.load(file2, sr=None)

        # Keep raw multi-channel data for smart alignment
        raw1 = self.audio1 if len(self.audio1.shape) > 1 else self.audio1[:, np.newaxis]
        raw2 = self.audio2 if len(self.audio2.shape) > 1 else self.audio2[:, np.newaxis]
        self._raw1 = raw1.astype(np.float32)
        self._raw2 = raw2.astype(np.float32)
        self._n_channels1 = self._raw1.shape[1]
        self._n_channels2 = self._raw2.shape[1]

        # Convert to mono
        if len(self.audio1.shape) > 1:
            self.audio1 = self.audio1.mean(axis=1)
        if len(self.audio2.shape) > 1:
            self.audio2 = self.audio2.mean(axis=1)

        # Normalize audio to [-1, 1] range
        self.audio1 = self.audio1.astype(np.float32) / np.max(np.abs(self.audio1))
        self.audio2 = self.audio2.astype(np.float32) / np.max(np.abs(self.audio2))

        if max_shift_sec <= 0:
            # No alignment — just truncate to same length
            self.best_shift = 0
            self.best_correlation = None
            min_len = min(len(self.audio1), len(self.audio2))
            self.audio1 = self.audio1[:min_len]
            self.audio2 = self.audio2[:min_len]
        else:
            # Find and apply shift so self.audio1/audio2 are always aligned from here on
            self.best_shift, self.best_correlation = self.find_best_shift(
                max_shift_sec, search_offset_sec
            )
            offset_samples = int(search_offset_sec * self.sample_rate1)
            abs_shift = self.best_shift + offset_samples
            if abs_shift >= 0 and abs_shift + len(self.audio2) <= len(self.audio1):
                self.audio1 = self.audio1[abs_shift : abs_shift + len(self.audio2)]
            else:
                self.audio1, self.audio2 = self.apply_shift(
                    self.audio1, self.audio2, abs_shift
                )
            shift_sec = abs_shift / self.sample_rate1
            print()
            print("Auto-aligned:")
            print(f"  position in file1 = {shift_sec:.3f}s ({abs_shift} samples from start)")
            print(f"  correlation={self.best_correlation:.4f}")

            if len(self.audio1) == 0 or len(self.audio2) == 0:
                raise ValueError(
                    f"No overlapping audio found at position "
                    f"{shift_sec:.3f}s ({abs_shift} samples from start). "
                    f"The best-matching position of the short clip lies entirely "
                    f"outside the long file's duration. "
                    f"This usually means the clip does not appear in the file, "
                    f"or --max-shift is too large and matched noise at the boundary."
                )

    def get_file_info(self):
        """Print basic information about both WAV files"""
        a_dur1 = f"{len(self.audio1)/self.sample_rate1:.2f} seconds"
        a_dur2 = f"{len(self.audio2)/self.sample_rate2:.2f} seconds"
        o_dur1 = f"{self._raw1.shape[0]/self.sample_rate1:.2f} seconds"
        o_dur2 = f"{self._raw2.shape[0]/self.sample_rate2:.2f} seconds"
        a_spl1 = str(len(self.audio1))
        a_spl2 = str(len(self.audio2))
        o_spl1 = str(self._raw1.shape[0])
        o_spl2 = str(self._raw2.shape[0])
        print()
        print("WAV FILE COMPARISON")
        print("───────────────────")
        print()
        print(f"File 1: {self.file1}")
        print(f"  Sample Rate: {self.sample_rate1} Hz")
        print(f"  Channels: {self._n_channels1}")
        print(f"  Duration: {a_dur1}"+ (f" (original {o_dur1})" if a_dur1 != o_dur1 else ""))
        print(f"  Samples: {a_spl1}"+ (f" (original {o_spl1})" if a_spl1 != o_spl1 else ""))
        print(f"  Max Amplitude: {np.max(np.abs(self.audio1)):.4f}")
        print()
        print(f"File 2: {self.file2}")
        print(f"  Sample Rate: {self.sample_rate2} Hz")
        print(f"  Channels: {self._n_channels2}")
        print(f"  Duration: {a_dur2}"+ (f" (original {o_dur2})" if a_dur2 != o_dur2 else ""))
        print(f"  Samples: {a_spl2}"+ (f" (original {o_spl2})" if a_spl2 != o_spl2 else ""))
        print(f"  Max Amplitude: {np.max(np.abs(self.audio2)):.4f}")
        print()
        if self.sample_rate1 != self.sample_rate2:
            print("⚠ Warning: Sample rates are different!")
        else:
            print("✓ Sample rates match")

    @staticmethod
    def _xcorr_shift(a1, a2, max_shift_samples):
        """FFT cross-correlation between two 1-D signals.

        Returns (shift_in_samples, normalised_peak) where shift is the lag of
        a2 relative to a1 (positive → a2 starts later).
        """
        # Normalize both signals
        a1 = (a1 - np.mean(a1)) / (np.std(a1) + 1e-10)
        a2 = (a2 - np.mean(a2)) / (np.std(a2) + 1e-10)

        # Use FFT-based cross-correlation for efficiency
        n = len(a1) + len(a2) - 1
        fft_size = 1
        while fft_size < n:
            fft_size *= 2

        fft_a1 = np.fft.fft(a1, fft_size)
        fft_a2 = np.fft.fft(a2, fft_size)

        # Cross-correlation in frequency domain
        corr = np.real(np.fft.ifft(fft_a1 * np.conj(fft_a2)))

        # Roll so index 0 = zero shift
        center = len(corr) // 2
        corr = np.roll(corr, center)

        # Limit to our search range
        start = max(0, center - max_shift_samples)
        end = min(len(corr), center + max_shift_samples + 1)
        window = corr[start:end]

        # Find best shift
        best_idx = np.argmax(window)
        shift = best_idx - min(max_shift_samples, center)

        # Normalize correlation to [-1, 1] range
        norm = np.sqrt(np.sum(a1**2) * np.sum(a2**2))
        peak = window[best_idx] / (norm + 1e-10)
        return shift, peak

    def find_best_shift(self, max_shift_sec=10, search_offset_sec=0.0):
        """Find alignment shift using per-channel cross-correlation.

        Searches a window of ±max_shift_sec around search_offset_sec in file1.
        Returns (shift_relative_to_offset, best_correlation). The caller adds
        offset_samples to get the absolute position in file1.
        """
        max_shift_samples = int(max_shift_sec * self.sample_rate1)
        offset_samples = int(search_offset_sec * self.sample_rate1)

        n1 = self._n_channels1
        n2 = self._n_channels2

        # Select highest-energy channels to keep search tractable
        mac = self._max_align_channels

        def top_channels(raw, k):
            energies = np.sum(raw**2, axis=0)
            order = np.argsort(energies)[::-1]
            return order[:k] if k > 0 else order

        ch1_idx = top_channels(self._raw1, mac if mac > 0 else n1)
        ch2_idx = top_channels(self._raw2, mac if mac > 0 else n2)
        n_pairs = len(ch1_idx) * len(ch2_idx)

        # Accumulate correlation scores across channel pairs
        axis_len = 2 * max_shift_samples + 1
        score_sum = np.zeros(axis_len, dtype=np.float64)

        # Slice the search window out of file1:
        #   starts at max(0, offset - max_shift)
        #   ends   at min(total, offset + clip_len + max_shift)
        clip_len = len(self._raw2)
        win_start = max(0, offset_samples - max_shift_samples)
        win_end = min(len(self._raw1), offset_samples + clip_len + max_shift_samples)
        window_len = win_end - win_start
        room_before = offset_samples
        room_after = len(self._raw1) - offset_samples - clip_len
        eff_max_shift = min(max_shift_samples, room_before + room_after)
        max_pos_in_window = window_len - clip_len if window_len > clip_len else 0
        xcorr_max_shift = max_pos_in_window

        for c2 in ch2_idx:
            ch2 = self._raw2[:, c2]
            if np.max(np.abs(ch2)) < 1e-6:
                continue  # silent channel, skip
            for c1 in ch1_idx:
                ch1 = self._raw1[win_start:win_end, c1]
                if np.max(np.abs(ch1)) < 1e-6:
                    continue

                shift, peak = self._xcorr_shift(ch1, ch2, xcorr_max_shift)

                # shift is relative to win_start; convert to offset-relative
                abs_pos = win_start + shift  # absolute pos in file1
                rel_shift = abs_pos - offset_samples  # relative to offset
                idx = rel_shift + max_shift_samples
                # Only accumulate if position is within valid range (clip must fit in file)
                if 0 <= idx < axis_len and win_start <= abs_pos <= win_end - clip_len:
                    score_sum[idx] += peak

        # Final best shift = highest accumulated score position
        best_idx = np.argmax(score_sum)
        best_shift = best_idx - max_shift_samples
        best_correlation = score_sum[best_idx] / max(1, n_pairs)

        print(
            f"  (searched {len(ch1_idx)}×{len(ch2_idx)}={n_pairs} channel pairs "
            f"[top-{mac if mac > 0 else 'all'} by energy], "
            f"best accumulated score={score_sum[best_idx]:.2f})"
        )

        return best_shift, best_correlation

    @staticmethod
    def apply_shift(a1, a2, shift):
        """Trim a1 and a2 to their overlapping region given shift.

        Positive shift: a2 lags → trim start of a1, trim end of a2.
        Negative shift: a2 leads → trim end of a1, trim start of a2.
        """
        if shift > 0:
            a1 = a1[shift:]
            if shift < len(a2):
                a2 = a2[:-shift]
            else:
                a2 = a2[:0]  # no overlap
        elif shift < 0:
            s = abs(shift)
            if s < len(a1):
                a1 = a1[:-s]
            else:
                a1 = a1[:0]  # no overlap
            a2 = a2[s:]
        min_len = min(len(a1), len(a2))
        return a1[:min_len], a2[:min_len]

    def estimate_clock_skew(self, window_sec=0.5, n_windows=8):
        """Estimate clock skew between the two aligned signals.

        Splits the aligned region into n_windows evenly spaced windows and
        measures the local cross-correlation lag in each. A linear fit of
        lag vs time gives the rate ratio (samples of drift per sample elapsed).

        Returns:
            ratio         - true sr2/sr1 ratio (1.0 = no skew)
            drift_samples - total drift in samples over the aligned region
            window_times  - centre times of each window (seconds)
            window_lags   - measured lag at each window (samples)
        """
        sr = self.sample_rate1
        win = int(window_sec * sr)
        n = len(self.audio1)
        if n < 2 * win:
            return 1.0, 0, np.array([]), np.array([])

        centres = np.linspace(win, n - win, n_windows, dtype=int)
        lags = []
        times = []
        search = win // 2  # search ±half-window for local lag

        for c in centres:
            seg1 = self.audio1[c - win // 2 : c + win // 2]
            lo = max(0, c - win // 2 - search)
            hi = min(n, c + win // 2 + search)
            seg2_wide = self.audio2[lo:hi]
            if len(seg2_wide) < len(seg1):
                continue
            corr = np.correlate(
                seg2_wide - seg2_wide.mean(),
                seg1 - seg1.mean(),
                mode="valid",
            )
            lag = np.argmax(corr) - search  # positive → audio2 is running ahead
            lags.append(lag)
            times.append(c / sr)

        if len(lags) < 2:
            return 1.0, 0, np.array(times), np.array(lags)

        times = np.array(times, dtype=float)
        lags = np.array(lags, dtype=float)

        # Linear fit: lag = slope * time + intercept
        slope, _ = np.polyfit(times, lags, 1)
        ratio = 1.0 + slope / sr
        drift_samples = int(round(slope * (n / sr)))

        return ratio, drift_samples, times, lags

    def apply_clock_correction(self, ratio):
        """Resample audio2 to compensate for clock skew (ratio = sr2/sr1).

        Uses FFT-based resampling via scipy.signal.resample then re-truncates
        both signals to the same length.
        """
        if abs(ratio - 1.0) < 1e-7:
            return
        new_len = int(round(len(self.audio2) / ratio))
        self.audio2 = signal.resample(self.audio2, new_len)
        min_len = min(len(self.audio1), len(self.audio2))
        self.audio1 = self.audio1[:min_len]
        self.audio2 = self.audio2[:min_len]

    def calculate_mse(self):
        """Calculate Mean Squared Error between the two audio signals"""
        return np.mean((self.audio1 - self.audio2) ** 2)

    def calculate_snr(self):
        """Calculate Signal-to-Noise Ratio"""
        signal_power = np.mean(self.audio1**2)
        noise_power = np.mean((self.audio1 - self.audio2) ** 2)

        if noise_power == 0:
            return float("inf")

        return 10 * np.log10(signal_power / noise_power)

    def calculate_correlation(self):
        """Calculate Pearson correlation between the two signals"""
        return np.corrcoef(self.audio1, self.audio2)[0, 1]

    def calculate_spectral_distance(self):
        """Calculate spectral distance using FFT"""
        # Compute FFT
        fft1 = np.abs(np.fft.fft(self.audio1))
        fft2 = np.abs(np.fft.fft(self.audio2))

        # Normalize
        fft1 = fft1 / np.sum(fft1)
        fft2 = fft2 / np.sum(fft2)

        # Calculate spectral distance
        return np.sqrt(np.sum((fft1 - fft2) ** 2))

    def calculate_silence_differences(self, threshold=0.01):
        """Compare silent regions between files"""
        # Identify silent regions
        silence1 = np.abs(self.audio1) < threshold
        silence2 = np.abs(self.audio2) < threshold

        # Calculate percentage of silent samples
        silence_percent1 = np.mean(silence1) * 100
        silence_percent2 = np.mean(silence2) * 100

        # Calculate agreement in silence detection
        silence_agreement = np.mean(silence1 == silence2) * 100

        return silence_percent1, silence_percent2, silence_agreement

    def plot_waveforms(self, save_plots=False, output_prefix=""):
        """Plot waveforms of both files for visual comparison, aligned if shift is known."""
        fig, axes = plt.subplots(3, 1, figsize=(12, 8))

        # Time axis
        time = np.arange(len(self.audio1)) / self.sample_rate1

        axes[0].plot(time, self.audio1, "b-", alpha=0.7)
        axes[0].set_title(f"Waveform - File 1 ({self.file1})")
        axes[0].set_ylabel("Amplitude")
        axes[0].set_xlim([0, time[-1]])
        axes[0].legend()
        axes[0].grid(True, alpha=0.3)

        axes[1].plot(time, self.audio2, "r-", alpha=0.7)
        axes[1].set_title(f"Waveform - File 2 ({self.file2})")
        axes[1].set_ylabel("Amplitude")
        axes[1].set_xlim([0, time[-1]])
        axes[1].legend()
        axes[1].grid(True, alpha=0.3)

        # Overlay — audio is already aligned, no extra shift needed
        shift_sec = self.best_shift / self.sample_rate1
        axes[2].plot(time, self.audio1, "b-", alpha=0.5, label=axes[0].get_title())
        axes[2].plot(time, self.audio2, "r-", alpha=0.5, label=axes[1].get_title())
        axes[2].set_title(f"Aligned Overlay (applied shift: {shift_sec:.3f}s)")
        axes[2].set_xlabel("Time (seconds)")
        axes[2].set_ylabel("Amplitude")
        axes[2].set_xlim([0, time[-1]])
        axes[2].legend()
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()

        if save_plots:
            filename = f"{output_prefix}waveforms.png"
            plt.savefig(filename, dpi=150, bbox_inches="tight")
            print(f"Saved waveform plot to: {filename}")
            plt.close()
        else:
            plt.show()

    def plot_spectrograms(self, save_plots=False, output_prefix=""):
        """Plot spectrograms of both files"""
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))

        # Create spectrograms
        f1, t1, Sxx1 = signal.spectrogram(
            self.audio1, fs=self.sample_rate1, nperseg=1024
        )
        f2, t2, Sxx2 = signal.spectrogram(
            self.audio2, fs=self.sample_rate2, nperseg=1024
        )

        # Plot spectrogram of file 1
        im1 = axes[0, 0].pcolormesh(
            t1, f1, 10 * np.log10(Sxx1 + 1e-10), shading="gouraud"
        )
        axes[0, 0].set_title(f"Spectrogram - File 1 ({self.file1})")
        axes[0, 0].set_ylabel("Frequency [Hz]")
        plt.colorbar(im1, ax=axes[0, 0], label="Power [dB]")

        # Plot spectrogram of file 2
        im2 = axes[0, 1].pcolormesh(
            t2, f2, 10 * np.log10(Sxx2 + 1e-10), shading="gouraud"
        )
        axes[0, 1].set_title(f"Spectrogram - File 2 ({self.file2})")
        axes[0, 1].set_ylabel("Frequency [Hz]")
        plt.colorbar(im2, ax=axes[0, 1], label="Power [dB]")

        # Plot frequency spectra
        freqs1 = np.fft.fftfreq(len(self.audio1), 1 / self.sample_rate1)
        fft1 = np.abs(np.fft.fft(self.audio1))
        fft2 = np.abs(np.fft.fft(self.audio2))

        # Only show positive frequencies
        pos_freqs = freqs1[: len(freqs1) // 2]
        axes[1, 0].plot(
            pos_freqs, fft1[: len(pos_freqs)], "b-", alpha=0.7, label="File 1"
        )
        axes[1, 0].plot(
            pos_freqs, fft2[: len(pos_freqs)], "r-", alpha=0.7, label="File 2"
        )
        axes[1, 0].set_title("Frequency Spectrum Comparison")
        axes[1, 0].set_xlabel("Frequency [Hz]")
        axes[1, 0].set_ylabel("Magnitude")
        axes[1, 0].legend()
        axes[1, 0].grid(True, alpha=0.3)
        axes[1, 0].set_xlim([0, min(self.sample_rate1, self.sample_rate2) // 2])

        # Plot spectral difference
        spectral_diff = np.abs(fft1[: len(pos_freqs)] - fft2[: len(pos_freqs)])
        axes[1, 1].plot(pos_freqs, spectral_diff, "g-", alpha=0.7)
        axes[1, 1].set_title("Spectral Difference")
        axes[1, 1].set_xlabel("Frequency [Hz]")
        axes[1, 1].set_ylabel("Difference Magnitude")
        axes[1, 1].grid(True, alpha=0.3)
        axes[1, 1].set_xlim([0, min(self.sample_rate1, self.sample_rate2) // 2])

        plt.tight_layout()

        if save_plots:
            filename = f"{output_prefix}spectrograms.png"
            plt.savefig(filename, dpi=150, bbox_inches="tight")
            print(f"Saved spectrogram plot to: {filename}")
            plt.close()
        else:
            plt.show()

    def compare_all(
        self, plot=True, save_plots=False, output_prefix="", silence_threshold=0.01
    ):
        """Run all comparisons and display results"""
        # Print file information
        self.get_file_info()

        # Calculate metrics
        mse = self.calculate_mse()
        snr = self.calculate_snr()
        correlation = self.calculate_correlation()
        spectral_dist = self.calculate_spectral_distance()
        silence1, silence2, silence_agreement = self.calculate_silence_differences(
            silence_threshold
        )

        # Clock-skew detection
        segment = self.sample_rate1
        drift_available = len(self.audio1) > 2 * segment

        skew_ratio, skew_drift, skew_times, skew_lags = self.estimate_clock_skew()
        skew_ppm = (skew_ratio - 1.0) * 1e6

        if drift_available:
            start_corr = np.corrcoef(self.audio1[:segment], self.audio2[:segment])[0, 1]
            end_corr_before = np.corrcoef(
                self.audio1[-segment:], self.audio2[-segment:]
            )[0, 1]
        else:
            start_corr = end_corr_before = 0.0

        # If significant skew detected, correct and recompute metrics
        skew_corrected = abs(skew_ppm) > 20 and len(skew_times) >= 2
        if skew_corrected:
            self.apply_clock_correction(skew_ratio)
            mse = self.calculate_mse()
            snr = self.calculate_snr()
            correlation = self.calculate_correlation()
            spectral_dist = self.calculate_spectral_distance()
            silence1, silence2, silence_agreement = self.calculate_silence_differences(
                silence_threshold
            )
            if drift_available and len(self.audio1) > 2 * segment:
                end_corr_after = np.corrcoef(
                    self.audio1[-segment:], self.audio2[-segment:]
                )[0, 1]
            else:
                end_corr_after = end_corr_before
        else:
            end_corr_after = end_corr_before

        aligned_sec = len(self.audio1) / self.sample_rate1
        shift_sec = self.best_shift / self.sample_rate1

        print("")
        print("COMPARISON METRICS")
        print("──────────────────")
        print()
        print(f"Aligned region: {aligned_sec:.2f}s ({len(self.audio1)} samples)")
        print(f"Applied shift:  {shift_sec:.3f}s ({self.best_shift} samples)")
        print(f"Mean Squared Error (MSE): {mse:.6e}")
        print(f"Signal-to-Noise Ratio (SNR): {snr:.2f} dB")
        print(f"Correlation Coefficient: {correlation:.4f}")
        print(f"Spectral Distance: {spectral_dist:.6f}")
        print()
        print(f"Silence Analysis (threshold = {silence_threshold}):")
        print(f"  File 1 silent samples: {silence1:.2f}%")
        print(f"  File 2 silent samples: {silence2:.2f}%")
        print(f"  Silence agreement: {silence_agreement:.2f}%")

        print("")
        print(f"Drift / Clock-Skew Check:")
        if drift_available:
            print(f"  Start correlation (first 1s): {start_corr:.4f}")
            print(f"  End correlation   (last 1s, before correction): {end_corr_before:.4f}")
        if len(skew_times) >= 2:
            print(f"  Estimated clock skew: {skew_ppm:+.1f} ppm")
            print(f"  ({skew_ratio:.9f} ratio,  {skew_drift:+d} samples total drift)")
            if skew_corrected:
                print(f"  ✓ Clock-skew correction applied (resampled file 2 by {skew_ratio:.9f}x)")
                if drift_available:
                    print(f"  End correlation   (last 1s, after  correction): {end_corr_after:.4f}")
            elif abs(skew_ppm) > 20:
                print(f" ⚠  Skew detected but too few windows for reliable correction")
            else:
                print(f"  ✓ No significant clock skew (< 20 ppm)")
        else:
            if drift_available:
                drift = start_corr - end_corr_before
                if drift > 0.1:
                    print(f" ⚠  Drift detected ({drift:.3f} drop) - possible clock skew ")
                    print(f"    (aligned region too short for ppm estimate)")
                else:
                    print(f"  ✓ No significant drift ({drift:.3f})")

        print()
        print(f"Correlation Interpretation:")
        if correlation > 0.9:
            print("  ✓ Excellent similarity - Files are almost identical")
        elif correlation > 0.7:
            print("  ✓ Good similarity - Files are very similar")
        elif correlation > 0.5:
            print(" ⚠  Moderate similarity - Files share some characteristics")
        elif correlation > 0.3:
            print(" ⚠  Weak similarity - Files are somewhat different")
        else:
            print("  ✗ Poor similarity - Files are very different")

        if plot:
            print()
            print("Generating visualizations...")
            self.plot_waveforms(save_plots, output_prefix)
            self.plot_spectrograms(save_plots, output_prefix)

        return {
            "mse": mse,
            "snr": snr,
            "correlation": correlation,
            "spectral_distance": spectral_dist,
            "silence_agreement": silence_agreement,
        }


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Compare two WAV files and generate analysis reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s file1.wav file2.wav
  %(prog)s file1.wav file2.wav --no-plot
  %(prog)s file1.wav file2.wav --max-shift 10
  %(prog)s file1.wav file2.wav --save-plots --output-prefix="comparison_"
  %(prog)s file1.wav file2.wav --silence-threshold 0.05
        """,
    )

    parser.add_argument("file1", help="First WAV file to compare")
    parser.add_argument("file2", help="Second WAV file to compare")

    parser.add_argument(
        "--max-shift",
        type=float,
        default=10,
        help="Max seconds to search for alignment shift, 0=no alignment (default: 10)",
    )
    parser.add_argument(
        "--max-align-channels",
        type=int,
        default=4,
        help="Max channels per file used for alignment search (highest-energy). "
        "0=use all. Higher = more accurate but slower for many-channel files (default: 4)",
    )
    parser.add_argument(
        "--search-offset",
        type=float,
        default=0.0,
        help="Centre of the search window in file1 (seconds). "
        "Use this when the clip is near the end: set to fullduration-clipduration (default: 0)",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Disable visualization plots (show only numerical results)",
    )
    parser.add_argument(
        "--save-plots",
        action="store_true",
        help="Save plots to files instead of displaying them",
    )
    parser.add_argument(
        "--output-prefix",
        default="",
        help='Prefix for output plot filenames (default: "")',
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.01,
        help="Threshold for silence detection (default: 0.01)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    return parser.parse_args()


def main():
    """Main function to handle command line execution"""
    args = parse_arguments()

    if args.verbose:
        print(f"Comparing files: {args.file1} and {args.file2}")

    try:
        comparator = WavComparator(
            args.file1,
            args.file2,
            max_shift_sec=args.max_shift,
            max_align_channels=args.max_align_channels,
            search_offset_sec=args.search_offset,
        )

        results = comparator.compare_all(
            plot=not args.no_plot,
            save_plots=args.save_plots,
            output_prefix=args.output_prefix,
            silence_threshold=args.silence_threshold,
        )

        # Print summary
        print("")
        print("SUMMARY")
        print("───────")
        print()
        print(f"Files compared successfully!")
        print(f"Correlation: {results['correlation']:.4f}")
        print(f"SNR: {results['snr']:.2f} dB")
        print()

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
