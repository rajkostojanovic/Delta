"""Offscreen UI and keyboard checks; does not send sound to a device."""
import os
import tempfile
import time
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from app import Window, LaneRow
from audio import Engine, Sample, RATE
from board import lane_color, loop_frames


def check_independent_rhythm_loading(app):
    window = Window(audio_enabled=False)
    window.show()
    window.activateWindow()
    app.processEvents()
    window.add_lane("rhythm")
    engine = window.engine
    reference = Engine()
    reference.add_lane("rhythm")
    kick = Sample(np.ones((120, 2), dtype=np.float32), RATE, "kick.wav")
    for mixer in (engine, reference):
        mixer.set_sample(0, kick)
        mixer.begin_loop(0, 10.)
        mixer.tap(0, 10.)
        mixer.tap(0, 10.25)
        mixer.finish_loop(0, 10.5)
    engine.stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    original = engine.lanes[0]
    before = (list(original.hits), original.length, original.origin)
    window.refresh()
    def compare_audio():
        np.testing.assert_array_equal(engine.render(512), reference.render(512))
        assert original.playing and original.sample is kick
        assert (original.hits, original.length, original.origin) == before
    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "snare.wav")
        sf.write(path, np.ones((300, 2), dtype=np.float32) * .1, RATE)
        def choose_file(*args, **kwargs):
            compare_audio()  # Playback continues while the import dialog is open.
            return path, "Audio"
        with patch.object(engine, "close", wraps=engine.close) as close, \
                patch.object(engine, "stop_all", wraps=engine.stop_all) as stop:
            with patch("app.QFileDialog.getOpenFileName", side_effect=choose_file):
                window.load_selected.click()  # Kick is selected and already occupied.
            assert len(engine.lanes) == 2 and window.selected == 1
            deadline = time.monotonic() + 5
            while window.loading and time.monotonic() < deadline:
                compare_audio()
                app.processEvents()
                QTest.qWait(5)
            assert not window.loading
            assert engine.lanes[1].sample is not None
            assert engine.lanes[1].sample.path == path
            compare_audio()
            # An explicitly added empty Rhythm lane is filled rather than duplicated.
            window.add_lane("rhythm")
            with patch("app.QFileDialog.getOpenFileName", side_effect=choose_file):
                window.load(2)
            deadline = time.monotonic() + 5
            while window.loading and time.monotonic() < deadline:
                compare_audio()
                app.processEvents()
                QTest.qWait(5)
            assert not window.loading and len(engine.lanes) == 3
            assert engine.lanes[2].sample is not None
            close.assert_not_called()
            stop.assert_not_called()
            compare_audio()
    window.close()
    app.processEvents()


