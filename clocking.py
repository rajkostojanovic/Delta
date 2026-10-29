"""Kick-led clock and manual loop editing; no audio analysis or hit quantization."""
import math


class ClockControls:
    def init_clock(self):
        self.master_index = None
        self.master_bpm = 120.0
        self.master_origin = 0

    def beat_frames(self):
        return self.rate * 60 / self.master_bpm

    def use_master(self, index):
        with self.lock:
            lane = self.lanes[index]
            if not lane.active or lane.kind != "rhythm" or lane.sample is None:
                return
            self.master_index = index
            if not lane.length:
                lane.beats_per_loop = 4
                lane.length = round(4 * self.beat_frames() * lane.tempo)
                lane.hits = []
                lane.count = 0
                lane.state = "LOOP"
            else:
                lane.beats_per_loop = lane.beats_per_loop or 4
                self._retime_clock(60 * self.rate * lane.beats_per_loop * lane.tempo / lane.length, skip=index)
            self.master_origin = lane.origin if lane.playing else self.frame

    def recorded_clock(self, index):
        lane = self.lanes[index]
        if index == self.master_index:
            lane.beats_per_loop = lane.beats_per_loop or 4
            self._retime_clock(60 * self.rate * lane.beats_per_loop * lane.tempo / lane.length, skip=index)
            self.master_origin = lane.origin
        else:
            lane.beats_per_loop = 0  # L-release retains the exact performed length.

    def set_loop_beats(self, index, beats):
        if not 0 <= beats <= 64:
            raise ValueError("Choose Free or 1 to 64 beats per loop.")
        with self.lock:
            lane = self.lanes[index]
            if lane.held_recording or not lane.sample:
                return
            if index == self.master_index and not beats:
                return
            if not beats:
                lane.beats_per_loop = 0
                return
            previous_length = lane.timing()[1]
            phase = (self.frame - lane.origin) % previous_length if lane.playing and previous_length else 0
            lane.length = max(1, round(beats * self.beat_frames() * lane.tempo))
            lane.beats_per_loop = beats
            lane.origin = self.frame - phase % lane.timing()[1]
            if lane.kind == "rhythm":
                lane.state = "LOOP"
                lane.paused_phase %= lane.timing()[1]
            else:
                lane.loop_paused_phase %= lane.timing()[1]
            if index == self.master_index:
                self.master_origin = lane.origin

    def set_clock_bpm(self, bpm):
        if not math.isfinite(bpm) or not 10 <= bpm <= 1000:
            raise ValueError("Choose a master tempo from 10 to 1000 BPM.")
        with self.lock:
            self._retime_clock(bpm)

    def _retime_clock(self, bpm, skip=None):
        with self.lock:
            ratio = self.master_bpm / bpm
            self.master_bpm = bpm
            for index, lane in enumerate(self.lanes):
                if index == skip or not (lane.active and lane.beats_per_loop and lane.length) or lane.held_recording:
                    continue
                phase = (self.frame - lane.origin) % lane.timing()[1] if lane.playing else (
                    lane.paused_phase if lane.kind == "rhythm" else lane.loop_paused_phase)
                lane.hits = [round(hit * ratio) for hit in lane.hits]
                lane.pitch_events = [(round(at * ratio), pitch) for at, pitch in lane.pitch_events]
                lane.length = max(1, round(lane.beats_per_loop * self.beat_frames() * lane.tempo))
                if lane.playing:
                    lane.origin = self.frame - round(phase * ratio)
                elif lane.kind == "rhythm":
                    lane.paused_phase = round(phase * ratio)
                else:
                    lane.loop_paused_phase = round(phase * ratio)
                if index == self.master_index:
                    self.master_origin = lane.origin

    def start_on_clock(self, index):
        lane = self.lanes[index]
        if index == self.master_index:
            self.master_origin = lane.origin
        elif self.master_index is not None and lane.beats_per_loop:
            # Beat-sized loops share a start reference. Recorded Free loops keep their timing.
            beat = self.beat_frames()
            lane.origin = round(self.master_origin + math.ceil((self.frame - self.master_origin) / beat) * beat)

    def clock_info(self):
        with self.lock:
            if self.master_index is None:
                return None
            lane = self.lanes[self.master_index]
            beat = self.beat_frames()
            beats = lane.beats_per_loop or 4
            phase = (self.frame - self.master_origin) % (beat * beats)
            return dict(index=self.master_index, name=lane.name, bpm=self.master_bpm,
                        beats=beats, beat=int(phase // beat) + 1, phase=phase / (beat * beats),
                        origin=self.master_origin, beat_frames=beat, playing=lane.playing)

    def set_live_add_hits(self, index, enabled):
        with self.lock:
            lane = self.lanes[index]
            lane.original_hits = None
            lane.adding_hits = bool(enabled and lane.kind == "rhythm" and lane.length and lane.sample)

    def add_loop_hit(self, index, position=None):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm" or not lane.length or lane.held_recording or not lane.sample:
                return None
            live = position is None
            if live:
                if not lane.playing:
                    return None
                position = round(((self.frame - lane.origin) % lane.timing()[1]) * lane.tempo)
            position = min(lane.length - 1, max(0, int(position)))
            if position not in lane.hits and len(lane.hits) < 128:
                lane.hits.append(position)
                lane.hits.sort()
                lane.count = len(lane.hits)
                lane.original_hits = None
                if live:
                    length = lane.timing()[1]
                    phase = (self.frame - lane.origin) % length
                    if (round(position / lane.tempo) - phase) % length > 1:
                        self._voice(index, self.frame)
            return position

    def delete_loop_hit(self, index, position):
        with self.lock:
            lane = self.lanes[index]
            if lane.kind != "rhythm" or lane.held_recording or position not in lane.hits:
                return False
            lane.hits.remove(position)
            lane.count = len(lane.hits)
            lane.original_hits = None
            return True
