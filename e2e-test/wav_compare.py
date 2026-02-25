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
    def __init__(self, file1, file2, max_shift_sec=0):
        """
        Initialize WAV comparator with two file paths

        Args:
            file1: Path to first WAV file
            file2: Path to second WAV file
            max_shift_sec: If > 0, auto-detect and apply alignment shift up to this many seconds
        """
        self.file1 = file1
        self.file2 = file2

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

        # Convert to mono if stereo
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
            self.best_shift, self.best_correlation = self.find_best_shift(max_shift_sec)
            self.audio1, self.audio2 = self.apply_shift(
                self.audio1, self.audio2, self.best_shift
            )
            shift_sec = self.best_shift / self.sample_rate1
            print()
            print("Auto-aligned:")
            print(f"  shift={shift_sec:.3f}s ({self.best_shift} samples)")
            print(f"  correlation={self.best_correlation:.4f}")

    def get_file_info(self):
        """Print basic information about both WAV files"""
        print()
        print("WAV FILE COMPARISON")
        print("───────────────────")
        print()
        print(f"File 1: {self.file1}")
        print(f"  Sample Rate: {self.sample_rate1} Hz")
        print(f"  Duration: {len(self.audio1)/self.sample_rate1:.2f} seconds")
        print(f"  Samples: {len(self.audio1)}")
        print(f"  Max Amplitude: {np.max(np.abs(self.audio1)):.4f}")
        print()
        print(f"File 2: {self.file2}")
        print(f"  Sample Rate: {self.sample_rate2} Hz")
        print(f"  Duration: {len(self.audio2)/self.sample_rate2:.2f} seconds")
        print(f"  Samples: {len(self.audio2)}")
        print(f"  Max Amplitude: {np.max(np.abs(self.audio2)):.4f}")
        print()
        if self.sample_rate1 != self.sample_rate2:
            print("⚠ Warning: Sample rates are different!")
        else:
            print("✓ Sample rates match")

    def find_best_shift(self, max_shift_sec=10):
        """Find the best shift using FFT-based cross-correlation.

        Returns the shift in samples and the best normalized correlation found.
        Positive shift: audio2 starts later than audio1.
        Negative shift: audio2 starts earlier than audio1.
        """
        max_shift_samples = int(max_shift_sec * self.sample_rate1)

        # Normalize both signals
        a1 = (self.audio1 - np.mean(self.audio1)) / (np.std(self.audio1) + 1e-10)
        a2 = (self.audio2 - np.mean(self.audio2)) / (np.std(self.audio2) + 1e-10)

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
        corr = corr[start:end]

        # Find best shift
        best_idx = np.argmax(corr)
        best_shift = best_idx - max_shift_samples

        # Normalize correlation to [-1, 1] range
        norm_a1 = np.sum(a1**2)
        norm_a2 = np.sum(a2**2)
        best_correlation = corr[best_idx] / np.sqrt(norm_a1 * norm_a2)

        return best_shift, best_correlation

    @staticmethod
    def apply_shift(a1, a2, shift):
        """Trim a1 and a2 to their overlapping region given shift.

        Positive shift: a2 lags → trim start of a1, trim end of a2.
        Negative shift: a2 leads → trim end of a1, trim start of a2.
        """
        if shift > 0:
            a1 = a1[shift:]
            a2 = a2[:-shift]
        elif shift < 0:
            s = abs(shift)
            a1 = a1[:-s]
            a2 = a2[s:]
        min_len = min(len(a1), len(a2))
        return a1[:min_len], a2[:min_len]

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
        axes[2].plot(time, self.audio1, "b-", alpha=0.5, label=axes[0].get_title())
        axes[2].plot(time, self.audio2, "r-", alpha=0.5, label=axes[0].get_title())
        shift_sec = self.best_shift / self.sample_rate1
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

        # Drift check: compare correlation at start vs end of aligned region
        segment = self.sample_rate1
        drift_available = len(self.audio1) > 2 * segment
        if drift_available:
            start_corr = np.corrcoef(self.audio1[:segment], self.audio2[:segment])[0, 1]
            end_corr = np.corrcoef(self.audio1[-segment:], self.audio2[-segment:])[0, 1]
        else:
            start_corr = end_corr = 0.

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

        if drift_available:
            drift = start_corr - end_corr
            print(f"\nDrift Check:")
            print(f"  Start correlation (first 1s): {start_corr:.4f}")
            print(f"  End correlation   (last 1s):  {end_corr:.4f}")
            if drift > 0.1:
                print(f" ⚠  Drift detected ({drift:.3f} drop) — possible clock skew")
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
        help="Max seconds to search for alignment shift, 0=no algiment (default: 10)",
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
        comparator = WavComparator(args.file1, args.file2, max_shift_sec=args.max_shift)

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
