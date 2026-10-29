import unittest
import numpy as np
from audio import Engine, Sample, RATE
from pitch_curve import interpolate_pitch


class InterpolationTests(unittest.TestCase):
    def test_smooth_points_and_loop_closure_without_overshoot(self):
        events = [(0, 0), (24000, 12)]
        positions = [0, 6000, 12000, 18000, 24000, 36000, 42000, 48000]
        actual = interpolate_pitch(events, 48000, positions)
        np.testing.assert_allclose(actual, [0, 1.875, 6, 10.125, 12, 6, 1.875, 0])
        all_pitches = interpolate_pitch(events, 48000, np.arange(96000))
        self.assertGreaterEqual(float(all_pitches.min()), 0)
        self.assertLessEqual(float(all_pitches.max()), 12)

    def test_linear_override_changes_only_the_chosen_interval(self):
        pitches = interpolate_pitch([(0, 0), (24000, 12)], 48000, [6000, 30000], modes={0: "linear"})
        np.testing.assert_allclose(pitches, [3, 10.125])
        steps = interpolate_pitch([(0, 0), (24000, 12)], 48000, [0, 23999, 24000, 47999, 48000], default="step")
        np.testing.assert_array_equal(steps, [0, 0, 12, 12, 0])

    def test_engine_changes_curve_without_restarting_or_moving_points(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, Sample(np.ones((RATE, 2), dtype=np.float32), RATE, "tone.wav"))
        engine.begin_loop(index, 1.)
        engine.pitch(index, 12, now=1.5)
        engine.finish_loop(index, 2.)
        lane = engine.lanes[index]
        engine.render(6000)
        voice = engine.voices[0]
        before = (list(lane.pitch_events), lane.origin, lane.length, voice.position)
        self.assertEqual(engine.toggle_pitch_interval(index, 0), "linear")
        self.assertEqual((lane.pitch_events, lane.origin, lane.length, voice.position), before)
        engine.render(1)
        self.assertEqual(lane.current_pitch, 3)
        self.assertTrue(lane.playing and lane.pitch_loop_enabled)
        self.assertEqual(engine.toggle_pitch_interval(index, 0), "smooth")
        engine.set_base_pitch(index, -12)
        self.assertEqual(lane.pitch_modes, {0: "smooth"})
        self.assertEqual(lane.pitch_events, [(0, -12), (24000, 0)])

    def test_modes_survive_rate_change_and_cancelled_retake(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, Sample(np.ones((RATE, 2), dtype=np.float32), RATE, "tone.wav"))
        engine.begin_loop(index, 1.)
        engine.pitch(index, 7, now=1.5)
        engine.finish_loop(index, 2.)
        engine.toggle_pitch_interval(index, 1)
        engine._set_rate(44100)
        self.assertEqual(engine.lanes[index].pitch_modes, {1: "linear"})
        self.assertEqual(engine.lanes[index].pitch_events, [(0, 0), (22050, 7)])
        engine.begin_loop(index, 3.)
        self.assertFalse(engine.lanes[index].pitch_modes)
        engine.cancel_loop(index)
        self.assertEqual(engine.lanes[index].pitch_modes, {1: "linear"})
        engine.set_pitch_curve(index, "step")
        self.assertFalse(engine.lanes[index].pitch_modes)
        self.assertEqual(engine.lanes[index].pitch_curve, "step")

    def test_zero_length_intervals_and_tempo_are_finite(self):
        result = interpolate_pitch([(0, 0), (0, 4), (24000, -2)], 48000, [0, 12000, 24000], tempo=2)
        np.testing.assert_allclose(result, [4, -2, 4])
        self.assertTrue(np.isfinite(result).all())

    def test_editing_one_note_keeps_other_notes_timing_modes_and_voice(self):
        engine = Engine()
        index = engine.add_lane("pitch")
        engine.set_sample(index, Sample(np.ones((RATE, 2), dtype=np.float32), RATE, "tone.wav"))
        engine.begin_loop(index, 1.)
        engine.pitch(index, 3, now=1.25)
        engine.pitch(index, -2, now=1.5)
        engine.finish_loop(index, 2.)
        lane = engine.lanes[index]
        engine.toggle_pitch_interval(index, 0)
        engine.render(12000)
        voice = engine.voices[0]
        before = (lane.origin, lane.length, voice.position, dict(lane.pitch_modes))
        self.assertTrue(engine.set_note_pitch(index, 1, 7.5))
        self.assertEqual(lane.pitch_events, [(0, 0), (12000, 7.5), (24000, 1)])
        self.assertEqual((lane.origin, lane.length, voice.position, lane.pitch_modes), before)
        self.assertTrue(lane.playing and lane.pitch_loop_enabled)
        engine.render(1)
        self.assertEqual(lane.current_pitch, 7.5)
        engine.toggle(index)
        self.assertTrue(engine.set_note_pitch(index, 1, -100))
        self.assertEqual(lane.pitch_events[1], (12000, -24))
        self.assertFalse(lane.playing)
        self.assertFalse(engine.set_note_pitch(index, 99, 3))


if __name__ == "__main__":
    unittest.main()
