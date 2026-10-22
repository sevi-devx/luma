
import os
import re
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

DEBUG_AUDIO = os.getenv("DEBUG_AUDIO", "1") == "1"
_debug_counter = 0


def pcm_to_array(pcm: bytes) -> np.ndarray:
    pcm = pcm[: len(pcm) // 2 * 2]
    return np.frombuffer(pcm, dtype="<i2")


def to_pcm16(samples: np.ndarray) -> bytes:
    samples = np.nan_to_num(samples)
    samples = np.clip(np.rint(samples), -32768, 32767)
    return samples.astype("<i2").tobytes()


def rms(pcm: bytes) -> float:
    samples = pcm_to_array(pcm).astype(np.float32)

    if samples.size == 0:
        return 0.0

    return float(np.sqrt(np.mean(samples * samples)))


def audio_level_dbfs(pcm: bytes) -> float:
    level = rms(pcm)

    if level <= 0:
        return -float("inf")

    return float(20 * np.log10(level / 32768.0))


def discord_to_gemini(pcm: bytes) -> bytes:
    """Convert 48 kHz stereo signed 16-bit PCM to 16 kHz mono PCM."""
    pcm = pcm[: len(pcm) // 4 * 4]

    if not pcm:
        return b""

    stereo = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)
    stereo = stereo.astype(np.float32)

    left = stereo[:, 0]
    right = stereo[:, 1]

    # Avoid cancellation when the channels have opposite polarity.
    mono = np.where(
        np.abs(left) + np.abs(right) > 0,
        (left + right) / 2.0,
        0.0,
    )

    # Band-limited downsampling: 48 kHz -> 16 kHz.
    mono_16k = resample_poly(mono, up=1, down=3)

    return to_pcm16(mono_16k)


def gemini_to_discord(pcm: bytes, rate: int = 24000) -> bytes:
    """Convert mono PCM from Gemini to 48 kHz stereo PCM for Discord."""
    if rate <= 0:
        raise ValueError("Sample rate must be positive.")

    samples = pcm_to_array(pcm).astype(np.float32)

    if samples.size == 0:
        return b""

    if rate != 48000:
        samples = resample_poly(samples, up=48000, down=rate)

    samples = np.clip(np.rint(samples), -32768, 32767).astype("<i2")

    # Duplicate each mono sample into left and right channels.
    stereo = np.column_stack((samples, samples))

    return stereo.astype("<i2").tobytes()


def parse_rate(mime_type: str | None, default: int = 24000) -> int:
    if not mime_type:
        return default

    match = re.search(r"(?:^|[;\s])rate\s*=\s*(\d+)", mime_type, re.I)

    if match:
        rate = int(match.group(1))
        return rate if rate > 0 else default

    return default


def save_debug_wav(pcm16: bytes):
    global _debug_counter

    if not DEBUG_AUDIO:
        return

    pcm16 = pcm16[: len(pcm16) // 2 * 2]

    if not pcm16:
        return

    _debug_counter += 1
    output_dir = Path("debug_audio")
    output_dir.mkdir(parents=True, exist_ok=True)

    path = output_dir / f"clip_{_debug_counter:03d}.wav"

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm16)

    print(
        f"[Debug] Saved {path} "
        f"({len(pcm16) / 32000:.2f}s, "
        f"{audio_level_dbfs(pcm16):.1f} dBFS)"
    )
