"""Pitch in semitones, interpolated through loop points without overshoot."""
import numpy as np


def interpolate_pitch(events, raw_length, positions, tempo=1., default="smooth", modes=None):
    length = max(1, round(raw_length / tempo))
    active = [(i, at, pitch) for i, (at, pitch) in enumerate(events) if at < raw_length]
    phase = np.asarray(positions, dtype=np.float64) % length
    if not active:
        return np.zeros_like(phase)
    times = np.minimum(length - 1, np.array([round(at / tempo) for _, at, _ in active], dtype=np.float64))
    values = np.array([pitch for _, _, pitch in active], dtype=np.float64)
    indices = np.maximum(0, np.searchsorted(times, phase, side="right") - 1)
    next_times = np.concatenate((times[1:], [length]))
    next_values = np.concatenate((values[1:], values[:1]))
    u = np.clip((phase - times[indices]) / np.maximum(1, next_times[indices] - times[indices]), 0, 1)
    modes = modes or {}
    kinds = np.array([modes.get(i, default) for i, _, _ in active])[indices]
    progress = np.where(kinds == "step", 0., np.where(kinds == "linear", u, u * u * (3 - 2 * u)))
    return values[indices] + (next_values[indices] - values[indices]) * progress
