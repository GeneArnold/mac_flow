"""Speech-to-text transcription.

Two interchangeable backends, both exposing `.transcribe(wav_bytes, language)`:

  MlxTranscriber   — local Whisper via mlx-whisper (Metal-accelerated on
                     Apple Silicon). Offline, free, no API key. The model is
                     downloaded from Hugging Face on first use and cached.
  GroqTranscriber  — Groq's hosted Whisper API. Needs GROQ_API_KEY.

`get_transcriber(cfg)` returns the one named by cfg["transcription"]["provider"].
The engine only touches the factory, never the concrete classes.
"""

import io
import wave

import numpy as np


def get_transcriber(cfg: dict):
    """Build the transcription backend named in config."""
    tc = cfg["transcription"]
    if tc["provider"] == "groq":
        return GroqTranscriber(
            api_key=cfg["keys"]["GROQ_API_KEY"],
            model=tc["groq_model"],
        )
    return MlxTranscriber(model=tc["mlx_model"])


def _wav_to_float32_16k(wav_bytes: bytes) -> np.ndarray:
    """Decode int16 PCM WAV bytes into a mono float32 array at 16 kHz.

    Whisper wants 16 kHz mono float32 in [-1, 1]. Doing the conversion here
    (rather than handing mlx-whisper a file path) avoids an ffmpeg dependency:
    the Recorder already gives us int16 PCM, so we only downmix and resample.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        n_channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)

    if rate != 16000 and len(audio) > 1:
        # Linear resample — plenty for speech, and keeps us scipy-free.
        n_out = int(round(len(audio) * 16000 / rate))
        x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)

    return audio


class MlxTranscriber:
    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo"):
        self._model = model

    def transcribe(self, wav_bytes: bytes, language: str | None = None) -> str:
        """Transcribe WAV bytes locally with mlx-whisper.

        The first call for a given model downloads it from Hugging Face
        (~1.6 GB for large-v3-turbo) and can take a while; subsequent calls
        hit the local cache. See Engine's background warm-up.
        """
        if not wav_bytes:
            return ""
        # Imported lazily so importing this module (e.g. for the Groq backend
        # or in tests) doesn't pull in mlx and load Metal.
        import mlx_whisper

        audio = _wav_to_float32_16k(wav_bytes)
        if audio.size == 0:
            return ""

        kwargs = {"path_or_hf_repo": self._model}
        if language:
            kwargs["language"] = language
        result = mlx_whisper.transcribe(audio, **kwargs)
        return result.get("text", "").strip()


class GroqTranscriber:
    def __init__(self, api_key: str, model: str = "whisper-large-v3"):
        # timeout=30s prevents the worker thread from hanging forever if
        # the HTTP request stalls (flaky network, stale keepalive,
        # slow server). On timeout the SDK raises, the engine catches
        # it, fires on_error, and the app recovers.
        from groq import Groq

        self._client = Groq(api_key=api_key, timeout=30.0)
        self._model = model

    def transcribe(self, wav_bytes: bytes, language: str | None = None) -> str:
        """Send WAV bytes to Groq Whisper. Returns the transcript string.

        language: optional ISO-639-1 code (e.g. "en", "es"). When None,
        Whisper auto-detects — works well for most use cases.

        Raises groq.APIError on network or auth failure — caller should handle.
        """
        if not wav_bytes:
            return ""
        # The Groq SDK needs a file-like object with a .name attribute
        # so it can infer the content type from the extension.
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "audio.wav"

        kwargs = {
            "file": audio_file,
            "model": self._model,
            "response_format": "text",
        }
        if language:
            kwargs["language"] = language

        result = self._client.audio.transcriptions.create(**kwargs)
        # SDK returns str when response_format="text", object otherwise
        return result.strip() if isinstance(result, str) else result.text.strip()
