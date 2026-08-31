"""Central orchestration engine for MacFlow.

Owns every worker object and coordinates the
    record → transcribe → enhance → inject
pipeline.

Architecture:
    Engine
    ├── Recorder        — captures mic audio via sounddevice / CoreAudio
    ├── Transcriber     — local mlx-whisper or the Groq Whisper API
    ├── Enhancer        — optional cleanup via Ollama / DeepSeek / OpenRouter / Groq
    ├── TextInjector    — pastes result into the active window (NSPasteboard + Cmd+V)
    └── HotkeyListener  — watches for the hotkey combo (pynput)

Threading model (same as Linux port):
    - HotkeyListener runs its own pynput thread (always live).
    - _on_press/_on_release are called from that pynput thread.
    - _process() runs in a fresh daemon thread per recording so the hotkey
      listener is never blocked by network calls.

UI callback contract:
    Callbacks fire from worker threads. The UI layer (rumps) is responsible
    for marshalling to the main thread if the underlying toolkit requires it
    (rumps.notification and rumps.App.title assignment are thread-safe, so
    most of our callbacks can be invoked directly).

Callbacks:
    on_recording_start()            — hotkey pressed, mic open
    on_recording_stop()             — hotkey released, processing begins
    on_result(raw, final, injected) — pipeline complete
    on_error(message)               — any stage failed
    on_audio_level(rms)             — per-chunk RMS (not currently used on mac)
"""

import datetime
import threading
import time
from pathlib import Path
from typing import Callable

import config
import paths
from adapters.base import get_hotkey_listener, get_injector
from core.enhancer import Enhancer
from core.recorder import Recorder
from core.transcriber import get_transcriber
from db import history as db

# Whisper sometimes returns these when given silence or very short audio.
# Discard them rather than injecting a meaningless word into the user's document.
_WHISPER_HALLUCINATIONS = {
    ".",
    "..",
    "...",
    "you",
    "you.",
    "bye",
    "bye.",
    "goodbye",
    "goodbye.",
    "thanks",
    "thanks.",
    "thank you",
    "thank you.",
    "okay",
    "okay.",
    "ok",
    "ok.",
}


