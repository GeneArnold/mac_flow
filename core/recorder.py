"""Microphone recording via sounddevice.

Captures raw PCM audio from the selected input device and packages it as
WAV bytes ready to send to the Groq Whisper API.

Key notes:
- sounddevice talks to CoreAudio transparently on macOS.
- device_index=-1 means "system default". Any other value is a sounddevice
  device index — see Recorder.list_devices() or `python main.py --list-mics`.
- We query the device's native sample rate at init time rather than forcing
  16 kHz. Some devices (e.g. AirPods, some USB mics) don't support 16 kHz
  and raise a PortAudio error if forced. The native rate works fine for Whisper.
- Audio is accumulated as int16 numpy frames while recording; stop() assembles
  them into a proper WAV header so we can POST raw bytes to Groq.
- on_level fires per chunk with the RMS amplitude so the UI can reflect mic
  activity (e.g. by updating the menubar title).
"""

import io
import threading
import wave

import numpy as np
import sounddevice as sd


class Recorder:
    # Number of currently-open input streams, process-wide, and a lock guarding
    # it. list_devices() consults this before reinitialising PortAudio — see the
    # comment there for why that matters.
    _open_streams = 0
    _stream_lock = threading.Lock()

    def __init__(
        self, device_index: int = -1, sample_rate: int = 16000, channels: int = 1
    ):
        # -1 → sounddevice default; otherwise use the explicit device index
        self._device = None if device_index == -1 else device_index

        # Always use the device's native rate to avoid PortAudio "unsupported rate" errors.
        # Groq Whisper accepts any standard sample rate, not just 16 kHz.
        if self._device is not None:
            try:
                native_rate = int(sd.query_devices(self._device)["default_samplerate"])
                self._sample_rate = native_rate
            except Exception:
                # Device vanished (unplugged) — fall back to configured rate
                self._sample_rate = sample_rate
        else:
            self._sample_rate = sample_rate

        self._channels = channels
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._stream = None

        # Optional callback: fired each audio chunk with the RMS float.
        self.on_level: callable | None = None

    def start(self) -> None:
        """Open the input stream and begin collecting audio frames."""
        self._frames = []
        self._recording = True
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()
        with Recorder._stream_lock:
            Recorder._open_streams += 1

    def stop(self) -> bytes:
        """Close the stream and return all captured audio as WAV bytes.
        Returns empty bytes b"" if nothing was recorded.
        """
        self._recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                with Recorder._stream_lock:
                    Recorder._open_streams = max(0, Recorder._open_streams - 1)
        return self._to_wav()

    def _callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        """sounddevice audio callback — runs on a C-level audio thread.
        Keep this fast: no UI calls, no blocking, no heavy computation.
        """
        if self._recording:
            self._frames.append(indata.copy())
            if self.on_level:
                rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
                self.on_level(rms)

    def _to_wav(self) -> bytes:
        """Concatenate all recorded frames into a properly-headered WAV buffer."""
        if not self._frames:
            return b""
        audio = np.concatenate(self._frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # int16 = 2 bytes per sample
            wf.setframerate(self._sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    @staticmethod
    def list_devices() -> list[dict]:
        """Return all available input devices as a list of dicts.
        Used by the mic selector UI and the --list-mics CLI flag.
        """
        # Force PortAudio to rescan devices (it caches the list at init time).
        #
        # This is only safe when NO stream is open. sd._terminate() tears down
        # PortAudio globally; if an InputStream is live, its cffi callback is
        # still being invoked on CoreAudio's real-time thread and the state it
        # touches has just been freed. That is a hard segfault
        # (EXC_BAD_ACCESS on com.apple.audio.IOThread.client), not an
        # exception — it cannot be caught, and it takes the app down mid-
        # recording. Opening the Mic menu while dictating was enough to hit it.
        #
        # When a stream is open we skip the rescan and return the cached device
        # list instead. A slightly stale menu is a fair trade for not crashing.
        with Recorder._stream_lock:
            safe_to_rescan = Recorder._open_streams == 0
        if safe_to_rescan:
            sd._terminate()
            sd._initialize()
        devices = []
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                devices.append(
                    {
                        "index": i,
                        "name": dev["name"],
                        "channels": dev["max_input_channels"],
                        "sample_rate": int(dev["default_samplerate"]),
                    }
                )
        return devices
