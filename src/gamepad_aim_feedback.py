"""Audio and haptics for the aim tests."""

import io
import math
import struct
import threading
import tkinter as tk
import wave

try:
    import winsound
except ImportError:
    winsound = None


CONTACT_INTERVAL = 0.12
RELEASE_RETRY_MS = 15
RELEASE_RETRIES = 2
_CUE_PARAMETERS = {
    "contact": (0.029, 720, 1440, 7500, 0.0),
    "hit": (0.078, 190, 740, 19000, 0.0),
    "miss": (0.056, 460, 920, 10000, 0.50),
}


def _cue_wave(cue):
    sample_rate = 22050
    duration, pitch, accent, amplitude, pitch_drop = _CUE_PARAMETERS[cue]

    frame_count = round(duration * sample_rate)
    samples = []
    for frame in range(frame_count):
        progress = frame / frame_count
        phase = math.tau * frame / sample_rate * (1.0 - 0.5 * pitch_drop * progress)
        envelope = min(1.0, frame / 14) * (1.0 - progress) ** 4
        tone = 0.77 * math.sin(phase * pitch) + 0.23 * math.sin(phase * accent)
        samples.append(round(amplitude * envelope * tone))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        output.writeframes(struct.pack(f"<{frame_count}h", *samples))
    return buffer.getvalue()


class _CuePlayer:
    """Play in-memory WAVs off the Tk thread, with at most one pending cue."""

    def __init__(self):
        self._condition = threading.Condition()
        self._pending = None
        self._playing_shot = False
        self._worker = None
        self._closed = False

    def play(self, cue):
        if winsound is None:
            return
        with self._condition:
            if self._closed:
                return
            if cue == "contact" and (self._playing_shot or self._pending in ("hit", "miss")):
                return
            self._pending = cue
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, args=(winsound,), daemon=True)
                try:
                    self._worker.start()
                except RuntimeError:
                    self._closed = True
                    self._pending = None
                    return
            self._condition.notify()

    def clear(self, contact_only=False):
        if self._worker is None:
            return
        with self._condition:
            if not contact_only or self._pending == "contact":
                self._pending = None

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _run(self, audio):
        cues = {}
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                cue = self._pending
                self._pending = None
                self._playing_shot = cue != "contact"
            if cue not in cues:
                cues[cue] = _cue_wave(cue)
            try:
                audio.PlaySound(cues[cue], audio.SND_MEMORY | audio.SND_NODEFAULT)
            except (RuntimeError, OSError):
                # A missing or unavailable sound device must not affect haptics.
                self.close()
                return
            with self._condition:
                self._playing_shot = False


class AimFeedback:
    def __init__(self, parent, set_rumble=None):
        self._parent = parent
        self._set_rumble = set_rumble
        self._audio = _CuePlayer()
        self._next_contact = None
        self._release_job = None
        self._release_pending = False
        self._release_retries = 0
        self._pulse_hit = None
        self._closed = False

    def contact(self, on_target, strength, sound, now):
        if self._closed:
            return
        if not on_target:
            if self._next_contact is not None:
                self._next_contact = None
                self._audio.clear(contact_only=True)
            if self._pulse_hit is False:
                self._stop_pulse()
            return

        if strength <= 0 and self._pulse_hit is False:
            self._stop_pulse()
        if self._next_contact is not None and now < self._next_contact:
            return
        self._next_contact = now + CONTACT_INTERVAL
        if self._pulse_hit is not True:
            self._pulse(strength, hit=False)
        if sound:
            self._audio.play("contact")
        else:
            self._audio.clear(contact_only=True)

    def hit(self, strength, sound):
        if self._closed:
            return
        self._pulse(strength, hit=True)
        if sound:
            self._audio.play("hit")

    def miss(self, sound):
        if sound and not self._closed:
            self._audio.play("miss")

    def stop(self):
        self._next_contact = None
        self._audio.clear()
        self._stop_pulse()

    def close(self):
        self._closed = True
        self.stop()
        self._audio.close()

    def _pulse(self, strength, hit):
        strength = max(0.0, min(strength, 1.0))
        if self._set_rumble is None or strength == 0:
            return
        left, right, duration = (1.0, 0.72, 80) if hit else (0.14, 0.42, 26)
        if self._set_rumble(left * strength, right * strength) is False:
            return
        self._cancel_release()
        self._pulse_hit = hit
        self._release_pending = True
        self._release_retries = RELEASE_RETRIES
        try:
            self._release_job = self._parent.after(duration, self._release_motors)
        except tk.TclError:
            self._release_motors()

    def _cancel_release(self):
        if self._release_job is not None:
            try:
                self._parent.after_cancel(self._release_job)
            except tk.TclError:
                pass
            self._release_job = None

    def _release_motors(self):
        self._release_job = None
        self._pulse_hit = None
        if not self._release_pending:
            return
        self._release_pending = self._set_rumble(0.0, 0.0) is False
        if self._release_pending and self._release_retries and not self._closed:
            self._release_retries -= 1
            try:
                self._release_job = self._parent.after(RELEASE_RETRY_MS, self._release_motors)
            except tk.TclError:
                pass

    def _stop_pulse(self):
        self._cancel_release()
        self._release_motors()