class Engine:
    def __init__(self):
        self._cfg = config.load()

        # Worker objects — built (or rebuilt) by _build_components()
        self._recorder: Recorder | None = None
        self._transcriber = None
        self._enhancer: Enhancer | None = None
        self._injector = None
        self._listener = None

        self._is_recording = threading.Event()
        self._start_time: float = 0.0

        # Tracks the newest WAV stashed when transcription failed.
        # Set by _process_wav on failure, cleared when a retry succeeds.
        # Persists across app launches via files on disk, but the
        # in-memory pointer only reflects this session.
        self._last_pending_wav: Path | None = None

        # UI callbacks — set by the app layer after construction.
        self.on_recording_start: Callable | None = None
        self.on_recording_stop: Callable | None = None
        self.on_result: Callable[[str, str, bool], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        # Fires when the pipeline finished cleanly but produced no
        # output — empty audio, hallucination-filtered transcripts.
        # The UI uses this to flip the icon back to idle so it doesn't
        # get stuck on the processing sparkle.
        self.on_idle: Callable | None = None
        self.on_audio_level: Callable[[float], None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialise the DB, build all components, and start listening for hotkeys."""
        db.init()
        self._build_components()
        self._warm_up_transcriber()
        self._listener.start(self._on_press, self._on_release)

    def stop(self) -> None:
        """Stop the hotkey listener. Safe to call when already stopped."""
        if self._listener:
            self._listener.stop()

    def reload(self) -> None:
        """Re-read config and rebuild all components.

        Called by UI settings code after any setting changes. The listener
        is restarted with the potentially new hotkey combo.
        """
        self._is_recording.clear()
        self.stop()
        self._cfg = config.load()
        self._build_components()
        self._listener.start(self._on_press, self._on_release)

    # ------------------------------------------------------------------
    # Public actions
    # ------------------------------------------------------------------

    def copy_last_transcript(self) -> str | None:
        """Put the most recent transcript on the clipboard and return it."""
        rows = db.get_recent(1)
        if not rows:
            return None
        text = rows[0]["final_text"]
        if self._injector:
            self._injector.copy_to_clipboard(text)
        return text

    def has_pending_wav(self) -> bool:
        """True if there's a saved WAV waiting to be retried."""
        return self._newest_pending_wav() is not None

    def retry_last(self) -> bool:
        """Re-run the pipeline on the most recently failed WAV.
        Returns True if a pending WAV was found (pipeline runs in
        a background thread). False if no pending WAVs exist.
        """
        wav_path = self._newest_pending_wav()
        if wav_path is None:
            return False
        self._last_pending_wav = wav_path
        try:
            wav = wav_path.read_bytes()
        except Exception as e:
            if self.on_error:
                self.on_error(f"Could not read pending WAV: {e}")
            return False
        threading.Thread(
            target=self._process_wav,
            args=(wav, 0.0, True),  # is_retry=True
            daemon=True,
        ).start()
        return True

    @property
    def cfg(self) -> dict:
        """Read-only access to the current config dict."""
        return self._cfg

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_components(self) -> None:
        """Instantiate all worker objects from the current config."""
        cfg = self._cfg
        self._recorder = Recorder(
            device_index=cfg["audio"]["device_index"],
            sample_rate=cfg["audio"]["sample_rate"],
            channels=cfg["audio"]["channels"],
        )
        self._recorder.on_level = self._level_cb

        self._transcriber = get_transcriber(cfg)

        # Enhancement provider — every backend is OpenAI-compatible, so we
        # only need its base_url, key, and model (see config.ENHANCE_PROVIDERS).
        prov = config.ENHANCE_PROVIDERS[cfg["enhancement"]["provider"]]
        key_env = prov["api_key_env"]
        # Local providers (Ollama) need no real key, but the OpenAI SDK still
        # wants a non-empty string.
        api_key = cfg["keys"].get(key_env, "") if key_env else "local"
        self._enhancer = Enhancer(
            api_key=api_key or "missing",
            model=cfg["enhancement"]["model"] or prov["model"],
            base_url=prov["base_url"],
        )
        self._injector = get_injector()
        self._listener = get_hotkey_listener(
            modifiers=cfg["hotkey"]["modifiers"],
            key=cfg["hotkey"]["key"],
        )

    def _warm_up_transcriber(self) -> None:
        """For the local mlx backend, download/load the Whisper model in the
        background right after launch so the first real dictation isn't stuck
        behind a multi-GB download. No-op for the cloud (Groq) backend.
        """
        if self._cfg["transcription"]["provider"] != "mlx":
            return

        def _run():
            try:
                import io
                import wave

                import numpy as np

                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(np.zeros(1600, dtype=np.int16).tobytes())
                self._transcriber.transcribe(buf.getvalue())
            except Exception:
                pass  # best-effort; the real call will surface any error

        threading.Thread(target=_run, daemon=True).start()

    def _level_cb(self, rms: float) -> None:
        if self.on_audio_level:
            self.on_audio_level(rms)

    def _on_press(self) -> None:
        """Called from the pynput thread when the hotkey is pressed down."""
        if self._is_recording.is_set():
            return  # guard against double-press
        self._is_recording.set()
        self._start_time = time.time()
        self._recorder.on_level = self._level_cb
        try:
            self._recorder.start()
        except Exception as e:
            # Mic permission denied or device unavailable — surface and reset
            self._is_recording.clear()
            if self.on_error:
                self.on_error(f"Mic unavailable: {e}")
            return
        if self.on_recording_start:
            self.on_recording_start()

    def _on_release(self) -> None:
        """Called from the pynput thread when the hotkey is released."""
        if not self._is_recording.is_set():
            return
        self._is_recording.clear()
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self) -> None:
        """Stop the mic, grab audio, and run the pipeline.
        Runs in its own daemon thread.
        """
        if self.on_recording_stop:
            self.on_recording_stop()

        wav = self._recorder.stop()
        duration = time.time() - self._start_time

        if not wav:
            # Silence / too short — let the UI know to clear the
            # processing icon so it doesn't get stuck.
            if self.on_idle:
                self.on_idle()
            return

        self._process_wav(wav, duration, is_retry=False)

    def _process_wav(self, wav: bytes, duration: float, is_retry: bool) -> None:
        """Pipeline: transcribe → enhance → inject → save.

        On transcribe failure, the WAV is stashed to `paths.pending_dir()`
        so nothing is lost to a transient network error. Users can
        retry via `retry_last()`.

        When `is_retry` is True and processing succeeds, the source WAV
        is deleted from the pending directory.
        """
        # --- Transcription ---
        try:
            raw = self._transcriber.transcribe(wav)
        except Exception as e:
            # Save the audio so the user can retry — unless this IS the
            # retry (in which case the file is already on disk).
            saved_path = None
            if not is_retry:
                saved_path = self._save_pending_wav(wav)
                self._last_pending_wav = saved_path
            if self.on_error:
                hint = (
                    " — Settings → Retry Last Recording"
                    if (saved_path or is_retry)
                    else ""
                )
                self.on_error(f"Transcribe failed: {e}{hint}")
            return

        # Discard Whisper hallucinations (common on silence or noise)
        if not raw.strip() or raw.strip().lower() in _WHISPER_HALLUCINATIONS:
            if is_retry:
                # Retry succeeded but audio was empty — still delete
                # the pending file since the user clearly asked to move on.
                self._delete_pending_wav()
            # Tell the UI to clear the processing icon so it doesn't
            # get stuck on the sparkle.
            if self.on_idle:
                self.on_idle()
            return

        # --- Enhancement ---
        mode = self._cfg["enhancement"]["mode"]
        try:
            final = self._enhancer.enhance(raw, mode) if mode != "raw" else raw
        except Exception as e:
            # Enhancement is optional — fall back to raw text on failure
            final = raw
            if self.on_error:
                self.on_error(f"Enhancement failed, using raw: {e}")

        # --- Output ---
        injected = False
        if self._cfg["output"]["auto_paste"]:
            injected = self._injector.inject(final)
            if not injected and self._cfg["output"]["auto_clipboard"]:
                # Paste failed (likely missing Accessibility permission) —
                # clipboard is the fallback so text isn't lost.
                self._injector.copy_to_clipboard(final)
        elif self._cfg["output"]["auto_clipboard"]:
            self._injector.copy_to_clipboard(final)
        # If both auto_paste and auto_clipboard are off, text only lives
        # in history — user retrieves it from the History menu.

        if self._cfg["output"]["save_history"]:
            db.save(raw, final, mode, duration_s=duration, injected=injected)

        if is_retry:
            self._delete_pending_wav()

        if self.on_result:
            self.on_result(raw, final, injected)

    # ------------------------------------------------------------------
    # Pending-WAV helpers (used when transcription fails so audio isn't lost)
    # ------------------------------------------------------------------

    def _save_pending_wav(self, wav: bytes) -> Path | None:
        """Write the WAV bytes to the pending dir with a timestamped name."""
        try:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = paths.pending_dir() / f"recording-{ts}.wav"
            path.write_bytes(wav)
            return path
        except Exception:
            return None

    def _newest_pending_wav(self) -> Path | None:
        """Return the most recently-saved pending WAV, or None."""
        if self._last_pending_wav and self._last_pending_wav.exists():
            return self._last_pending_wav
        try:
            wavs = sorted(paths.pending_dir().glob("*.wav"))
        except Exception:
            return None
        return wavs[-1] if wavs else None

    def _delete_pending_wav(self) -> None:
        """Remove the pending WAV that was just successfully retried."""
        if not self._last_pending_wav:
            return
        try:
            self._last_pending_wav.unlink(missing_ok=True)
        except Exception:
            pass
        self._last_pending_wav = None
