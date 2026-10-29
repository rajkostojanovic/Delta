import unittest
import numpy as np
from audio import Engine, Sample, RATE


class ClockTests(unittest.TestCase):
    def setup_clock(self, seed_hits=True):
        engine = Engine()
        master = engine.add_lane("rhythm")
        sample = Sample(np.ones((1, 2), dtype=np.float32), RATE, "kick.wav")
        engine.set_sample(master, sample)
        engine.use_master(master)
        if seed_hits:
            for hit in (0, 24000, 48000, 72000):
                engine.add_loop_hit(master, hit)
        return engine, master, sample

    def test_default_four_beats_fifth_unquantized_hit_and_delete(self):
        engine, master, _ = self.setup_clock(seed_hits=False)
        lane = engine.lanes[master]
        self.assertEqual(lane.hits, [])
        self.assertEqual(lane.count, 0)
        self.assertEqual(lane.length, 96000)
        engine.toggle(master)
        self.assertFalse(engine.render(96000).any())
        self.assertTrue(lane.playing)
        for hit in (0, 24000, 48000, 72000):
            engine.add_loop_hit(master, hit)
        self.assertEqual(lane.hits, [0, 24000, 48000, 72000])
        self.assertEqual(lane.length, 96000)
        engine.render(12345)
        original = (list(lane.hits), lane.length, lane.origin, engine.master_bpm)
        hit = engine.add_loop_hit(master)
        self.assertEqual(hit, 12345)
        self.assertEqual(lane.hits, [0, 12345, 24000, 48000, 72000])
        self.assertEqual((lane.length, lane.origin, engine.master_bpm), original[1:])
        engine.delete_loop_hit(master, hit)
        self.assertEqual(lane.hits, original[0])
        for hit in list(lane.hits):
            engine.delete_loop_hit(master, hit)
        self.assertTrue(lane.playing)
        self.assertEqual(engine.clock_info()["beats"], 4)
        self.assertEqual(lane.length, 96000)

    def test_three_beat_lane_against_four_beat_master(self):
        engine, master, sample = self.setup_clock()
        child = engine.add_lane("rhythm")
        engine.set_sample(child, sample)
        engine.set_loop_beats(child, 3)
        engine.add_loop_hit(child, 0)
        engine.toggle(master)
        engine.toggle(child)
        self.assertEqual(engine.lanes[child].length, 72000)
        self.assertEqual(engine.lanes[master].length, 96000)
        out = engine.render(288001)[:, 0]
        self.assertAlmostEqual(float(out[0]), .7)
        self.assertAlmostEqual(float(out[72000]), .7)
        self.assertAlmostEqual(float(out[288000]), .7)
        self.assertAlmostEqual(float(out[96000]), .35)

    def test_shorter_loop_preserves_hidden_hits_for_later_extension(self):
        engine, master, _ = self.setup_clock()
        lane = engine.lanes[master]
        original = list(lane.hits)
        engine.set_loop_beats(master, 3)
        self.assertEqual(lane.hits, original)
        self.assertEqual(lane.timing(), ([0, 24000, 48000], 72000))
        engine.toggle(master)
        out = engine.render(144000)[:, 0]
        self.assertEqual(np.flatnonzero(out).tolist(), [0, 24000, 48000, 72000, 96000, 120000])
        engine.set_loop_beats(master, 4)
        self.assertEqual(lane.hits, original)
        self.assertEqual(lane.timing()[1], 96000)

    def test_master_tempo_preserves_musical_positions_and_sample_rate(self):
        engine, master, sample = self.setup_clock()
        child = engine.add_lane("rhythm")
        engine.set_sample(child, sample)
        engine.set_loop_beats(child, 3)
        engine.add_loop_hit(child, 12345)
        engine.toggle(master)
        engine.toggle(child)
        engine.render(12000)
        engine.set_clock_bpm(60)
        self.assertEqual(engine.lanes[master].length, 192000)
        self.assertEqual(engine.lanes[child].length, 144000)
        self.assertEqual(engine.lanes[child].hits, [24690])
        self.assertEqual(engine.frame - engine.lanes[master].origin, 24000)
        engine._set_rate(44100)
        self.assertEqual(engine.master_bpm, 60)
        self.assertEqual(engine.lanes[master].length, 176400)

    def test_free_recording_and_master_retake_use_exact_duration(self):
        engine, master, sample = self.setup_clock()
        engine.begin_loop(master, 10.)
        engine.tap(master, 10.1)
        engine.finish_loop(master, 13.)
        self.assertEqual(engine.lanes[master].length, 144000)
        self.assertEqual(engine.master_bpm, 80)
        other = engine.add_lane("rhythm")
        engine.set_sample(other, sample)
        engine.begin_loop(other, 20.)
        engine.tap(other, 20.17)
        engine.finish_loop(other, 21.3)
        self.assertEqual(engine.lanes[other].length, 62400)
        self.assertEqual(engine.lanes[other].beats_per_loop, 0)

    def test_clock_size_can_control_melody_sequence(self):
        engine, _, sample = self.setup_clock()
        melody = engine.add_lane("pitch")
        engine.set_sample(melody, sample)
        engine.begin_loop(melody, 1.)
        engine.pitch(melody, 3, now=2.8)
        engine.finish_loop(melody, 3.)
        recorded = list(engine.lanes[melody].pitch_events)
        engine.set_loop_beats(melody, 3)
        engine.render(72000)
        self.assertEqual(engine.lanes[melody].current_pitch, 0)
        self.assertEqual(engine.lanes[melody].pitch_events, recorded)
        engine.set_loop_beats(melody, 4)
        engine.render(86401)
        self.assertEqual(engine.lanes[melody].current_pitch, 3)
        engine.render(9599)
        self.assertAlmostEqual(engine.lanes[melody].current_pitch, 0, places=6)


if __name__ == "__main__":
    unittest.main()
