import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import soundfile as sf

from audio import Engine, Sample, RATE, load_sample


def pulse(size=1, rate=RATE):
    return Sample(np.ones((size, 2), dtype=np.float32), rate, "test.wav")


class TimingTests(unittest.TestCase):
    def test_empty_start_and_melody_playback_at_any_slot(self):
        engine = Engine()
        self.assertEqual(engine.lanes, [])
        self.assertFalse(engine.render(256).any())
        for _ in range(4):
            index = engine.add_lane("pitch")
            engine.set_sample(index, pulse(RATE))
            engine.toggle(index)
            engine.pitch(index, 7)
            self.assertEqual(engine.lanes[index].current_pitch, 7)
            self.assertTrue(np.isfinite(engine.render(256)).all())
        for index in range(4):
            engine.remove_lane(index)
        self.assertFalse(engine.render(256).any())

    def test_adding_lanes_preserves_playback_samples_and_timing(self):
        engines = [Engine(), Engine()]
        signal = np.sin(np.arange(4096, dtype=np.float32) * .03)
        sample = Sample(np.column_stack((signal, signal)), RATE, "tone.wav")
        for engine in engines:
            melody = engine.add_lane("pitch")
            rhythm = engine.add_lane("rhythm")
            engine.set_sample(melody, sample)
            engine.set_sample(rhythm, sample)
            engine.toggle(melody)
            lane = engine.lanes[rhythm]
            lane.hits, lane.length, lane.state, lane.playing = [0, 512], 1024, "LOOP", True
            engine.render(333)
        subject, reference = engines
        for kind in ("rhythm", "pitch", "rhythm"):
            before = [(lane.origin, lane.playing, lane.pitch, lane.tempo) for lane in subject.lanes[:2]]
            subject.add_lane(kind)
            self.assertEqual(before, [(lane.origin, lane.playing, lane.pitch, lane.tempo) for lane in subject.lanes[:2]])
            np.testing.assert_array_equal(subject.render(777), reference.render(777))

    def test_hold_loop_uses_l_edges_and_repeats_exact_gaps(self):
        engine = Engine()
        index = engine.add_lane("rhythm")
        engine.set_sample(index, pulse())
        self.assertTrue(engine.begin_loop(index, 10.))
        engine.tap(index, 10.1)
        self.assertGreater(float(engine.render(1).max()), 0)
        engine.tap(index, 10.35)
        self.assertGreater(float(engine.render(1).max()), 0)
        self.assertFalse(engine.lanes[index].playing)
        engine.finish_loop(index, 10.75)
        lane = engine.lanes[index]
        self.assertEqual(lane.hits, [4800, 16800])
        self.assertEqual(lane.length, 36000)
        self.assertTrue(lane.playing)
        out = engine.render(72000)
        self.assertEqual(np.flatnonzero(out[:, 0]).tolist(), [4800, 16800, 40800, 52800])
        # L up schedules no additional closing hit.
        engine.finish_loop(index, 12.)
        self.assertEqual(lane.length, 36000)

    def test_hold_loop_no_hits_and_cancel_preserve_previous_pattern(self):
        engine = Engine()
        index = engine.add_lane("rhythm")
        self.assertFalse(engine.begin_loop(index, 1.))
        engine.set_sample(index, pulse())
        engine.begin_loop(index, 1.)
        engine.finish_loop(index, 2.)
        lane = engine.lanes[index]
        self.assertEqual(lane.state, "READY")
        self.assertFalse(lane.playing)
        engine.begin_loop(index, 3.)
        engine.tap(index, 3.1)
        engine.finish_loop(index, 4.)
        old = (list(lane.hits), lane.length, lane.origin)
        engine.render(200)
        engine.begin_loop(index, 5.)
        engine.tap(index, 5.2)
        engine.cancel_loop(index)
        self.assertEqual((lane.hits, lane.length, lane.origin), old)
        self.assertTrue(lane.playing)
        engine.begin_loop(index, 6.)
        engine.finish_loop(index, 7.)
        self.assertEqual((lane.hits, lane.length, lane.origin), old)

    def test_hold_loop_stop_discards_partial_and_tempo_scales_replay_only(self):
        engine = Engine()
        index = engine.add_lane("rhythm")
        engine.set_sample(index, pulse())
        engine.set_tempo(index, 1.5)
        engine.begin_loop(index, 1.)
        engine.tap(index, 1.25)
        engine.stop_all()
        engine.finish_loop(index, 2.)
        self.assertFalse(engine.lanes[index].playing)
        self.assertEqual(engine.lanes[index].state, "READY")
        engine.begin_loop(index, 3.)
        engine.tap(index, 3.25)
        engine.finish_loop(index, 4.)
        self.assertEqual(engine.lanes[index].hits, [12000])
        self.assertEqual(engine.lanes[index].length, 48000)
        self.assertEqual(engine.lanes[index].timing(), ([8000], 32000))

    def test_melody_loop_repeats_absolute_pitches_without_climbing(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, pulse(RATE))
        engine.begin_loop(index, 10.)
        for at, step in ((10.1, 1), (10.2, 1), (10.3, 1), (10.5, -1)):
            engine.pitch(index, step, now=at)
        engine.finish_loop(index, 10.8)
        lane = engine.lanes[index]
        self.assertEqual(lane.pitch_events, [(0, 0), (4800, 1), (9600, 2), (14400, 3), (24000, 2)])
        self.assertEqual(lane.length, 38400)
        self.assertTrue(lane.playing and lane.pitch_loop_enabled)
        for _ in range(3):
            for count, expected in ((4800, 1), (4800, 2), (4800, 3), (9600, 2), (14400, 0)):
                self.assertTrue(np.isfinite(engine.render(count)).all())
                self.assertAlmostEqual(lane.current_pitch, expected, places=6)

    def test_melody_loop_audio_is_independent_of_callback_size(self):
        signal = np.sin(np.arange(3000, dtype=np.float32) * .02)
        engines = [Engine(), Engine()]
        for engine in engines:
            index = engine.add_lane("pitch")
            engine.set_sample(index, Sample(np.column_stack((signal, signal)), RATE, "tone.wav"))
            engine.begin_loop(index, 1.)
            engine.pitch(index, 4, now=1.002)
            engine.pitch(index, -3, now=1.005)
            engine.finish_loop(index, 1.01)
        whole = engines[0].render(1600)
        parts = np.concatenate([engines[1].render(n) for n in (71, 499, 1, 600, 429)])
        np.testing.assert_allclose(whole, parts, atol=2e-7)

    def test_base_pitch_transposes_all_loop_notes_without_restart(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, pulse(RATE))
        engine.begin_loop(index, 1.)
        for at, step in ((1.1, 1), (1.2, 1), (1.3, 1), (1.5, -1)):
            engine.pitch(index, step, now=at)
        engine.finish_loop(index, 1.8)
        lane = engine.lanes[index]
        original = list(lane.pitch_events)
        engine.render(12000)
        voice = engine.voices[0]
        before = (lane.origin, lane.length, voice.position, lane.loop_paused_phase)
        engine.set_base_pitch(index, -12)
        self.assertTrue(lane.playing and lane.pitch_loop_enabled)
        self.assertEqual(lane.pitch_events, [(at, pitch - 12) for at, pitch in original])
        self.assertEqual((lane.origin, lane.length, voice.position, lane.loop_paused_phase), before)
        self.assertEqual(engine.snapshot()[1][index]["base_pitch"], -12)
        engine.render(2400)
        self.assertAlmostEqual(lane.current_pitch, -9, places=6)
        self.assertEqual(engine.snapshot()[1][index]["base_pitch"], -12)
        engine.set_base_pitch(index, 0)
        self.assertEqual(lane.pitch_events, original)
        engine.set_base_pitch(index, 0)
        self.assertEqual(lane.pitch_events, original)

    def test_base_pitch_limits_preserve_intervals_and_work_while_paused(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, pulse(RATE))
        engine.begin_loop(index, 1.)
        engine.pitch(index, 7, now=1.2)
        engine.pitch(index, -10, now=1.5)
        engine.finish_loop(index, 2.)
        lane = engine.lanes[index]
        engine.toggle(index)
        engine.set_base_pitch(index, 24)
        self.assertEqual([pitch for _, pitch in lane.pitch_events], [17, 24, 14])
        engine.set_base_pitch(index, -24)
        self.assertEqual([pitch for _, pitch in lane.pitch_events], [-21, -14, -24])
        self.assertTrue(lane.pitch_loop_enabled)
        self.assertFalse(lane.playing)

    def test_melody_loop_optional_pause_tempo_cancel_and_reload(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, pulse(RATE))
        engine.begin_loop(index, 1.)
        engine.pitch(index, 3, now=1.5)
        engine.finish_loop(index, 2.)
        lane = engine.lanes[index]
        original = list(lane.pitch_events)
        engine.render(12000)
        engine.toggle(index)
        self.assertEqual(lane.loop_paused_phase, 12000)
        engine.render(5000)
        engine.toggle(index)
        self.assertEqual(engine.frame - lane.origin, 12000)
        engine.set_tempo(index, 1.5)
        self.assertEqual(engine.frame - lane.origin, 8000)
        engine.begin_loop(index, 3.)
        engine.pitch(index, -7, now=3.2)
        engine.cancel_loop(index)
        self.assertEqual(lane.pitch_events, original)
        self.assertTrue(lane.pitch_loop_enabled)
        engine.set_pitch_loop(index, False)
        self.assertTrue(lane.playing)
        engine.pitch(index, 1)
        live = lane.current_pitch
        engine.render(RATE * 2)
        self.assertEqual(lane.current_pitch, live)
        self.assertEqual(lane.pitch_events, original)
        engine.set_pitch_loop(index, True)
        engine.pitch(index, -1)
        self.assertFalse(lane.pitch_loop_enabled)
        engine.set_sample(index, pulse(100))
        self.assertFalse(lane.pitch_events)

    def test_relative_tempo_changes_hit_spacing_not_sample_speed(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse(3))
        lane = engine.lanes[1]
        lane.hits, lane.length, lane.playing = [0, 1200], 2400, True
        engine.set_tempo(1, 1.5)
        audio = engine.render(3203)[:, 0]
        self.assertEqual(np.flatnonzero(audio).tolist(),
                         [hit + n for hit in (0, 800, 1600, 2400, 3200) for n in range(3)])
        self.assertEqual(lane.hits, [0, 1200])
        self.assertEqual(lane.length, 2400)
        self.assertEqual(engine.snapshot()[1][1]["length"], 1600)
        engine.set_tempo(1, 1)
        self.assertEqual(engine.snapshot()[1][1]["length"], 2400)

    def test_tempo_change_preserves_live_and_paused_phase(self):
        for pause in (False, True):
            engine = Engine(default_lanes=True)
            engine.set_sample(1, pulse())
            lane = engine.lanes[1]
            lane.hits, lane.length, lane.playing = [0, 1200], 2400, True
            engine.render(600)
            if pause:
                engine.toggle(1)
                engine.render(200)
            engine.set_tempo(1, .5)
            if pause:
                engine.toggle(1)
            self.assertEqual(np.flatnonzero(engine.render(1201)[:, 0]).tolist(), [1200])

    def test_sample_lane_speed_is_independent_and_survives_reload(self):
        engine = Engine(default_lanes=True)
        for i in (0, 3, 4):
            engine.set_sample(i, pulse(RATE))
            engine.toggle(i)
        engine.set_tempo(0, 1.5)
        engine.set_tempo(4, .5)
        engine.render(100)
        positions = {voice.lane: voice.position for voice in engine.voices}
        self.assertEqual(positions, {0: 150, 3: 100, 4: 100})
        engine.set_sample(0, pulse(RATE))
        self.assertEqual(engine.lanes[0].tempo, 1.5)

    def test_lane_volume_is_independent_and_survives_reload(self):
        engine = Engine(default_lanes=True)
        for index in (1, 2):
            engine.set_sample(index, pulse(10))
        engine.set_volume(1, .5)
        engine._voice(1, 0)
        engine._voice(2, 0)
        np.testing.assert_allclose(engine.render(10), .525, atol=1e-6)
        engine.set_sample(1, pulse(10))
        engine.rerecord(1)
        self.assertEqual(engine.lanes[1].volume, .5)
        self.assertEqual(engine.lanes[2].volume, 1)

    def test_live_volume_fades_without_stopping_playback(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(3, pulse(RATE))
        engine.toggle(3)
        engine.set_volume(3, 0)
        audio = engine.render(RATE // 5)[:, 0]
        self.assertGreater(audio[0], .3)
        self.assertLess(audio[-1], 1e-6)
        self.assertTrue((np.diff(audio) <= 0).all())
        self.assertTrue(engine.lanes[3].playing)
        self.assertEqual(engine.voices[0].position, RATE // 5)

    def test_previous_loop_continues_through_rerecord_and_rearming(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        lane = engine.lanes[1]
        lane.count = 2
        lane.hits, lane.length, lane.playing = [0, 1200], 2400, True
        lane.state = "LOOP"
        engine.render(600)
        engine.rerecord(1)
        self.assertEqual(np.flatnonzero(engine.render(1801)[:, 0]).tolist(), [600, 1800])
        origin = lane.origin
        engine.tap(1, 0)
        engine.tap(1, .5)
        self.assertEqual(lane.origin, origin)
        self.assertEqual(np.flatnonzero(engine.render(1200)[:, 0]).tolist(), [0, 1199])
        self.assertEqual(lane.timing(), ([0, 1200], 2400))
        engine.rerecord(1)
        self.assertEqual(lane.timing(), ([0, 1200], 2400))
        self.assertTrue(lane.playing)
        engine.tap(1, 1)
        engine.stop_all()
        self.assertEqual(lane.hits, [0, 1200])
        self.assertEqual(lane.state, "LOOP")
        self.assertFalse(lane.playing)

    def test_added_hit_repeats_without_changing_loop_or_doubling_tap(self):
        for index in (1, 2):
            engine = Engine(default_lanes=True)
            engine.set_sample(index, pulse())
            lane = engine.lanes[index]
            lane.hits, lane.length, lane.playing, lane.state, lane.count = [0], 2400, True, "LOOP", 1
            engine.render(600)
            engine.set_add_hits(index, True)
            engine.tap(index)
            self.assertEqual(lane.hits, [0, 600])
            self.assertEqual(lane.length, 2400)
            self.assertEqual(lane.origin, 0)
            self.assertEqual(lane.count, 2)
            audio = engine.render(2401)[:, 0]
            self.assertEqual(np.flatnonzero(audio).tolist(), [0, 1800, 2400])
            self.assertAlmostEqual(float(audio[0]), .35, places=5)
            origin = lane.origin
            engine.set_add_hits(index, False)
            engine.tap(index)
            self.assertEqual(lane.hits, [0])
            self.assertEqual(lane.count, 1)
            self.assertEqual(lane.origin, origin)
            self.assertTrue(lane.playing)
            self.assertEqual(np.flatnonzero(engine.render(2400)[:, 0]).tolist(), [1799])

    def test_add_hits_respects_tempo_pause_and_recording(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        lane = engine.lanes[1]
        lane.hits, lane.length, lane.playing, lane.state = [0], 2400, True, "LOOP"
        engine.set_tempo(1, 1.5)
        engine.render(400)
        engine.set_add_hits(1, True)
        engine.tap(1)
        self.assertEqual(lane.hits, [0, 600])
        engine.toggle(1)
        engine.render(200)
        engine.tap(1)
        self.assertEqual(lane.hits, [0, 600])
        engine.rerecord(1)
        self.assertFalse(lane.adding_hits)
        engine.set_add_hits(1, True)
        self.assertFalse(lane.adding_hits)

    def test_rerecord_keeps_sound_and_count(self):
        engine = Engine(default_lanes=True)
        source = pulse()
        engine.set_sample(1, source)
        for _ in range(2):
            engine.add_count(1)
        engine.confirm(1)
        for tap in [0, .5, 1, 1.5, 2, 2.5]:
            engine.tap(1, tap)
        engine.confirm(1)
        lane = engine.lanes[1]
        self.assertIs(lane.sample, source)
        self.assertEqual(lane.count, 2)
        self.assertEqual(lane.state, "ARMED")
        self.assertEqual(lane.hits, [])
        self.assertTrue(lane.playing)
        self.assertEqual(len(engine.voices), 6)
        for tap in [10, 10.2, 10.8, 11, 11.6, 11.8]:
            engine.tap(1, tap)
        self.assertEqual(lane.hits, [0, 9600])
        self.assertEqual(lane.length, 38400)
        self.assertIs(lane.sample, source)
        self.assertEqual(lane.count, 2)

    def test_rate_change_keeps_loop_seconds(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        lane = engine.lanes[1]
        lane.hits, lane.length = [0, 24000], 96000
        engine.frame = 144000
        engine._set_rate(44100)
        self.assertEqual(lane.hits, [0, 22050])
        self.assertEqual(lane.length, 88200)
        self.assertEqual(engine.frame, 132300)
        engine.rerecord(1)
        lane.count = 1
        engine.rerecord(1)
        engine.tap(1, 0)
        engine.tap(1, 1)
        engine.tap(1, 2)
        self.assertEqual(lane.length, 44100)

    def test_asio_pair_and_native_rate_are_used(self):
        engine = Engine(default_lanes=True)
        info = dict(name="ASIO test", hostapi=0, max_output_channels=4, default_samplerate=44100)
        with patch("audio.sd.query_devices", return_value=info), \
             patch("audio.sd.query_hostapis", return_value={"name": "ASIO"}), \
             patch("audio.sd.AsioSettings") as settings, \
             patch("audio.sd.check_output_settings"), \
             patch("audio.sd.OutputStream") as stream:
            engine.open(6, 2)
            settings.assert_called_once_with(channel_selectors=[2, 3])
            self.assertEqual(stream.call_args.kwargs["samplerate"], 44100)
            self.assertEqual(stream.call_args.kwargs["channels"], 2)
            stream.return_value.start.assert_called_once()
            engine.close()

    def test_invalid_channel_pair_never_opens_stream(self):
        engine = Engine(default_lanes=True)
        info = dict(name="ASIO test", hostapi=0, max_output_channels=4, default_samplerate=44100)
        with patch("audio.sd.query_devices", return_value=info), \
             patch("audio.sd.query_hostapis", return_value={"name": "ASIO"}), \
             patch("audio.sd.OutputStream") as stream:
            with self.assertRaises(ValueError):
                engine.open(6, 4)
            stream.assert_not_called()

    def test_rate_failure_retries_same_device(self):
        engine = Engine(default_lanes=True)
        info = dict(name="Output", hostapi=0, max_output_channels=2, default_samplerate=48000)
        with patch("audio.sd.query_devices", return_value=info), \
             patch("audio.sd.query_hostapis", return_value={"name": "MME"}), \
             patch("audio.sd.check_output_settings", side_effect=[RuntimeError("Invalid sample rate"), None]), \
             patch("audio.sd.OutputStream") as stream:
            engine.open(3)
            self.assertEqual(stream.call_args.kwargs["device"], 3)
            self.assertEqual(stream.call_args.kwargs["samplerate"], 44100)
            engine.close()

    def test_uneven_hits_and_closing_gap(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        for _ in range(5):
            engine.add_count(1)
        engine.confirm(1)
        for now in [0, .5, 1, 1.5, 1.75, 2, 2.5, 3, 3.5, 3.75, 4, 4.5, 5, 5.5, 5.75]:
            engine.tap(1, now=now)
        lane = engine.lanes[1]
        self.assertEqual(lane.hits, [0, 24000, 48000, 72000, 84000])
        self.assertEqual(lane.length, 96000)
        self.assertEqual(len(lane.hits), 5)
        # Remove live monitoring voices; inspect two complete recorded cycles.
        engine.voices.clear()
        engine.frame = lane.origin
        result = engine.render(192000)[:, 0]
        self.assertEqual(np.flatnonzero(result).tolist(),
                         [0, 24000, 48000, 72000, 84000, 96000, 120000, 144000, 168000, 180000])

    def test_callback_boundary_does_not_duplicate_hit(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        lane = engine.lanes[1]
        lane.hits, lane.length, lane.playing = [0, 256], 512, True
        blocks = np.concatenate([engine.render(256) for _ in range(4)])
        self.assertEqual(np.flatnonzero(blocks[:, 0]).tolist(), [0, 256, 512, 768])

    def test_independent_lanes(self):
        engine = Engine(default_lanes=True)
        for index, length in [(1, 400), (2, 600)]:
            engine.set_sample(index, pulse())
            lane = engine.lanes[index]
            lane.hits, lane.length, lane.playing = [0], length, True
        result = engine.render(1201)[:, 0]
        self.assertEqual(np.flatnonzero(result).tolist(), [0, 400, 600, 800, 1200])
        self.assertAlmostEqual(float(result[1200]), .7, places=5)

    def test_pause_resume_and_reset(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        lane = engine.lanes[1]
        lane.hits, lane.length, lane.playing = [0, 100], 200, True
        engine.render(50)
        engine.toggle(1)
        self.assertFalse(engine.render(300).any())
        engine.toggle(1)
        self.assertEqual(np.flatnonzero(engine.render(100)[:, 0]).tolist(), [50])
        engine.reset_count(1)
        self.assertEqual(lane.hits, [])
        self.assertEqual(lane.count, 0)
        self.assertFalse(engine.render(100).any())

    def test_stop_during_recording_rearms(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        engine.add_count(1)
        engine.confirm(1)
        engine.tap(1, 10)
        engine.stop_all()
        self.assertEqual(engine.lanes[1].state, "ARMED")
        engine.tap(1, 20)
        engine.tap(1, 21)
        engine.tap(1, 22)
        self.assertEqual(engine.lanes[1].length, RATE)

    def test_three_attempts_use_second_start_and_second_to_third_length(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        engine.add_count(1)
        engine.add_count(1)
        engine.confirm(1)
        # Starts: 0, 1.0, 2.4. First interval must not influence length.
        for now in (0, .4, 1.0, 1.5, 2.4, 3.0):
            engine.frame = round(now * RATE)
            engine.tap(1, now)
        lane = engine.lanes[1]
        self.assertEqual(lane.take_count, 3)
        self.assertEqual(lane.length, round(1.4 * RATE))
        # Next start is 3.8: second start + two complete 1.4-second cycles.
        self.assertEqual(lane.origin, round(3.8 * RATE))
        engine.voices.clear()
        self.assertEqual(np.flatnonzero(engine.render(RATE)[:, 0]).tolist(), [38400])

    def test_short_second_attempt_keeps_every_hit_inside_loop(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        engine.add_count(1)
        engine.add_count(1)
        engine.confirm(1)
        for now in (0, 2, 3, 3.1, 3.2, 3.3):
            engine.tap(1, now)
        lane = engine.lanes[1]
        self.assertEqual(lane.length, round(.2 * RATE))
        self.assertTrue(all(0 <= hit < lane.length for hit in lane.hits))

    def test_matching_hits_and_closing_gap_are_averaged(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(1, pulse())
        for _ in range(5):
            engine.add_count(1)
        engine.confirm(1)
        for now in [0, .48, 1.01, 1.48, 1.74, 2, 2.52, 2.99, 3.52, 3.76,
                    4.1, 4.6, 5.1, 5.6, 6.7]:
            engine.tap(1, now)
        lane = engine.lanes[1]
        self.assertEqual(lane.hits, [0, 24000, 48000, 72000, 84000])
        self.assertEqual(lane.length, 100800)
        self.assertEqual(lane.length - lane.hits[-1], 16800)
        self.assertTrue(lane.playing)

    def test_single_impulse_four_takes_shared_boundaries(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(2, pulse())
        engine.set_takes(2, 4)
        engine.add_count(2)
        engine.confirm(2)
        self.assertEqual(engine.lanes[2].take_count, 4)
        for i, now in enumerate([0, 1, 2.2, 3.1]):
            engine.tap(2, now)
            self.assertEqual(engine.lanes[2].playing, i == 3)
            self.assertEqual(engine.snapshot()[1][2]["completed_takes"], i)
            # The shared boundary is exactly one monitored hit, not two.
            np.testing.assert_allclose(engine.render(1), .35, atol=1e-6)
        lane = engine.lanes[2]
        self.assertEqual(lane.hits, [0])
        self.assertEqual(lane.length, 49600)
        self.assertTrue(lane.playing)
        # The last monitored hit is not doubled; playback waits a complete interval.
        self.assertEqual(np.flatnonzero(engine.render(49600)[:, 0]).tolist(), [49599])

    def test_three_takes_and_restart_discard_partial_average(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(2, pulse())
        engine.set_takes(2, 3)
        engine.add_count(2)
        engine.confirm(2)
        engine.tap(2, 0)
        engine.tap(2, 2)
        with self.assertRaises(ValueError):
            engine.set_takes(2, 4)
        engine.rerecord(2)
        self.assertEqual(engine.lanes[2].takes, [])
        self.assertEqual(engine.lanes[2].count, 1)
        self.assertEqual(engine.lanes[2].take_count, 3)
        for now in [10, 11, 12.2]:
            engine.tap(2, now)
        self.assertEqual(engine.lanes[2].length, 57600)

    def test_aborted_takes_are_cleared_on_stop_load_or_recount(self):
        for action in (lambda e: e.stop_all(), lambda e: e.set_sample(1, pulse()),
                       lambda e: e.reset_count(1)):
            engine = Engine(default_lanes=True)
            engine.set_sample(1, pulse())
            engine.add_count(1)
            engine.confirm(1)
            engine.tap(1, 0)
            engine.tap(1, 1)
            self.assertEqual(len(engine.lanes[1].takes), 1)
            action(engine)
            self.assertEqual(engine.lanes[1].takes, [])
            self.assertEqual(engine.lanes[1].hits, [])

    def test_pitch_and_source_rate(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(3, pulse(48000, 24000))
        engine.pitch(3, 12)
        engine.toggle(3)
        engine.render(100)
        self.assertEqual(engine.voices[0].position, 100)
        engine.pitch(3, 1000)
        self.assertEqual(engine.lanes[3].pitch, 24)
        engine.pitch(3, -1000)
        self.assertEqual(engine.lanes[3].pitch, -24)

    def test_bass_pitch_eases_in_audio_and_snapshot(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(3, pulse(RATE))
        engine.toggle(3)
        engine.pitch(3, 12)
        engine.render(480)
        first = engine.snapshot()[1][3]["pitch"]
        self.assertGreater(first, 0)
        self.assertLess(first, 4)
        self.assertGreater(engine.voices[0].position, 480)
        self.assertLess(engine.voices[0].position, 600)
        engine.render(4560)
        self.assertAlmostEqual(engine.snapshot()[1][3]["pitch"], 11.4, delta=.05)
        self.assertEqual(engine.lanes[3].history[-1][1], engine.snapshot()[1][3]["pitch"])
        engine.pitch(3, -24)
        engine.render(480)
        self.assertLess(engine.snapshot()[1][3]["pitch"], first + 8)
        self.assertGreater(engine.snapshot()[1][3]["pitch"], -12)

    def test_bass_smoothing_is_independent_of_callback_size(self):
        for rate in (44100, 48000):
            engines = [Engine(default_lanes=True), Engine(default_lanes=True)]
            data = np.sin(np.arange(RATE) * .013).astype(np.float32)
            sample = Sample(np.column_stack((data, data)), RATE, "tone.wav")
            for engine in engines:
                engine._set_rate(rate)
                engine.set_sample(3, sample)
                engine.toggle(3)
                engine.pitch(3, 7)
            whole = engines[0].render(4096)
            split = np.concatenate([engines[1].render(256) for _ in range(16)])
            np.testing.assert_allclose(whole, split, atol=1e-6)
            self.assertAlmostEqual(engines[0].voices[0].position, engines[1].voices[0].position, places=7)
            self.assertAlmostEqual(engines[0].lanes[3].current_pitch, engines[1].lanes[3].current_pitch, places=10)

    def test_melody_jumps_immediately_while_bass_smooths(self):
        engine = Engine(default_lanes=True)
        for index in (3, 4):
            engine.set_sample(index, pulse(RATE))
            engine.toggle(index)
            engine.pitch(index, 7)
        self.assertEqual(engine.snapshot()[1][4]["pitch"], 19)
        self.assertEqual(engine.snapshot()[1][3]["pitch"], 0)
        engine.render(256)
        self.assertEqual(engine.snapshot()[1][4]["pitch"], 19)
        self.assertLess(engine.snapshot()[1][3]["pitch"], 7)

    def test_ambient_end_and_repeat(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(0, pulse(10))
        engine.toggle(0)
        out = engine.render(20)
        self.assertEqual(np.count_nonzero(out[:, 0]), 10)
        self.assertFalse(engine.lanes[0].playing)
        engine.lanes[0].repeat = True
        engine.toggle(0)
        self.assertTrue((engine.render(100) > 0).all())

    def test_one_frame_source_and_voice_bound(self):
        engine = Engine(default_lanes=True)
        engine.set_sample(3, pulse())
        engine.toggle(3)
        engine.pitch(3, 24)
        self.assertTrue(np.isfinite(engine.render(256)).all())
        for _ in range(100):
            engine._voice(3, engine.frame)
        self.assertLessEqual(len(engine.voices), 48)
        self.assertLessEqual(float(engine.render(256).max()), 1)

    def test_dynamic_lanes_preserve_other_voices_and_release_removed_sound(self):
        engine = Engine(default_lanes=True)
        added = engine.add_lane("pitch")
        engine.set_sample(added, pulse(RATE))
        engine.set_sample(3, pulse(RATE))
        engine.toggle(added)
        engine.toggle(3)
        engine.remove_lane(3)
        self.assertIsNone(engine.lanes[3].sample)
        self.assertFalse(engine.lanes[3].active)
        self.assertEqual([voice.lane for voice in engine.voices], [added])
        self.assertGreater(float(engine.render(256).max()), 0)
        for index in range(len(engine.lanes)):
            engine.remove_lane(index)
        self.assertFalse(engine.render(256).any())
        rhythm = engine.add_lane("rhythm")
        engine.set_sample(rhythm, pulse(RATE))
        engine.add_count(rhythm)
        engine.confirm(rhythm)
        for now in (0., .5, 1.):
            engine.tap(rhythm, now)
        self.assertTrue(engine.lanes[rhythm].playing)
        self.assertTrue(engine.render(256).any())

    def test_load_mono_and_reject_bad_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mono.wav"
            sf.write(path, np.zeros(200), 22050)
            sample = load_sample(str(path))
            self.assertEqual(sample.data.shape, (200, 2))
            self.assertEqual(sample.rate, 22050)
            self.assertFalse(sample.data.flags.writeable)
            with self.assertRaises(Exception):
                load_sample(str(Path(folder) / "missing.wav"))


if __name__ == "__main__":
    unittest.main()