def check_kick_clock_and_hit_edits(app):
    window = Window(audio_enabled=False)
    window.show()
    window.activateWindow()
    app.processEvents()
    window.engine.stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    window.add_lane("rhythm")
    sample = Sample(np.ones((200, 2), dtype=np.float32), RATE, "kick.wav")
    window.loaded(0, sample, [.4] * 100)
    window.refresh()
    app.processEvents()
    engine = window.engine
    lane = engine.lanes[0]
    assert engine.master_index == 0 and lane.beats_per_loop == 4
    assert lane.hits == [] and lane.count == 0
    assert window.rows[0].master.isChecked()
    assert window.rows[0].beats.value() == 4
    window.play(0)
    assert not engine.render(128).any()
    for hit in (0, 24000, 48000, 72000):
        engine.add_loop_hit(0, hit)
    window.refresh()
    timeline = window.rows[0].timeline
    original = (list(lane.hits), lane.length, lane.origin)
    # Arbitrary time, with no beat snapping; the hit becomes selected for deletion.
    position = QPoint(round(timeline.x_at(.37 * RATE)), timeline.height() // 2)
    QTest.mouseDClick(timeline, Qt.MouseButton.LeftButton, pos=position)
    assert len(lane.hits) == 5 and window.selected_hit is not None
    assert lane.length == original[1] and lane.origin == original[2]
    assert all(hit in lane.hits for hit in original[0])
    assert window.delete_hit_button.isEnabled()
    app.processEvents()
    assert window.grab().save(str(Path(__file__).parent / "verification" / "master-kick-clock.png"))
    window.delete_hit_button.click()
    assert lane.hits == original[0] and lane.playing
    # Select a specific existing kick and delete only that occurrence in the pattern.
    position = QPoint(round(timeline.x_at(.5 * RATE)), timeline.height() // 2)
    QTest.mouseClick(timeline, Qt.MouseButton.LeftButton, pos=position)
    assert window.selected_hit == (0, 24000)
    QTest.keyClick(window, Qt.Key.Key_Delete)
    assert lane.hits == [0, 48000, 72000] and lane.length == original[1]
    window.add_lane("rhythm")
    window.loaded(1, sample, [.2] * 100)
    window.rows[1].beats.setValue(3)
    assert engine.lanes[1].length == 72000 and lane.length == 96000
    window.clock_tempo.setValue(60)
    assert engine.lanes[1].length == 144000 and lane.length == 192000
    assert engine.master_index == 0 and lane.playing
    window.rows[1].options_menu.popup(window.rows[1].options.mapToGlobal(QPoint(0, 30)))
    app.processEvents()
    assert window.rows[1].options_menu.grab().save(str(Path(__file__).parent / "verification" / "clock-lane-options.png"))
    window.rows[1].options_menu.hide()
    window.close()
    app.processEvents()


def check_loop_views(app):
    window = Window(audio_enabled=False)
    window.resize(1120, 850)
    window.show()
    app.processEvents()
    sample = Sample(np.ones((RATE, 2), dtype=np.float32) * .1, RATE, "sound.wav")
    for kind, name in (("rhythm", "Kick"), ("rhythm", "Percussion"), ("pitch", "Melody")):
        window.add_lane(kind)
        window.apply_lane_name(window.selected, name)
        window.loaded(window.selected, sample, (np.abs(np.sin(np.arange(128) * .2)) * .5).tolist())
    engine = window.engine
    engine.set_loop_beats(1, 3)
    for at in (0, 24000, 48000, 72000):
        engine.add_loop_hit(0, at)
    for at in (0, 17000, 57000):
        engine.add_loop_hit(1, at)
    engine.begin_loop(2, 10.)
    for at, step in ((10.25, 1), (10.5, 1), (10.75, 1), (11.25, -1)):
        engine.pitch(2, step, now=at)
    engine.finish_loop(2, 12.)
    engine.set_loop_beats(2, 4)
    engine.toggle(0)
    engine.toggle(1)
    engine.render(24000)
    window.select(0)
    window.lane_scroll.verticalScrollBar().setValue(0)
    window.refresh()
    app.processEvents()
    assert [loop_frames(row.timeline.info, RATE, engine.frame) for row in window.rows] == [96000, 72000, 96000]
    marker = window.rows[0].timeline.x_at(24000)
    engine.render(777)
    window.refresh()
    assert window.rows[0].timeline.x_at(24000) == marker
    assert window.overview.zoom == 200 and window.overview.time_window()[1] == 144000
    initial_height = window.overview.height()
    initial_audio = engine.snapshot()
    window.overview_zoom.setValue(100)
    assert window.overview.time_window()[1] == 288000  # Full 12-beat alignment cycle.
    assert window.overview.height() == initial_height and engine.snapshot() == initial_audio
    window.overview_zoom.setValue(200)
    melody = engine.lanes[2]
    editor = window.rows[2].timeline
    original = (list(melody.pitch_events), melody.origin, melody.length)
    curve_position = QPoint(round(editor.x_at(6000)), editor.height() // 2)
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=curve_position)
    assert melody.pitch_modes == {0: "linear"}
    assert melody.playing and melody.pitch_loop_enabled
    assert (melody.pitch_events, melody.origin, melody.length) == original
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=curve_position)
    assert melody.pitch_modes == {0: "smooth"}
    progression = window.rows[2].curve
    progression.setCurrentIndex(progression.findData("step"))
    progression.activated.emit(progression.currentIndex())
    assert melody.pitch_curve == "step" and not melody.pitch_modes
    progression.setCurrentIndex(progression.findData("smooth"))
    progression.activated.emit(progression.currentIndex())
    QTest.mouseDClick(editor, Qt.MouseButton.LeftButton, pos=curve_position)
    assert melody.pitch_modes == {0: "linear"}
    window.refresh()
    # Drag one existing point vertically; time and neighboring pitches stay fixed.
    points, _, _ = editor.pitch_geometry()
    note, x, y, pitch = next(point for point in points if point[0] == 2)
    before_drag = (list(melody.pitch_events), melody.origin, melody.length, dict(melody.pitch_modes))
    start_point = QPoint(round(x), round(y))
    end_point = start_point - QPoint(0, 10)
    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=start_point)
    assert editor.drag_note == note
    QTest.mouseMove(editor, end_point)
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=end_point)
    assert melody.pitch_events[note][1] == pitch + 1
    assert [at for at, _ in melody.pitch_events] == [at for at, _ in before_drag[0]]
    assert all(value == before_drag[0][i] for i, value in enumerate(melody.pitch_events) if i != note)
    assert (melody.origin, melody.length, melody.pitch_modes) == before_drag[1:]
    assert melody.playing and melody.pitch_loop_enabled and editor.drag_note is None
    engine.render(1)
    assert melody.current_pitch == pitch + 1
    window.refresh()
    for i, row in enumerate(window.rows):
        assert lane_color(i).name() in row.select.styleSheet()
    app.processEvents()
    destination = Path(__file__).parent / "verification"
    assert window.grab().save(str(destination / "overview-combined.png"))
    before = engine.snapshot()
    original_height = window.overview.height()
    assert 140 <= original_height <= 230
    window.overview_zoom.setValue(400)
    assert window.overview.time_window()[1] == 72000
    assert window.overview.height() == original_height and engine.snapshot() == before
    window.overview_zoom.setValue(200)
    upper_height = window.workspace_splitter.sizes()[0]
    window.workspace_splitter.moveSplitter(upper_height - 40, 1)
    app.processEvents()
    resized_height = window.overview.height()
    assert resized_height >= original_height + 30
    window.refresh()
    app.processEvents()
    assert window.overview.height() == resized_height
    window.overview_mode.setCurrentText("Separate rows")
    app.processEvents()
    assert engine.snapshot() == before
    assert window.overview.mode == "Separate rows"
    assert window.overview.height() == resized_height
    assert window.overview.time_window()[1] == 144000
    assert window.grab().save(str(destination / "overview-rows.png"))
    # Legend selection uses the same lane IDs and colors as the loop editors.
    rect, index = window.overview.legend[-1]
    QTest.mouseClick(window.overview, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert window.selected == index == 2
    window.remove_lane(1)
    window.refresh()
    assert [i for i, _ in window.overview.lanes] == [0, 2]
    assert lane_color(2).name() in window.rows[2].select.styleSheet()
    assert engine.lanes[0].playing and engine.lanes[2].playing
    window.close()
    app.processEvents()


def check_lane_editor_windows(app):
    window = Window(audio_enabled=False)
    window.show()
    app.processEvents()
    window.engine.stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    source = Sample(np.ones((RATE, 2), dtype=np.float32) * .05, RATE, "sound.wav")
    for kind in ("rhythm", "pitch"):
        window.add_lane(kind)
        window.loaded(window.selected, source, [.2] * 128)
    engine = window.engine
    engine.add_loop_hit(0, 0)
    engine.toggle(0)
    engine.begin_loop(1, 1.)
    engine.pitch(1, 3, now=1.3)
    engine.pitch(1, -1, now=1.6)
    engine.finish_loop(1, 2.)
    engine.render(6000)
    window.open_lane_editor(1)
    app.processEvents()
    melody_editor = window.lane_editors[1]
    assert melody_editor.isVisible() and not melody_editor.isModal()
    assert QApplication.activeModalWidget() is None
    assert melody_editor.timeline.expanded and melody_editor.timeline.kind == "pitch"
    assert melody_editor.timeline.height() >= 280
    assert window.selected == 1 and window.owns_active_window()
    previous = list(engine.lanes[1].pitch_events)
    QTest.keyPress(melody_editor, Qt.Key.Key_L)
    assert engine.lanes[1].held_recording
    QTest.keyClick(melody_editor, Qt.Key.Key_Up)
    QTest.keyClick(melody_editor, Qt.Key.Key_Escape)
    QTest.keyRelease(melody_editor, Qt.Key.Key_L)
    assert engine.lanes[1].pitch_events == previous
    QTest.keyClick(melody_editor, Qt.Key.Key_Space)
    assert not engine.lanes[1].playing and engine.lanes[0].playing
    QTest.keyClick(melody_editor, Qt.Key.Key_Space)
    assert engine.lanes[1].playing and engine.lanes[0].playing
    window.refresh()
    points, _, span = melody_editor.timeline.pitch_geometry()
    note, x, y, pitch = next(point for point in points if point[0] == 1)
    expected = round(pitch + 17 * span / melody_editor.timeline.pitch_extent(), 1)
    start = QPoint(round(x), round(y))
    QTest.mousePress(melody_editor.timeline, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(melody_editor.timeline, start - QPoint(0, 17))
    QTest.mouseRelease(melody_editor.timeline, Qt.MouseButton.LeftButton, pos=start - QPoint(0, 17))
    assert engine.lanes[1].pitch_events[note] == (previous[note][0], expected)
    assert expected != round(expected)  # Guides do not snap the note to whole semitones.
    interval = QPoint(round(melody_editor.timeline.x_at(7200)), melody_editor.timeline.height() // 2)
    QTest.mouseDClick(melody_editor.timeline, Qt.MouseButton.LeftButton, pos=interval)
    assert engine.lanes[1].pitch_modes == {0: "linear"}
    window.refresh()
    app.processEvents()
    destination = Path(__file__).parent / "verification"
    assert melody_editor.grab().save(str(destination / "melody-editor.png"))
    window.open_lane_editor(0)
    app.processEvents()
    assert 0 not in window.lane_editors
    assert melody_editor.isVisible() and window.selected == 0
    assert window.isActiveWindow()
    rhythm_timeline = window.rows[0].timeline
    at = QPoint(round(rhythm_timeline.x_at(.37 * RATE)), rhythm_timeline.height() // 2)
    QTest.mouseDClick(rhythm_timeline, Qt.MouseButton.LeftButton, pos=at)
    assert len(engine.lanes[0].hits) == 2 and window.delete_hit_button.isEnabled()
    window.delete_hit_button.click()
    assert engine.lanes[0].hits == [0]
    shown = []
    window.rows[0].options_menu.aboutToShow.connect(lambda: shown.append(True))
    QTimer.singleShot(50, window.rows[0].options_menu.hide)
    QTest.mouseClick(window.rows[0].select, Qt.MouseButton.LeftButton)
    QTest.qWait(70)
    assert shown and 0 not in window.lane_editors
    window.activateWindow()
    app.processEvents()
    with patch("audio.time.perf_counter", return_value=10.):
        QTest.keyPress(window, Qt.Key.Key_L)
    with patch("audio.time.perf_counter", return_value=10.1):
        QTest.keyClick(window, Qt.Key.Key_Return)
    with patch("audio.time.perf_counter", return_value=11.):
        QTest.keyRelease(window, Qt.Key.Key_L)
    assert engine.lanes[0].playing and engine.lanes[0].hits == [4800]
    assert engine.lanes[1].playing
    window.refresh()
    app.processEvents()
    assert window.grab().save(str(destination / "rhythm-inline.png"))
    before_close = engine.snapshot()
    melody_editor.close()
    app.processEvents()
    assert engine.snapshot() == before_close
    window.open_lane_editor(1)
    assert window.lane_editors[1] is melody_editor
    window.apply_lane_name(1, "Bass melody")
    window.refresh()
    assert "Bass melody" in melody_editor.windowTitle()
    window.remove_lane(0)
    assert 0 not in window.lane_editors and engine.lanes[1].playing
    window.close()
    app.processEvents()
    assert not melody_editor.isVisible()


def main():
    app = QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    window = Window(audio_enabled=False)
    window.show()
    window.activateWindow()
    app.processEvents()
    assert not window.engine.lanes and not window.rows
    assert window.selected is None
    assert list(window.board_groups) == ["Rhythm", "Melody"]
    assert all(group[1].add_button.isVisible() for group in window.board_groups.values())
    assert not window.load_selected.isEnabled()
    assert not window.engine.render(256).any()
    destination = Path(__file__).parent / "verification"
    destination.mkdir(exist_ok=True)
    assert window.grab().save(str(destination / "delta-empty.png"))
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, Qt.Key.Key_M)
    for title in ("Rhythm", "Melody"):
        with patch("app.QInputDialog.getText", return_value=(f"My {title}", True)):
            window.board_groups[title][1].add_button.click()
    assert [lane.kind for lane in window.engine.lanes] == ["rhythm", "pitch"]
    assert [lane.name for lane in window.engine.lanes] == ["My Rhythm", "My Melody"]
    with patch("app.QInputDialog.getText", return_value=("Bass texture", True)):
        next(action for action in window.rows[1].options_menu.actions() if action.text().startswith("Rename")).trigger()
    assert window.engine.lanes[1].name == "Bass texture"
    with patch("app.QInputDialog.getText") as rename_prompt:
        QTest.mouseClick(window.rows[1].select, Qt.MouseButton.LeftButton)
        app.processEvents()
        rename_prompt.assert_not_called()
    assert window.lane_editors[1].isVisible() and window.selected == 1
    assert not window.lane_editors[1].isModal()
    window.lane_editors[1].close()
    with patch("app.QInputDialog.getText", return_value=("Cancelled", False)):
        window.board_groups["Rhythm"][1].add_button.click()
    assert len(window.engine.lanes) == 2
    window.activateWindow()
    app.processEvents()
    window.engine.stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    window.engine.set_sample(1, Sample(np.ones((RATE, 2), dtype=np.float32), RATE, "melody.wav"))
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert window.engine.lanes[1].playing
    assert window.engine.render(256).any()
    QTest.keyClick(window, Qt.Key.Key_K)
    assert window.selected == 0
    QTest.keyClick(window, Qt.Key.Key_M)
    assert window.selected == 1
    playing_row = window.rows[1]
    playing_group = playing_row.parentWidget()
    voice = next(voice for voice in window.engine.voices if voice.lane == 1)
    def name_during_playback(*args, **kwargs):
        before = voice.position
        assert window.engine.render(256).any()
        assert voice.position > before
        return "New layer", True
    with patch.object(window.engine, "close", wraps=window.engine.close) as close, \
            patch.object(window.engine, "stop_all", wraps=window.engine.stop_all) as stop, \
            patch("app.QInputDialog.getText", side_effect=name_during_playback):
        for title in ("Rhythm", "Melody"):
            window.board_groups[title][1].add_button.click()
        close.assert_not_called()
        stop.assert_not_called()
    assert window.rows[1] is playing_row
    assert playing_row.parentWidget() is playing_group
    assert window.engine.lanes[1].playing
    assert voice in window.engine.voices
    assert window.engine.render(256).any()
    for i in range(len(window.engine.lanes)):
        window.remove_lane(i)
    # Retain the inherited Gamma controls fixture for broad behavior regressions.
    window.engine = Engine(default_lanes=True)
    window.rows = [LaneRow(window, i, lane.name, lane.kind) for i, lane in enumerate(window.engine.lanes)]
    window.arrange_board()
    window.select(1)
    window.engine.stream = SimpleNamespace(active=True, stop=lambda: None, close=lambda: None)
    samples = np.zeros((RATE, 2), dtype=np.float32)
    for i in range(5):
        window.engine.set_sample(i, Sample(samples, RATE, f"sample_{i}.wav"))
        window.rows[i].volume.setValue(20 + i * 10)
        assert window.engine.lanes[i].volume == (20 + i * 10) / 100
        assert window.rows[i].volume_value.text() == f"{20 + i * 10}%"
        window.rows[i].volume.setValue(100)
        window.rows[i].tempo.setValue((75 + i * 10) * 100)
        assert window.engine.lanes[i].tempo == (75 + i * 10) / 100
        assert window.rows[i].tempo_value.text() == f"{75 + i * 10:.2f}%"
        window.rows[i].tempo.setValue(10000)
    window.select(4)
    window.rows[4].pitch_slider.setValue(-120)
    assert window.engine.lanes[4].pitch == -12
    assert window.engine.lanes[3].pitch == 0
    window.refresh()
    assert window.rows[4].pitch_value.text() == "-12.0 st"
    QTest.keyClick(window, Qt.Key.Key_Up)
    window.refresh()
    assert window.rows[4].pitch_slider.value() == -110
    window.reset_pitch(4)
    window.refresh()
    assert window.rows[4].pitch_slider.value() == 0
    window.rows[4].pitch_slider.setValue(240)
    assert window.engine.lanes[4].pitch == 24
    window.rows[4].pitch_slider.setValue(120)
    tempo = window.rows[1].tempo
    start = tempo.rect().center()
    QTest.mousePress(tempo, Qt.MouseButton.LeftButton, pos=start)
    assert tempo.value() == 10000
    QTest.mouseRelease(tempo, Qt.MouseButton.LeftButton, pos=start - QPoint(0, 10))
    assert tempo.value() == 10100
    tempo.setValue(10000)
    QTest.mousePress(tempo, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, start)
    QTest.mouseRelease(tempo, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, start - QPoint(0, 10))
    assert tempo.value() == 10010
    assert window.engine.lanes[1].tempo == 1.001
    tempo.setValue(10000)
    window.select(1)
    assert not hasattr(window.rows[1], "takes")
    with patch("audio.time.perf_counter", return_value=10.):
        QTest.keyPress(window, Qt.Key.Key_L)
    assert window.engine.lanes[1].held_recording
    for now in (10.1, 10.35):
        with patch("audio.time.perf_counter", return_value=now):
            QTest.keyClick(window, Qt.Key.Key_Return)
    assert not window.engine.lanes[1].playing
    with patch("audio.time.perf_counter", return_value=10.75):
        QTest.keyRelease(window, Qt.Key.Key_L)
    lane = window.engine.lanes[1]
    assert lane.playing and lane.state == "LOOP"
    assert lane.hits == [round(.1 * RATE), round(.35 * RATE)]
    assert lane.length == round(.75 * RATE)
    source = lane.sample
    previous_hits = list(lane.hits)
    QTest.keyPress(window, Qt.Key.Key_L)
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.keyRelease(window, Qt.Key.Key_L)
    assert lane.hits == previous_hits and lane.playing and lane.sample is source
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert not window.engine.lanes[1].playing
    window.select(1)
    window.engine.toggle(1)
    window.refresh()
    window.rows[1].add_hits.setChecked(True)
    assert window.engine.lanes[1].adding_hits
    before_count = len(window.engine.lanes[1].hits)
    window.engine.frame = window.engine.lanes[1].origin + 1234
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert len(window.engine.lanes[1].hits) == before_count + 1
    window.rows[1].add_hits.setChecked(False)
    assert not window.engine.lanes[1].adding_hits
    assert len(window.engine.lanes[1].hits) == before_count + 1
    previous_hits = list(window.engine.lanes[1].hits)
    for index in (3, 4):
        window.select(index)
        interval = window.rows[index].interval
        assert interval.currentData() == 1
        for choice, step in enumerate((1, 3, 4, 7)):
            interval.setCurrentIndex(choice)
            window.reset_pitch(index)
            QTest.keyClick(window, Qt.Key.Key_Up)
            assert window.engine.lanes[index].pitch == step
            QTest.keyClick(window, Qt.Key.Key_Down)
            assert window.engine.lanes[index].pitch == 0
        QTest.keyPress(window, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
        QTest.qWait(100)
        QTest.keyRelease(window, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
        assert 0 < window.engine.lanes[index].pitch < 1.5
        before = window.engine.lanes[index].pitch
        parent_menu = window.rows[index].options_menu
        parent_menu.popup(window.rows[index].select.mapToGlobal(QPoint(0, 30)))
        app.processEvents()
        interval.showPopup()
        app.processEvents()
        QTest.keyClick(interval.view(), Qt.Key.Key_Up)
        QTest.keyClick(interval.view(), Qt.Key.Key_Return)
        assert window.engine.lanes[index].pitch == before
        parent_menu.hide()
        window.activateWindow()
        app.processEvents()
    # Melody keeps live control and gains an optional hold-L pitch sequence.
    window.select(4)
    window.rows[4].interval.setCurrentIndex(0)
    window.reset_pitch(4)
    with patch("audio.time.perf_counter", return_value=10.):
        QTest.keyPress(window, Qt.Key.Key_L)
    assert window.engine.lanes[4].held_recording and window.engine.lanes[4].playing
    for at, key in ((10.1, Qt.Key.Key_Up), (10.2, Qt.Key.Key_Up), (10.3, Qt.Key.Key_Up), (10.5, Qt.Key.Key_Down)):
        with patch("audio.time.perf_counter", return_value=at):
            QTest.keyClick(window, key)
    with patch("audio.time.perf_counter", return_value=10.8):
        QTest.keyRelease(window, Qt.Key.Key_L)
    melody = window.engine.lanes[4]
    assert melody.pitch_loop_enabled
    assert [pitch for _, pitch in melody.pitch_events] == [0, 1, 2, 3, 2]
    window.refresh()
    assert window.rows[4].pitch_loop.isChecked()
    window.rows[4].pitch_loop.setChecked(False)
    assert melody.playing and not melody.pitch_loop_enabled
    recorded = list(melody.pitch_events)
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert not melody.pitch_loop_enabled and melody.pitch_events == recorded
    window.rows[4].pitch_loop.setChecked(True)
    QTest.keyPress(window, Qt.Key.Key_L)
    QTest.keyClick(window, Qt.Key.Key_Down)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    QTest.keyRelease(window, Qt.Key.Key_L)
    assert melody.pitch_events == recorded and melody.pitch_loop_enabled
    before_transpose = (melody.origin, melody.length, melody.loop_paused_phase)
    window.rows[4].pitch_slider.setValue(-120)
    assert melody.playing and melody.pitch_loop_enabled
    assert melody.pitch_events == [(at, pitch - 12) for at, pitch in recorded]
    assert (melody.origin, melody.length, melody.loop_paused_phase) == before_transpose
    window.refresh()
    assert window.rows[4].pitch_loop.isChecked()
    assert window.rows[4].pitch_slider.value() == -120
    window.reset_pitch(4)
    assert melody.pitch_events == recorded and melody.pitch_loop_enabled
    for key, index in [(Qt.Key.Key_A, 0), (Qt.Key.Key_K, 1), (Qt.Key.Key_I, 2),
                       (Qt.Key.Key_B, 3), (Qt.Key.Key_M, 4)]:
        QTest.keyClick(window, key)
        assert window.selected == index
        assert window.rows[index].select.isChecked()
    QTest.keyClick(window, Qt.Key.Key_C)
    assert window.rows[4].volume.value() == 99
    assert window.rows[3].volume.value() == 100
    QTest.keyClick(window, Qt.Key.Key_V)
    assert window.rows[4].volume.value() == 100
    QTest.keyClick(window, Qt.Key.Key_X)
    assert window.rows[4].tempo.value() == 10010
    QTest.keyClick(window, Qt.Key.Key_Z)
    assert window.rows[4].tempo.value() == 10000
    with patch.object(window, "load") as load:
        QTest.keyClick(window, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
        load.assert_called_once_with(4)
        QTest.keyClick(window, Qt.Key.Key_K)
        QTest.keyPress(window, Qt.Key.Key_L)
        assert window.engine.lanes[1].held_recording
        QTest.keyClick(window, Qt.Key.Key_M)
        assert not window.engine.lanes[1].held_recording
        QTest.keyRelease(window, Qt.Key.Key_L)
        assert window.engine.lanes[1].hits == previous_hits
        assert load.call_count == 1
    for key, index in [(Qt.Key.Key_B, 3), (Qt.Key.Key_M, 4)]:
        window.engine.pitch(index, 5)
        QTest.keyPress(window, Qt.Key.Key_R)
        QTest.keyClick(window, key)
        QTest.keyRelease(window, Qt.Key.Key_R)
        assert window.engine.lanes[index].pitch == 0
        assert window.selected == index
    QTest.keyPress(window, Qt.Key.Key_R)
    QTest.keyClick(window, Qt.Key.Key_A)
    QTest.keyRelease(window, Qt.Key.Key_R)
    assert window.selected == 0 and window.engine.lanes[0].playing
    QTest.keyClick(window, Qt.Key.Key_P)
    assert not window.engine.lanes[0].playing
    QTest.keyClick(window, Qt.Key.Key_P)
    assert window.engine.lanes[0].playing
    # Representative populated visual state; no audio device is used.
    window.engine.frame = RATE * 6
    for index, duration, hits in [(1, 2, [0, .5, 1, 1.5]), (2, 2.1, [0, .5, 1, 1.5, 1.75])]:
        lane = window.engine.lanes[index]
        lane.hits = [round(x * RATE) for x in hits]
        lane.count = len(hits)
        lane.length = round(duration * RATE)
        lane.origin = 0
        lane.playing = True
        lane.state = "LOOP"
    for index in (3, 4):
        window.engine.lanes[index].history = [(n * RATE // 4, np.sin(n / 3) * 8) for n in range(25)]
    window.rows[0].timeline.envelope = (np.abs(np.sin(np.arange(512) * .04)) * .7).tolist()
    destination = Path(__file__).parent / "verification"
    destination.mkdir(exist_ok=True)
    asio = next((i for i in range(window.device.count()) if "ASIO4ALL" in window.device.itemText(i)), -1)
    if asio >= 0:
        window.device.setCurrentIndex(asio)
        assert window.channels.count() >= 1
        assert window.panel.isEnabled()
    for width, height in [(1030, 790), (720, 560)]:
        window.resize(width, height)
        window.refresh()
        app.processEvents()
        assert window.width() == width
        for row in window.rows:
            assert row.timeline.width() >= 260
            assert row.timeline.height() >= 85
            assert row.options.x() < row.timeline.x()
            assert row.options_menu.actions()
            assert row.detail.fontMetrics().horizontalAdvance(row.detail.text()) <= row.detail.width()
        assert window.rows[0].timeline.mapTo(window, QPoint()).x() == window.rows[1].timeline.mapTo(window, QPoint()).x()
        assert window.rows[3].y() != window.rows[4].y()
        assert window.grab().save(str(destination / f"delta-{width}.png"))
    options = window.rows[1].options_menu
    options.popup(window.rows[1].options.mapToGlobal(QPoint(0, 30)))
    app.processEvents()
    assert options.isVisible()
    assert options.grab().save(str(destination / "lane-options.png"))
    options.hide()
    pitch_options = window.rows[4].options_menu
    pitch_options.popup(window.rows[4].select.mapToGlobal(QPoint(0, 30)))
    app.processEvents()
    assert pitch_options.grab().save(str(destination / "melody-options.png"))
    pitch_options.hide()
    window.engine.set_pitch_loop(4, True)
    window.refresh()
    app.processEvents()
    assert window.grab().save(str(destination / "melody-pitch-loop.png"))
    # Match the sketch's example count, without making it a fixed configuration.
    window.remove_lane(0)
    window.add_lane("rhythm")
    added = window.selected
    assert window.engine.lanes[added].kind == "rhythm"
    assert sum(lane.active and lane.kind == "pitch" for lane in window.engine.lanes) == 2
    assert sum(lane.active and lane.kind == "rhythm" for lane in window.engine.lanes) == 3
    window.resize(1030, 790)
    window.refresh()
    app.processEvents()
    assert window.grab().save(str(destination / "delta-sketch-layout.png"))
    # A completed load for a removed lane must never repopulate the slot.
    window.loading.add(added)
    window.remove_lane(added)
    window.loaded(added, Sample(samples, RATE, "late.wav"), [])
    assert window.engine.lanes[added].sample is None
    for i, lane in enumerate(window.engine.lanes):
        if lane.active:
            window.remove_lane(i)
    assert window.selected is None
    assert list(window.board_groups) == ["Rhythm", "Melody"]
    assert all(group[1].add_button.isVisible() for group in window.board_groups.values())
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.keyClick(window, Qt.Key.Key_Up)
    window.refresh()
    assert not window.load_selected.isEnabled()
    window.add_lane("ambient")
    assert window.engine.lanes[window.selected].active
    assert window.load_selected.isEnabled()
    window.close()
    app.processEvents()
    check_independent_rhythm_loading(app)
    check_kick_clock_and_hit_edits(app)
    check_loop_views(app)
    check_lane_editor_windows(app)
    print("UI passed: melody editor windows and inline rhythm, empty startup, Rhythm/Melody creation, horizontal board, loop controls, note dragging/interpolation/keyboard controls, dynamic lanes, late load removal, empty board, 1030px and 720px layouts.")


if __name__ == "__main__":
    main()
