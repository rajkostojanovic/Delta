from __future__ import annotations

import math
import os
import ctypes
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from clocking import ClockControls
from pitch_curve import interpolate_pitch

import numpy as np
# Must precede the first sounddevice import, including when app.py is launched directly.
os.environ["SD_ENABLE_ASIO"] = "1"
import sounddevice as sd
import soundfile as sf

RATE = 48000
MAX_VOICES = 48
MAX_FILE_BYTES = 192 * 1024 * 1024
BASS_SMOOTH_SECONDS = 0.035


def asio_channel_names(device):
    info = sd.query_devices(device, "output")
    names = [f"Channel {i + 1}" for i in range(info["max_output_channels"])]
    if sd.query_hostapis(info["hostapi"])["name"] != "ASIO":
        return names
    try:
        library = ctypes.CDLL(sd._libname)
        get_name = library.PaAsio_GetOutputChannelName
        get_name.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        get_name.restype = ctypes.c_int
        for index in range(len(names)):
            value = ctypes.c_char_p()
            if get_name(device, index, ctypes.byref(value)) == 0 and value.value:
                names[index] = value.value.decode("utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    return names


def show_asio_panel(device, window_handle):
    info = sd.query_devices(device, "output")
    if sd.query_hostapis(info["hostapi"])["name"] != "ASIO":
        raise ValueError("Select an ASIO output first.")
    library = ctypes.CDLL(sd._libname)
    panel = library.PaAsio_ShowControlPanel
    panel.argtypes = [ctypes.c_int, ctypes.c_void_p]
    panel.restype = ctypes.c_int
    result = panel(device, window_handle)
    if result != 0:
        raise RuntimeError(f"ASIO control panel error {result}")


@dataclass
class Sample:
    data: np.ndarray
    rate: int
    path: str


def load_sample(path: str) -> Sample:
    info = sf.info(path)
    if info.frames <= 0 or info.samplerate <= 0:
        raise ValueError("This file contains no playable audio.")
    if info.channels not in (1, 2):
        raise ValueError("Please load a mono or stereo recording.")
    if info.frames * 2 * 4 > MAX_FILE_BYTES:
        raise ValueError("This recording is too large. Maximum decoded size: 192 MB.")
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    if not np.isfinite(data).all():
        raise ValueError("The recording contains invalid audio values.")
    data = np.ascontiguousarray(data)
    data.flags.writeable = False
    return Sample(data, rate, str(Path(path).resolve()))


@dataclass
class Lane:
    name: str
    kind: str
    active: bool = True
    smooth_pitch: bool = False
    sample: Sample | None = None
    count: int = 0
    take_count: int = 3
    takes: list[tuple[list[int], int]] = field(default_factory=list)
    state: str = "EMPTY"
    hits: list[int] = field(default_factory=list)
    started: float | None = None
    second_started: float | None = None
    length: int = 0
    origin: int = 0
    paused_phase: int = 0
    playing: bool = False
    adding_hits: bool = False
    original_hits: tuple[list[int], int] | None = None
    pitch: float = 0.0
    volume: float = 1.0
    tempo: float = 1.0
    current_volume: float = 1.0
    current_pitch: float = 0.0
    repeat: bool = False
    history: list[tuple[int, float]] = field(default_factory=list)

    previous_loop: tuple[list[int], int] | None = None
    held_recording: bool = False
    recording_was_playing: bool = False
    recording_origin: int = 0
    recording_paused_phase: int = 0
    pitch_events: list[tuple[int, float]] = field(default_factory=list)
    pitch_curve: str = "smooth"
    pitch_modes: dict[int, str] = field(default_factory=dict)
    pitch_loop_enabled: bool = False
    loop_paused_phase: int = 0
    previous_pitch_state: dict | None = None
    beats_per_loop: int = 0

    def timing(self):
        if self.previous_loop is not None:
            hits, length = self.previous_loop
            return [round(hit / self.tempo) for hit in hits], max(1, round(length / self.tempo))
        if not self.length:
            return self.hits, 0
        return [round(hit / self.tempo) for hit in self.hits if hit < self.length], max(1, round(self.length / self.tempo))


@dataclass
class Voice:
    lane: int
    sample: Sample
    start: int
    position: float = 0.0
    continuous: bool = False


class Engine(ClockControls):
    def __init__(self, default_lanes=False):
        self.lock = threading.RLock()
        self.lanes = []
        if default_lanes:
            # The inherited Gamma fixture remains available for regression checks.
            self.lanes = [Lane("Ambience", "ambient"), Lane("Kick", "rhythm"),
                          Lane("Impulse", "rhythm"), Lane("Bass", "pitch", smooth_pitch=True, pitch_curve="step"), Lane("Melody", "pitch", pitch_curve="step")]
            self.lanes[4].pitch = 12.0
            self.lanes[4].current_pitch = 12.0
        self.frame = 0
        self.rate = RATE
        self.voices: list[Voice] = []
        self.stream = None
        self.error = ""
        self.underruns = 0
        self.voice_drops = 0
        self.init_clock()

    def add_lane(self, kind):
        if kind not in ("ambient", "pitch", "rhythm"):
            raise ValueError("Choose a sound, melody, or rhythm lane.")
        with self.lock:
            name = {"ambient": "Sound", "pitch": "Melody", "rhythm": "Rhythm"}[kind]
            number = 1 + sum(lane.kind == kind for lane in self.lanes)
            self.lanes.append(Lane(f"{name} {number}", kind))
            return len(self.lanes) - 1

    def remove_lane(self, index):
        with self.lock:
            lane = self.lanes[index]
            if self.master_index == index:
                self.master_index = None
            self._silence(index)
            # Stable slots keep queued file-load callbacks and playing voice IDs valid.
            # Release the sample and recordings without shifting any other lane.
            self.lanes[index] = Lane(lane.name, lane.kind, active=False)

    def open(self, device=None, pair=0):
        self.close()
        self.error = ""
        self.underruns = 0
        self.voice_drops = 0
        info = sd.query_devices(device, "output")
        is_asio = sd.query_hostapis(info["hostapi"])["name"] == "ASIO"
        if pair < 0 or pair + 1 >= info["max_output_channels"]:
            raise ValueError("The selected stereo output pair is unavailable.")
        if pair and not is_asio:
            raise ValueError("Channel selection is supported for ASIO outputs only.")
        extra = sd.AsioSettings(channel_selectors=[pair, pair + 1]) if is_asio else None
        failures = []
        rates = dict.fromkeys([int(info["default_samplerate"]), RATE, 44100])
        for rate in rates:
            stream = None
            try:
                sd.check_output_settings(device=device, samplerate=rate, channels=2,
                                         dtype="float32", extra_settings=extra)
                stream = sd.OutputStream(device=device, samplerate=rate, channels=2,
                                         dtype="float32", blocksize=0, latency="low",
                                         extra_settings=extra, callback=self.callback)
                self._set_rate(rate)
                stream.start()
            except Exception as exc:
                if stream is not None:
                    stream.close()
                failures.append(f"{rate} Hz: {exc}")
            else:
                self.stream = stream
                return
        raise RuntimeError(f"{info['name']}: " + " | ".join(failures))

    def _set_rate(self, rate):
        with self.lock:
            factor = rate / self.rate
            self.frame = round(self.frame * factor)
            self.master_origin = round(self.master_origin * factor)
            for lane in self.lanes:
                if lane.previous_loop is not None:
                    hits, length = lane.previous_loop
                    lane.previous_loop = ([round(hit * factor) for hit in hits], round(length * factor))
                lane.hits = [round(hit * factor) for hit in lane.hits]
                lane.takes = [([round(hit * factor) for hit in hits], round(length * factor))
                              for hits, length in lane.takes]
                lane.length = round(lane.length * factor)
                lane.origin = round(lane.origin * factor)
                lane.pitch_events = [(round(at * factor), pitch) for at, pitch in lane.pitch_events]
                lane.loop_paused_phase = round(lane.loop_paused_phase * factor)
                if lane.kind == "rhythm":
                    lane.paused_phase = round(lane.paused_phase * factor)
                lane.history = [(round(frame * factor), pitch) for frame, pitch in lane.history]
            self.rate = rate

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.stop_all()

    def stop_all(self):
        with self.lock:
            for index, lane in enumerate(self.lanes):
                if lane.held_recording:
                    self.cancel_loop(index)
            self.voices.clear()
            for index, lane in enumerate(self.lanes):
                lane.playing = False
                self.set_add_hits(index, False)
                lane.paused_phase = 0
                lane.loop_paused_phase = 0
                if lane.previous_loop is not None:
                    lane.hits, lane.length = lane.previous_loop
                    lane.previous_loop = None
                    lane.takes.clear()
                    lane.started = None
                    lane.state = "LOOP"
                elif lane.state == "RECORDING":
                    lane.hits.clear()
                    lane.takes.clear()
                    lane.started = None
                    lane.state = "ARMED"

    def set_sample(self, index, sample):
        with self.lock:
            total = sum(x.sample.data.nbytes for i, x in enumerate(self.lanes)
                        if i != index and x.sample is not None)
            if total + sample.data.nbytes > 384 * 1024 * 1024:
                raise ValueError("Loaded recordings exceed the 384 MB session limit.")
            lane = self.lanes[index]
            if lane.held_recording:
                self.cancel_loop(index)
            self._silence(index)
            self.set_add_hits(index, False)
            lane.previous_loop = None
            lane.sample = sample
            lane.playing = False
            lane.hits.clear()
            lane.takes.clear()
            lane.length = 0
            lane.started = None
            lane.paused_phase = 0
            lane.history.clear()
            lane.pitch_events.clear()
            lane.pitch_modes.clear()
            lane.pitch_loop_enabled = False
            lane.loop_paused_phase = 0
            lane.previous_pitch_state = None
            lane.current_pitch = lane.pitch
            lane.state = "READY"

    def _silence(self, index):
        self.voices = [v for v in self.voices if v.lane != index]

    def begin_loop(self, index, now=None):
        """L down starts the loop clock; Enter taps are recorded without a count."""
        with self.lock:
            lane = self.lanes[index]
            if not lane.active or lane.kind not in ("rhythm", "pitch") or lane.sample is None or lane.held_recording:
                return False
            if lane.kind == "pitch":
                lane.previous_pitch_state = dict(events=list(lane.pitch_events), modes=dict(lane.pitch_modes), length=lane.length,
                    enabled=lane.pitch_loop_enabled, origin=lane.origin, phase=lane.loop_paused_phase,
                    pitch=lane.pitch, current_pitch=lane.current_pitch, playing=lane.playing,
                    state=lane.state, source_position=lane.paused_phase)
                lane.pitch_events = [(0, lane.current_pitch)]
                lane.pitch_modes = {}
                lane.pitch = lane.current_pitch
                lane.length = 0
                lane.pitch_loop_enabled = False
                lane.started = time.perf_counter() if now is None else now
                lane.held_recording = True
                lane.state = "RECORDING"
                if not lane.playing:
                    self.toggle(index)
                lane.origin = self.frame
                return True
            self.set_add_hits(index, False)
            lane.previous_loop = (list(lane.hits), lane.length) if lane.length else None
            lane.recording_was_playing = lane.playing
            lane.recording_origin = lane.origin
            lane.recording_paused_phase = lane.paused_phase
            self._silence(index)
            lane.playing = False
            lane.hits = []
            lane.takes.clear()
            lane.length = 0
            lane.origin = self.frame
            lane.started = time.perf_counter() if now is None else now
            lane.held_recording = True
            lane.state = "RECORDING"
            return True

    def finish_loop(self, index, now=None):
        """L up sets the out point, preserving both the leading and trailing gaps."""
        with self.lock:
            lane = self.lanes[index]
            if not lane.held_recording:
                return
            if lane.kind == "pitch":
                now = time.perf_counter() if now is None else now
                lane.length = max(1, lane.pitch_events[-1][0] + 1, round((now - lane.started) * self.rate))
                lane.started = None
                lane.held_recording = False
                lane.previous_pitch_state = None
                lane.pitch_loop_enabled = True
                lane.loop_paused_phase = 0
                lane.origin = self.frame
                lane.pitch = lane.current_pitch = lane.pitch_events[0][1]
                lane.state = "LOOP"
                if not lane.playing:
                    self.toggle(index)
                self.recorded_clock(index)
                return
            if not lane.hits:
                self.cancel_loop(index)
                return
            now = time.perf_counter() if now is None else now
            lane.length = max(1, lane.hits[-1] + 1, round((now - lane.started) * self.rate))
            lane.count = len(lane.hits)
            lane.started = None
            lane.held_recording = False
            lane.previous_loop = None
            lane.origin = self.frame
            lane.paused_phase = 0
            lane.playing = True
            lane.state = "LOOP"
            self.recorded_clock(index)

    def cancel_loop(self, index):
        with self.lock:
            lane = self.lanes[index]
            if not lane.held_recording:
                return
            if lane.kind == "pitch":
                old = lane.previous_pitch_state
                lane.held_recording = False
                lane.started = None
                lane.previous_pitch_state = None
                lane.pitch_events = old["events"]
                lane.pitch_modes = old["modes"]
                lane.length, lane.origin = old["length"], old["origin"]
                lane.pitch_loop_enabled, lane.loop_paused_phase = old["enabled"], old["phase"]
                lane.pitch, lane.current_pitch = old["pitch"], old["current_pitch"]
                lane.state = old["state"]
                if not old["playing"]:
                    self._silence(index)
                    lane.playing = False
                    lane.paused_phase = old["source_position"]
                elif not lane.playing:
                    lane.playing = True
                    self._voice(index, self.frame, True, lane.paused_phase)
                return
            self._silence(index)
            lane.held_recording = False
            lane.started = None
            if lane.previous_loop is not None:
                lane.hits, lane.length = lane.previous_loop
                lane.previous_loop = None
                lane.count = len(lane.hits)
                lane.playing = lane.recording_was_playing
                lane.origin = lane.recording_origin
                lane.paused_phase = lane.recording_paused_phase
                lane.state = "LOOP"
            else:
                lane.hits = []
                lane.count = 0
                lane.length = 0
                lane.playing = False
                lane.state = "READY" if lane.sample else "EMPTY"

    def reset_count(self, index):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm":
                return
            self._silence(index)
            lane.playing = False
            self.set_add_hits(index, False)
            lane.previous_loop = None
            lane.count = 0
            lane.hits.clear()
            lane.takes.clear()
            lane.length = 0
            lane.started = None
            lane.state = "COUNTING" if lane.sample else "EMPTY"

    def add_count(self, index):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm" or lane.sample is None:
                return
            if lane.state != "COUNTING":
                self.reset_count(index)
            lane.count = min(128, lane.count + 1)

    def confirm(self, index):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind == "rhythm" and lane.count:
                self.rerecord(index)

    def set_takes(self, index, count):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm" or count not in (2, 3, 4):
                raise ValueError("Choose 2, 3, or 4 takes for a rhythm lane.")
            if lane.state == "RECORDING":
                raise ValueError("Finish or restart recording before changing the take count.")
            lane.take_count = count

    def rerecord(self, index):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm":
                return
            self.set_add_hits(index, False)
            if lane.previous_loop is None and lane.length and lane.sample and lane.count:
                lane.previous_loop = (list(lane.hits), lane.length)
            if lane.previous_loop is None:
                self._silence(index)
                lane.playing = False
                lane.length = 0
                lane.paused_phase = 0
            lane.hits.clear()
            lane.takes.clear()
            lane.started = None
            lane.second_started = None
            lane.state = "ARMED" if lane.sample and lane.count else "COUNTING" if lane.sample else "EMPTY"

    def set_add_hits(self, index, enabled):
        with self.lock:
            lane = self.lanes[index]
            enabled = bool(enabled and lane.kind == "rhythm" and lane.state == "LOOP"
                           and lane.length and lane.sample)
            if enabled and not lane.adding_hits:
                lane.original_hits = (list(lane.hits), lane.count)
            elif not enabled and lane.original_hits is not None:
                lane.hits, lane.count = lane.original_hits
                lane.original_hits = None
            lane.adding_hits = enabled

    def _add_hit(self, index):
        lane = self.lanes[index]
        if not lane.playing or lane.state != "LOOP" or len(lane.hits) >= 128:
            return
        _, length = lane.timing()
        phase = (self.frame - lane.origin) % length
        hit = min(lane.length - 1, round(phase * lane.tempo))
        if hit in lane.hits:
            return
        lane.hits.append(hit)
        lane.hits.sort()
        lane.count = len(lane.hits)
        # The scheduler sounds an exact-position tap once. Monitor rounding-back taps now.
        if (round(hit / lane.tempo) - phase) % length > 1:
            self._voice(index, self.frame)

    def tap(self, index, now=None):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm" or lane.sample is None:
                return
            if lane.held_recording:
                if len(lane.hits) < 128:
                    now = time.perf_counter() if now is None else now
                    lane.hits.append(max(0, round((now - lane.started) * self.rate)))
                    self._voice(index, self.frame)
                return
            if index == self.master_index and lane.state == "LOOP":
                if lane.playing:
                    self.add_loop_hit(index)
                else:
                    self.toggle(index)
                return
            if lane.adding_hits:
                self._add_hit(index)
                return
            if lane.state not in ("ARMED", "RECORDING"):
                return
            now = time.perf_counter() if now is None else now
            if lane.started is None:
                lane.started = now
                if lane.previous_loop is None:
                    lane.origin = self.frame
                lane.state = "RECORDING"
            elapsed = max(0, round((now - lane.started) * self.rate))
            if len(lane.hits) < lane.count:
                lane.hits.append(elapsed)
                self._voice(index, self.frame)
            else:
                if elapsed <= lane.hits[-1] or elapsed < self.rate // 20:
                    return
                lane.takes.append((list(lane.hits), elapsed))
                if len(lane.takes) == 1:
                    lane.second_started = now
                # A boundary closes this take and sounds the first hit of the next.
                lane.hits = [0]
                lane.started = now
                if lane.previous_loop is None:
                    lane.origin = self.frame
                self._voice(index, self.frame)
            if len(lane.takes) == lane.take_count - 1 and len(lane.hits) == lane.count:
                completed = len(lane.takes)
                final_hits = lane.hits
                last = round(sum(take[0][-1] for take in lane.takes) / completed)
                lane.hits = [min(last, round((sum(take[0][i] for take in lane.takes) + final_hits[i])
                                            / lane.take_count)) for i in range(lane.count - 1)] + [last]
                lane.length = (lane.takes[1][1] if lane.take_count == 3
                               else round(sum(take[1] for take in lane.takes) / completed))
                lane.hits = [min(hit, lane.length - 1) for hit in lane.hits]
                lane.started = None
                lane.previous_loop = None
                # Preserve the second attempt's phase; the final tap already sounded.
                hits, length = lane.timing()
                if lane.take_count == 3 and lane.second_started is not None:
                    phase = round((now - lane.second_started) * self.rate / lane.tempo) % length
                    lane.origin = self.frame + (length - phase)
                else:
                    lane.origin = self.frame + max(1, length - hits[-1])
                lane.paused_phase = 0
                lane.playing = True
                lane.state = "LOOP"

    def _voice(self, index, start, continuous=False, position=0.0):
        sample = self.lanes[index].sample
        if sample is not None:
            if len(self.voices) >= MAX_VOICES:
                oldest_hit = next((i for i, voice in enumerate(self.voices) if not voice.continuous), 0)
                self.voices.pop(oldest_hit)
                self.voice_drops += 1
            self.voices.append(Voice(index, sample, start, position, continuous))

    def toggle(self, index):
        with self.lock:
            lane = self.lanes[index]
            if lane.sample is None:
                return
            if lane.kind == "rhythm":
                if not lane.length:
                    return
                if lane.playing:
                    lane.paused_phase = (self.frame - lane.origin) % lane.timing()[1]
                    self._silence(index)
                else:
                    lane.origin = self.frame - lane.paused_phase
                    if not lane.paused_phase:
                        self.start_on_clock(index)
                    elif index == self.master_index:
                        self.master_origin = lane.origin
                lane.playing = not lane.playing
            elif lane.playing:
                if lane.kind == "pitch" and lane.pitch_loop_enabled and lane.length:
                    lane.loop_paused_phase = (self.frame - lane.origin) % lane.timing()[1]
                voices = [v for v in self.voices if v.lane == index]
                lane.paused_phase = voices[0].position if voices else 0
                lane.playing = False
                self._silence(index)
            else:
                lane.playing = True
                lane.origin = self.frame - (lane.loop_paused_phase if lane.kind == "pitch" and lane.pitch_loop_enabled else 0)
                if not lane.loop_paused_phase and not lane.held_recording:
                    self.start_on_clock(index)
                self._voice(index, max(self.frame, lane.origin), True, lane.paused_phase)

    def restart(self, index):
        with self.lock:
            lane = self.lanes[index]
            lane.playing = False
            lane.paused_phase = 0
            lane.loop_paused_phase = 0
            self._silence(index)
            self.toggle(index)

    def set_tempo(self, index, value):
        with self.lock:
            lane = self.lanes[index]
            old_length = lane.timing()[1]
            lane.tempo = min(1.5, max(.5, float(value)))
            new_length = lane.timing()[1]
            if (lane.kind == "rhythm" or lane.pitch_loop_enabled) and old_length:
                if lane.playing:
                    phase = (self.frame - lane.origin) % old_length
                    lane.origin = self.frame - round(phase * new_length / old_length)
                else:
                    if lane.kind == "pitch":
                        lane.loop_paused_phase = round(lane.loop_paused_phase * new_length / old_length)
                    else:
                        lane.paused_phase = round(lane.paused_phase * new_length / old_length)

    def set_pitch_loop(self, index, enabled):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "pitch" or lane.held_recording:
                return
            enabled = bool(enabled and lane.pitch_events and lane.length)
            if lane.pitch_loop_enabled and not enabled:
                lane.loop_paused_phase = (self.frame - lane.origin) % lane.timing()[1] if lane.playing else lane.loop_paused_phase
            elif enabled and not lane.pitch_loop_enabled:
                lane.origin = self.frame - lane.loop_paused_phase
            lane.pitch_loop_enabled = enabled

    def set_volume(self, index, value):
        with self.lock:
            lane = self.lanes[index]
            lane.volume = min(1.0, max(0.0, float(value)))
            if not any(voice.lane == index for voice in self.voices):
                lane.current_volume = lane.volume

    def set_pitch_curve(self, index, mode):
        if mode not in ("smooth", "linear", "step"):
            raise ValueError("Choose Smooth, Linear, or Steps.")
        with self.lock:
            lane = self.lanes[index]
            lane.pitch_curve = mode
            lane.pitch_modes.clear()

    def toggle_pitch_interval(self, index, interval):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "pitch" or lane.held_recording or not lane.pitch_loop_enabled or not 0 <= interval < len(lane.pitch_events):
                return None
            current = lane.pitch_modes.get(interval, lane.pitch_curve)
            mode = "smooth" if current == "linear" else "linear"
            lane.pitch_modes[interval] = mode
            return mode

    def set_note_pitch(self, index, note, value):
        with self.lock:
            lane = self.lanes[index]
            if not lane.active or lane.kind != "pitch" or lane.held_recording or not lane.pitch_loop_enabled or not 0 <= note < len(lane.pitch_events):
                return False
            at, _ = lane.pitch_events[note]
            lane.pitch_events[note] = (at, max(-24., min(24., float(value))))
            return True

    def set_base_pitch(self, index, value):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "pitch":
                return
            if lane.pitch_loop_enabled and lane.pitch_events and not lane.held_recording:
                notes = [pitch for _, pitch in lane.pitch_events]
                shift = max(-24 - min(notes), min(24 - max(notes), value - notes[0]))
                lane.pitch_events = [(at, pitch + shift) for at, pitch in lane.pitch_events]
                lane.pitch += shift
                lane.current_pitch += shift
            else:
                self.pitch(index, value - lane.pitch)

    def pitch(self, index, change, now=None):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind == "pitch":
                if lane.pitch_loop_enabled:
                    self.set_pitch_loop(index, False)
                lane.history.append((self.frame, lane.current_pitch))
                lane.pitch = min(24.0, max(-24.0, lane.pitch + change))
                if lane.held_recording:
                    now = time.perf_counter() if now is None else now
                    at = max(0, round((now - lane.started) * self.rate))
                    lane.pitch_events.append((at, lane.pitch))
                if not lane.smooth_pitch or not lane.playing:
                    lane.current_pitch = lane.pitch
                    lane.history.append((self.frame, lane.current_pitch))
                lane.history = lane.history[-2000:]

    def render(self, frames):
        out = np.zeros((frames, 2), dtype=np.float32)
        with self.lock:
            end = self.frame + frames
            gains = []
            for lane in self.lanes:
                if frames and abs(lane.volume - lane.current_volume) > 1e-6:
                    gain = lane.volume + (lane.current_volume - lane.volume) * np.exp(
                        -np.arange(1, frames + 1) / (self.rate * .01))
                    lane.current_volume = float(gain[-1])
                    gains.append(gain)
                else:
                    lane.current_volume = lane.volume
                    gains.append(lane.volume)
            pitch_rates = {}
            for index, lane in enumerate(self.lanes):
                if not (frames and lane.playing and lane.pitch_loop_enabled and lane.length and lane.pitch_events):
                    continue
                length = lane.timing()[1]
                phases = (np.arange(frames, dtype=np.int64) + self.frame - lane.origin) % length
                pitches = interpolate_pitch(lane.pitch_events, lane.length, phases, lane.tempo, lane.pitch_curve, lane.pitch_modes)
                pitch_rates[index] = np.exp2(pitches / 12)
                lane.pitch = lane.current_pitch = float(pitches[-1])
            for index, bass in enumerate(self.lanes):
                if index in pitch_rates or not (bass.smooth_pitch and frames and abs(bass.current_pitch - bass.pitch) > 1e-6):
                    continue
                # Smooth in audio time; integrate playback rates to keep the sample position continuous.
                t = np.arange(1, frames + 1) / self.rate
                curve = bass.pitch + (bass.current_pitch - bass.pitch) * np.exp(-t / BASS_SMOOTH_SECONDS)
                pitch_rates[index] = np.exp2(curve / 12)
                bass.current_pitch = float(curve[-1])
                stride = max(1, round(self.rate * .008))
                bass.history.extend((self.frame + n + 1, float(curve[n])) for n in range(0, frames, stride))
                bass.history.append((end, bass.current_pitch))
                bass.history = bass.history[-2000:]
            for i, lane in enumerate(self.lanes):
                if lane.kind == "rhythm" and lane.playing and lane.length:
                    hits, length = lane.timing()
                    for hit in hits:
                        first = lane.origin + hit
                        cycle = max(0, (self.frame - first + length - 1) // length)
                        when = first + cycle * length
                        while when < end:
                            self._voice(i, when)
                            when += length
            keep = []
            for voice in self.voices:
                lane = self.lanes[voice.lane]
                offset = max(0, voice.start - self.frame)
                if offset >= frames:
                    keep.append(voice)
                    continue
                rate = voice.sample.rate / self.rate
                if lane.kind != "rhythm":
                    rate *= lane.tempo
                steps = None
                if lane.kind == "pitch":
                    if voice.lane in pitch_rates:
                        steps = rate * pitch_rates[voice.lane][offset:]
                    rate *= 2 ** (lane.current_pitch / 12)
                size = len(voice.sample.data)
                looping = voice.continuous and (lane.kind == "pitch" or lane.repeat)
                count = frames - offset
                if not looping and steps is None:
                    count = min(count, max(0, math.ceil((size - voice.position) / rate)))
                if count:
                    if steps is None:
                        positions = voice.position + np.arange(count) * rate
                        advance = count * rate
                    else:
                        positions = voice.position + np.concatenate(([0.0], np.cumsum(steps[:-1])))
                        if not looping:
                            count = int(np.searchsorted(positions, size))
                            positions = positions[:count]
                        advance = float(np.sum(steps[:count]))
                    if looping:
                        positions %= size
                    left = np.minimum(positions.astype(np.int64), size - 1)
                    right = (left + 1) % size if looping else np.minimum(left + 1, size - 1)
                    fraction = (positions - left).astype(np.float32)[:, None]
                    chunk = voice.sample.data[left] * (1 - fraction) + voice.sample.data[right] * fraction
                    gain = gains[voice.lane]
                    if isinstance(gain, np.ndarray):
                        gain = gain[offset:offset + count, None]
                    out[offset:offset + count] += chunk * (0.35 * gain)
                    voice.position += advance
                    if looping:
                        voice.position %= size
                if looping or voice.position < size:
                    keep.append(voice)
                elif voice.continuous:
                    lane.playing = False
                    lane.paused_phase = 0
            self.voices = keep
            self.frame = end
        np.clip(out, -1, 1, out=out)
        return out

    def callback(self, outdata, frames, timing, status):
        try:
            if status.output_underflow:
                self.underruns += 1
            outdata[:] = self.render(frames)
        except Exception as exc:
            outdata.fill(0)
            self.error = f"Audio stopped: {type(exc).__name__}: {exc}"
            self.stop_all()
            raise sd.CallbackAbort from exc

    def snapshot(self):
        with self.lock:
            result = []
            for i, lane in enumerate(self.lanes):
                hits, length = lane.timing()
                notes = [pitch for _, pitch in lane.pitch_events] if lane.pitch_loop_enabled and not lane.held_recording else []
                base = notes[0] if notes else lane.pitch
                positions = [v.position for v in self.voices if v.lane == i and v.continuous]
                result.append(dict(name=lane.name, kind=lane.kind, count=lane.count,
                                   take_count=lane.take_count, completed_takes=len(lane.takes),
                                   state=lane.state, hits=list(lane.hits) if lane.state in ("ARMED", "RECORDING") else list(hits), length=0 if lane.held_recording else length, tempo=lane.tempo,
                                   origin=lane.origin, playing=lane.playing, adding_hits=lane.adding_hits, pitch=lane.current_pitch,
                                   pitch_target=lane.pitch,
                                   base_pitch=base, base_min=base - 24 - min(notes) if notes else -24,
                                   base_max=base + 24 - max(notes) if notes else 24,
                                   history=list(lane.history), paused_phase=lane.paused_phase,
                                   pitch_events=list(lane.pitch_events), pitch_loop_enabled=lane.pitch_loop_enabled,
                                   pitch_curve=lane.pitch_curve, pitch_modes=dict(lane.pitch_modes), raw_length=lane.length,
                                   loop_paused_phase=lane.loop_paused_phase,
                                   raw_hits=list(lane.hits), beats_per_loop=lane.beats_per_loop,
                                   master_clock=self.master_index == i,
                                   position=positions[0] if positions else lane.paused_phase,
                                   duration=len(lane.sample.data) / lane.sample.rate if lane.sample else 0,
                                   source_rate=lane.sample.rate if lane.sample else RATE,
                                   path=lane.sample.path if lane.sample else ""))
            return self.frame, result
