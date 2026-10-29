from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QVBoxLayout, QWidget, QSlider, QMenu, QWidgetAction, QInputDialog, QSpinBox, QDoubleSpinBox, QSplitter,
)

from audio import Engine, RATE, load_sample, sd, asio_channel_names, show_asio_panel
from board import Timeline, GroupHeader, Overview, lane_color
from lane_editor import LaneEditor

ROOT = Path(__file__).resolve().parent
COLORS = ["#a8b4bd", "#ec7769", "#e0c75a", "#68c6ac", "#b19ae0"]


def icon_button(owner, icon, tip, action):
    button = QPushButton()
    button.setIcon(owner.style().standardIcon(icon))
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setFixedSize(32, 30)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.clicked.connect(action)
    return button


class TempoSlider(QSlider):
    """Relative dragging gives the short tempo control room for fine adjustments."""
    def __init__(self):
        super().__init__(Qt.Orientation.Vertical)
        self.drag_y = None
        self.setRange(5000, 15000)
        self.setSingleStep(10)
        self.setPageStep(100)
        self.setValue(10000)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_y = event.position().y()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_y is None:
            return super().mouseMoveEvent(event)
        delta = self.drag_y - event.position().y()
        scale = 1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 10
        self.setValue(self.value() + round(delta * scale))
        self.drag_y = event.position().y()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_y is not None:
            self.mouseMoveEvent(event)
            self.drag_y = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        scale = 1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 10
        self.setValue(self.value() + round(event.angleDelta().y() / 120 * scale))
        event.accept()


class LoadSignals(QObject):
    done = Signal(int, object, object)
    failed = Signal(int, str)


class Loader(QRunnable):
    def __init__(self, index, path):
        super().__init__()
        self.index, self.path = index, path
        self.signals = LoadSignals()

    def run(self):
        try:
            sample = load_sample(self.path)
            bounds = np.linspace(0, len(sample.data), min(512, len(sample.data)) + 1, dtype=int)
            envelope = [float(np.max(np.abs(sample.data[a:b]))) for a, b in zip(bounds[:-1], bounds[1:])]
            self.signals.done.emit(self.index, sample, envelope)
        except Exception as exc:
            self.signals.failed.emit(self.index, str(exc))


class LaneRow(QFrame):
    def __init__(self, window, index, name, kind):
        super().__init__()
        self.setObjectName("lane")
        self.index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.setMinimumWidth(140)
        control_panel = QWidget()
        control_panel.setFixedWidth(238)
        controls = QVBoxLayout(control_panel)
        controls.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.select = QPushButton(name)
        self.select.setCheckable(True)
        self.select.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.select.setFixedWidth(126)
        self.select.setStyleSheet(f"font-weight: 600; color: {lane_color(index).name()}; border-color: {lane_color(index).name()};")
        self.select.setToolTip("Click to open this melody's editor window" if kind == "pitch" else "Click the lane name for its options")
        self.select.pressed.connect(lambda: window.select(index))
        if kind == "pitch":
            self.select.clicked.connect(lambda: window.open_lane_editor(index))
        top.addWidget(self.select)
        self.load = icon_button(self, QStyle.StandardPixmap.SP_DialogOpenButton,
                                "Load sound", lambda: window.load(index))
        top.addWidget(self.load)
        self.play = icon_button(self, QStyle.StandardPixmap.SP_MediaPlay,
                                "Play / pause lane", lambda: window.play(index))
        top.addWidget(self.play)
        self.restart = icon_button(self, QStyle.StandardPixmap.SP_MediaSkipBackward,
                                   "Restart lane", lambda: window.restart(index))
        top.addWidget(self.restart)
        controls.addLayout(top)
        self.file = QLabel("No sound loaded")
        self.file.setFixedWidth(222)
        controls.addWidget(self.file)
        self.state = QLabel("EMPTY")
        state_line = QHBoxLayout()
        state_line.addWidget(self.state)
        state_line.addStretch()
        controls.addLayout(state_line)
        bottom = QHBoxLayout()
        self.detail = QLabel("0 hits" if kind == "rhythm" else "0:00" if kind == "ambient" else "+0.0 st")
        bottom.addWidget(self.detail)
        bottom.addStretch()
        if kind == "rhythm":
            self.add_hits = QCheckBox("Add hits")
            self.add_hits.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.add_hits.setToolTip("While the loop plays, Enter adds a hit at the current position")
            self.add_hits.setAccessibleName(f"{name} add hits")
            def change_add_hits(checked):
                window.select(index)
                window.engine.set_live_add_hits(index, checked)
            self.add_hits.toggled.connect(change_add_hits)
            bottom.addWidget(self.add_hits)
            controls.addWidget(QLabel("Hold L, tap Enter, release L to loop"))
        elif kind == "ambient":
            repeat = QCheckBox("Repeat")
            repeat.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            repeat.toggled.connect(lambda checked: window.set_repeat(index, checked))
            bottom.addWidget(repeat)
        else:
            pitch_heading = QHBoxLayout()
            pitch_heading.addWidget(QLabel("Bass / pitch"))
            self.pitch_value = QLabel(f"{window.engine.lanes[index].pitch:+.1f} st")
            pitch_heading.addWidget(self.pitch_value, 0, Qt.AlignmentFlag.AlignRight)
            controls.addLayout(pitch_heading)
            self.pitch_slider = QSlider(Qt.Orientation.Horizontal)
            self.pitch_slider.setRange(-240, 240)
            self.pitch_slider.setSingleStep(10)
            self.pitch_slider.setPageStep(120)
            self.pitch_slider.setTickInterval(120)
            self.pitch_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
            self.pitch_slider.setValue(round(window.engine.lanes[index].pitch * 10))
            self.pitch_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.pitch_slider.setAccessibleName(f"{name} bass pitch")
            self.pitch_slider.setToolTip("Lower for deeper bass. A running pitch loop transposes every note together, keeping its timing and intervals.")
            def change_pitch(value):
                window.set_absolute_pitch(index, value / 10)
                self.pitch_value.setText(f"{value / 10:+.1f} st")
            self.pitch_slider.valueChanged.connect(change_pitch)
            controls.addWidget(self.pitch_slider)
            self.pitch_loop = QCheckBox("Pitch loop")
            self.pitch_loop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.pitch_loop.setToolTip("Repeat the recorded pitch changes; uncheck for live pitch control")
            self.pitch_loop.setAccessibleName(f"{name} pitch loop")
            self.pitch_loop.toggled.connect(lambda checked: window.engine.set_pitch_loop(index, checked))
            controls.addWidget(self.pitch_loop)
            controls.addWidget(QLabel("Hold L, play Up/Down, release L"))
            curve_line = QHBoxLayout()
            curve_line.addWidget(QLabel("Progression"))
            self.curve = QComboBox()
            self.curve.setAccessibleName(f"{name} pitch interpolation")
            for label, mode in (("Smooth", "smooth"), ("Linear", "linear"), ("Steps", "step")):
                self.curve.addItem(label, mode)
            self.curve.setCurrentIndex(self.curve.findData(window.engine.lanes[index].pitch_curve))
            self.curve.activated.connect(lambda _: window.engine.set_pitch_curve(index, self.curve.currentData()))
            self.curve.setToolTip("Apply to the whole loop. Double-click an interval in the loop to toggle smooth / linear.")
            curve_line.addWidget(self.curve)
            controls.addLayout(curve_line)
            self.interval = QComboBox()
            self.interval.setFixedWidth(126)
            self.interval.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.interval.setToolTip("Pitch jump for each Up / Down press; Shift keeps gliding")
            self.interval.setAccessibleName(f"{name} pitch jump")
            for interval_name, semitones in [("Semitone", 1), ("Minor third", 3), ("Major third", 4), ("Fifth", 7)]:
                self.interval.addItem(interval_name, semitones)
            self.interval.activated.connect(lambda _: window.select(index))
            bottom.addWidget(self.interval)
            bottom.addWidget(icon_button(self, QStyle.StandardPixmap.SP_BrowserReload,
                                         "Original pitch", lambda: window.reset_pitch(index)))
        controls.addLayout(bottom)
        if kind in ("rhythm", "pitch"):
            beat_row = QHBoxLayout()
            beat_row.addWidget(QLabel("Beats per loop"))
            self.beats = QSpinBox()
            self.beats.setRange(0, 64)
            self.beats.setSpecialValueText("Free (L)")
            self.beats.setAccessibleName(f"{name} beats per loop")
            self.beats.setToolTip("Change loop duration at the master tempo; existing hit positions stay where you placed them")
            self.beats.valueChanged.connect(lambda value: window.change_loop_beats(index, value))
            beat_row.addWidget(self.beats)
            controls.addLayout(beat_row)
        if kind == "rhythm":
            self.master = QCheckBox("Use as kick master clock")
            self.master.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.master.toggled.connect(lambda checked: window.change_master(index, checked))
            controls.addWidget(self.master)
        layout.addWidget(control_panel)
        self.timeline = Timeline(index)
        layout.addWidget(self.timeline, 1)
        volume_panel = QWidget()
        volume_panel.setFixedWidth(44)
        volume_layout = QVBoxLayout(volume_panel)
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(4)
        volume_title = QLabel("Vol")
        volume_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        volume_layout.addWidget(volume_title)
        self.volume = QSlider(Qt.Orientation.Vertical)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.setAccessibleName(f"{name} volume")
        self.volume.setToolTip(f"{name} volume: 100%")
        self.volume.setMinimumHeight(52)
        self.volume_value = QLabel("100%")
        self.volume_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        def change_volume(value):
            window.engine.set_volume(index, value / 100)
            self.volume_value.setText(f"{value}%")
            self.volume.setToolTip(f"{name} volume: {value}%")
        self.volume.valueChanged.connect(change_volume)
        volume_layout.addWidget(self.volume, 1, Qt.AlignmentFlag.AlignHCenter)
        volume_layout.addWidget(self.volume_value)
        layout.addWidget(volume_panel)
        tempo_panel = QWidget()
        tempo_panel.setFixedWidth(64)
        tempo_layout = QVBoxLayout(tempo_panel)
        tempo_layout.setContentsMargins(0, 0, 0, 0)
        tempo_layout.setSpacing(4)
        tempo_title = QLabel("Tempo")
        tempo_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tempo_layout.addWidget(tempo_title)
        self.tempo = TempoSlider()
        self.tempo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tempo.setAccessibleName(f"{name} relative tempo")
        self.tempo.setMinimumHeight(52)
        self.tempo.setStyleSheet("QSlider::add-page:vertical { background: #a59959; }")
        self.tempo_value = QLabel("100%")
        self.tempo_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        def change_tempo(value):
            window.engine.set_tempo(index, value / 10000)
            self.tempo_value.setText(f"{value / 100:.2f}%")
            self.tempo.setToolTip(f"{name} relative tempo: {value / 100:.2f}% (100% = original). Drag gently; hold Shift for finer control.")
        self.tempo.valueChanged.connect(change_tempo)
        change_tempo(10000)
        tempo_layout.addWidget(self.tempo, 1, Qt.AlignmentFlag.AlignHCenter)
        tempo_layout.addWidget(self.tempo_value)
        layout.addWidget(tempo_panel)

        # The lane name opens its controls; the score stays uncluttered.
        for widget in (control_panel, self.timeline, volume_panel, tempo_panel):
            layout.removeWidget(widget)
        self.options = self.select
        self.options_menu = QMenu(self)
        panel = QWidget()
        panel_layout = QHBoxLayout(panel)
        panel_layout.addWidget(control_panel)
        panel_layout.addWidget(volume_panel)
        panel_layout.addWidget(tempo_panel)
        action = QWidgetAction(self.options_menu)
        action.setDefaultWidget(panel)
        self.options_menu.addAction(action)
        self.options_menu.addSeparator()
        replace_action = self.options_menu.addAction("Replace this lane's sound…")
        replace_action.triggered.connect(lambda: window.load(index, replace=True))
        rename_action = self.options_menu.addAction("Rename lane…")
        rename_action.triggered.connect(lambda: window.rename_lane(index))
        remove_action = self.options_menu.addAction("Remove this lane")
        remove_action.triggered.connect(lambda: window.remove_lane(index))
        if kind != "pitch":
            self.select.setMenu(self.options_menu)
        labels = QWidget()
        labels.setFixedWidth(142)
        label_layout = QVBoxLayout(labels)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(2)
        label_layout.addStretch()
        label_layout.addWidget(self.select, 0, Qt.AlignmentFlag.AlignHCenter)
        label_layout.addStretch()
        layout.addWidget(labels)
        self.timeline.selected.connect(lambda: window.select(index))
        self.timeline.hit_selected.connect(lambda hit: window.select_hit(index, hit))
        self.timeline.hit_added.connect(lambda hit: window.add_timeline_hit(index, hit))
        self.timeline.pitch_interval.connect(lambda interval: window.toggle_pitch_interval(index, interval))
        self.timeline.note_dragged.connect(lambda note, pitch: window.drag_note_pitch(index, note, pitch))
        if kind == "pitch":
            self.timeline.setToolTip("Drag a pitch point up/down to edit one note. Shift: fine adjustment. Double-click an interval: smooth / linear.")
        layout.addWidget(self.timeline, 1)
        layout.addWidget(self.play, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumHeight(100)



class Window(QMainWindow):
    def __init__(self, audio_enabled=True):
        super().__init__()
        self.engine = Engine()
        self.selected = None
        self.loading = set()
        self.jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.arrows = set()
        self.r_held = False
        self.loop_key_lane = None
        self.selected_hit = None
        self.lane_editors = {}
        self.last_tick = time.perf_counter()
        self.setWindowTitle("Delta")
        self.resize(1030, 790)
        self.setMinimumSize(620, 510)
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(16, 12, 16, 10)
        main.setSpacing(8)
        toolbar = QHBoxLayout()
        title = QLabel("DELTA")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        toolbar.addWidget(title)
        toolbar.addStretch()
        self.device = QComboBox()
        self.device.setMinimumWidth(160)
        self.device.setMaximumWidth(320)
        self.device.setToolTip("Audio output")
        self.device.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        toolbar.addWidget(self.device)
        toolbar.addWidget(icon_button(self, QStyle.StandardPixmap.SP_BrowserReload,
                                     "Connect audio output", self.connect_audio))
        toolbar.addWidget(icon_button(self, QStyle.StandardPixmap.SP_MediaStop,
                                     "Stop all lanes", self.engine.stop_all))
        main.addLayout(toolbar)
        output_bar = QHBoxLayout()
        self.channels = QComboBox()
        self.channels.setToolTip("ASIO stereo output pair")
        self.channels.setMinimumWidth(180)
        self.channels.setMaximumWidth(360)
        output_bar.addWidget(self.channels)
        self.panel = QPushButton("ASIO panel")
        self.panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panel.clicked.connect(self.open_asio_panel)
        output_bar.addWidget(self.panel)
        output_bar.addWidget(icon_button(self, QStyle.StandardPixmap.SP_BrowserReload,
                                        "Refresh output devices", self.refresh_devices))
        output_bar.addStretch()
        self.output_status = QLabel("Disconnected")
        output_bar.addWidget(self.output_status)
        main.addLayout(output_bar)
        clock_bar = QHBoxLayout()
        self.clock_label = QLabel("Master: load a kick into a Rhythm lane")
        clock_bar.addWidget(self.clock_label, 1)
        clock_bar.addWidget(QLabel("BPM"))
        self.clock_tempo = QDoubleSpinBox()
        self.clock_tempo.setRange(10, 1000)
        self.clock_tempo.setDecimals(2)
        self.clock_tempo.setValue(120)
        self.clock_tempo.setFixedWidth(90)
        self.clock_tempo.setAccessibleName("Master clock BPM")
        self.clock_tempo.valueChanged.connect(self.change_clock_tempo)
        clock_bar.addWidget(self.clock_tempo)
        main.addLayout(clock_bar)
        scroll = QScrollArea()
        self.lane_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board_container = QWidget()
        self.board_layout = QVBoxLayout(self.board_container)
        self.board_layout.setContentsMargins(8, 0, 8, 0)
        self.board_layout.setSpacing(14)
        self.rows = []
        self.board_groups = {}
        self.empty_board = None
        for index, lane in enumerate(self.engine.lanes):
            self.rows.append(LaneRow(self, index, lane.name, lane.kind))
        self.arrange_board()
        scroll.setWidget(self.board_container)
        scroll.setMinimumHeight(90)
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(9)
        self.workspace_splitter.setToolTip("Drag the divider to give the shared timeline more or less space")
        self.workspace_splitter.addWidget(scroll)
        overview_panel = QWidget()
        overview_layout = QVBoxLayout(overview_panel)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(6)
        overview_bar = QHBoxLayout()
        overview_bar.addWidget(QLabel("All lanes · shared timeline"), 1)
        overview_bar.addWidget(QLabel("Zoom"))
        self.overview_zoom = QSpinBox()
        self.overview_zoom.setRange(50, 800)
        self.overview_zoom.setSingleStep(25)
        self.overview_zoom.setSuffix("%")
        self.overview_zoom.setValue(200)
        self.overview_zoom.setFixedWidth(85)
        self.overview_zoom.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.overview_zoom.setAccessibleName("Shared timeline sound zoom")
        self.overview_zoom.setToolTip("Magnify events, waveforms and pitch shapes without resizing the panel or changing playback")
        overview_bar.addWidget(self.overview_zoom)
        self.overview_mode = QComboBox()
        self.overview_mode.addItems(["Combined", "Separate rows"])
        self.overview_mode.setAccessibleName("Overview layout")
        self.overview_mode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        overview_bar.addWidget(self.overview_mode)
        overview_layout.addLayout(overview_bar)
        self.overview = Overview()
        self.overview.lane_selected.connect(self.select)
        self.overview_mode.currentTextChanged.connect(self.overview.set_mode)
        self.overview_zoom.valueChanged.connect(self.overview.set_zoom)
        overview_layout.addWidget(self.overview, 1)
        self.workspace_splitter.addWidget(overview_panel)
        self.workspace_splitter.setStretchFactor(0, 3)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([400, 170])
        main.addWidget(self.workspace_splitter, 1)
        actions = QHBoxLayout()
        self.load_selected = QPushButton("Load sound (Ctrl+O)")
        self.load_selected.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_selected.clicked.connect(lambda: self.load(self.selected))
        actions.addWidget(self.load_selected)
        self.enter_selected = QPushButton("Enter")
        self.enter_selected.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.enter_selected.clicked.connect(self.trigger_selected)
        actions.addWidget(self.enter_selected)
        self.delete_hit_button = QPushButton("Delete hit")
        self.delete_hit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_hit_button.setEnabled(False)
        self.delete_hit_button.clicked.connect(self.delete_selected_hit)
        actions.addWidget(self.delete_hit_button)
        actions.addStretch()
        main.addLayout(actions)
        self.lane_help = QLabel()
        self.lane_help.setWordWrap(True)
        main.addWidget(self.lane_help)
        self.status = QLabel("Audio disconnected")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        main.addWidget(self.status)
        self.device.currentIndexChanged.connect(self.output_changed)
        self.channels.currentIndexChanged.connect(self.route_changed)
        self.refresh_devices(reinitialize=False)
        self.setStyleSheet("""
            QWidget { background: #f6f4ea; color: #354239; font-family: 'Segoe UI'; font-size: 12px; }
            QFrame#lane { border: none; }
            QPushButton { background: transparent; border: 1px solid #bbc2b4; border-radius: 2px; padding: 4px; }
            QPushButton:hover { background: #e6ebdf; border-color: #566b5b; }
            QPushButton:checked { border: 1px solid #354e3e; background: #dfe8d8; }
            QPushButton:disabled { color: #a5ab9e; border-color: #dcded4; }
            QPushButton#laneOptions { background: #35483c; color: #f6f4ea; border: none; font-size: 18px; }
            QPushButton#laneOptions::menu-indicator { image: none; }
            QLabel { background: transparent; }
            QComboBox { padding: 5px; border: 1px solid #bbc2b4; background: #f6f4ea; }
            QMenu { border: 1px solid #7a897b; padding: 10px; }
            QScrollArea { border: none; }
            QSplitter::handle:vertical { background: #ced6c7; margin: 2px 80px; border-radius: 2px; }
            QSlider::groove:vertical { background: #dedfd3; width: 6px; border-radius: 3px; }
            QSlider::handle:vertical { background: #35483c; height: 12px; margin: 0 -6px; border-radius: 3px; }
            QSlider::sub-page:vertical { background: #dedfd3; border-radius: 3px; }
            QSlider::add-page:vertical { background: #9aae95; border-radius: 3px; }
        """)
        self.update_lane_actions()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(25)
        QApplication.instance().installEventFilter(self)
        if audio_enabled:
            QTimer.singleShot(0, self.connect_audio)

    def arrange_board(self):
        # Both basic sections stay visible, even when they contain no lanes.
        active = [i for i, lane in enumerate(self.engine.lanes) if lane.active]
        for title, kinds in (("Rhythm", ("rhythm",)), ("Melody", ("pitch", "ambient"))):
            indices = [i for i in active if self.engine.lanes[i].kind in kinds]
            existing = self.board_groups.get(title)
            if existing:
                group, header, columns = existing
            else:
                group = QWidget()
                group_layout = QHBoxLayout(group)
                group_layout.setContentsMargins(0, 0, 0, 0)
                group_layout.setSpacing(0)
                header = GroupHeader(title, len(indices))
                header.add_lane.connect(lambda k=kinds[0]: self.create_lane(k))
                group_layout.addWidget(header)
                columns = QVBoxLayout()
                columns.setContentsMargins(0, 0, 0, 0)
                columns.setSpacing(0)
                group.empty_hint = QLabel(f"No {title.lower()} lanes — click + to add one")
                group.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
                group.empty_hint.setStyleSheet("color: #899384;")
                columns.addWidget(group.empty_hint)
                group_layout.addLayout(columns, 1)
                self.board_groups[title] = (group, header, columns)
                self.board_layout.addWidget(group)
            group.empty_hint.setVisible(not indices)
            for index in indices:
                row = self.rows[index]
                if columns.indexOf(row) < 0:
                    columns.addWidget(row, 1)
                    row.show()
            header.count = len(indices)
            header.update()
            self.board_layout.setStretch(self.board_layout.indexOf(group), max(1, len(indices)))

    def add_lane(self, kind):
        index = self.engine.add_lane(kind)
        lane = self.engine.lanes[index]
        self.rows.append(LaneRow(self, index, lane.name, lane.kind))
        self.arrange_board()
        self.select(index)
        self.refresh()

    def create_lane(self, kind):
        self.cancel_held_loop()
        title = "Rhythm" if kind == "rhythm" else "Melody"
        name, accepted = QInputDialog.getText(self, f"Add {title.lower()} lane", "Lane name:", text=title)
        if accepted:
            self.add_lane(kind)
            self.apply_lane_name(self.selected, name.strip() or title)
            self.open_lane_editor(self.selected)

    def open_lane_editor(self, index):
        if not self.engine.lanes[index].active:
            return
        self.select(index)
        if self.engine.lanes[index].kind != "pitch":
            self.raise_()
            self.activateWindow()
            return
        editor = self.lane_editors.get(index)
        if editor is None:
            editor = LaneEditor(self, index)
            self.lane_editors[index] = editor
        self.refresh()
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def owns_active_window(self):
        return self.isActiveWindow() or any(editor.isActiveWindow() for editor in self.lane_editors.values())

    def rename_lane(self, index):
        self.cancel_held_loop()
        self.select(index)
        name, accepted = QInputDialog.getText(self, "Rename lane", "Lane name:", text=self.engine.lanes[index].name)
        if accepted and name.strip():
            self.apply_lane_name(index, name.strip())

    def apply_lane_name(self, index, name):
        with self.engine.lock:
            self.engine.lanes[index].name = name
        row = self.rows[index]
        row.select.setText(row.select.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 110))
        row.select.setToolTip(f"{name} — click to open its editor" if self.engine.lanes[index].kind == "pitch" else f"{name} — click for lane options")
        row.select.setAccessibleName(name)
        row.options.setAccessibleName(f"{name} lane editor" if self.engine.lanes[index].kind == "pitch" else f"{name} lane options")
        row.volume.setAccessibleName(f"{name} volume")
        row.tempo.setAccessibleName(f"{name} relative tempo")
        if hasattr(row, "pitch_slider"):
            row.pitch_slider.setAccessibleName(f"{name} bass pitch")
        self.update_lane_actions()

    def change_master(self, index, checked):
        if checked:
            self.engine.use_master(index)
        elif self.engine.master_index == index:
            self.engine.master_index = None
        self.refresh()

    def change_clock_tempo(self, value):
        self.engine.set_clock_bpm(value)

    def change_loop_beats(self, index, value):
        self.engine.set_loop_beats(index, value)
        self.refresh()

    def select_hit(self, index, hit):
        self.select(index)
        self.selected_hit = (index, hit) if hit >= 0 else None
        for i, row in enumerate(self.rows):
            row.timeline.selected_hit = hit if i == index and hit >= 0 else None
            row.timeline.update()
            if i in self.lane_editors:
                self.lane_editors[i].timeline.selected_hit = row.timeline.selected_hit
                self.lane_editors[i].timeline.update()
        self.delete_hit_button.setEnabled(self.selected_hit is not None)

    def add_timeline_hit(self, index, hit):
        added = self.engine.add_loop_hit(index, hit)
        if added is not None:
            self.select_hit(index, added)
        self.refresh()

    def delete_selected_hit(self):
        if self.selected_hit is not None:
            index, hit = self.selected_hit
            self.engine.delete_loop_hit(index, hit)
            self.select_hit(index, -1)
            self.refresh()

    def remove_lane(self, index):
        if self.loop_key_lane == index:
            self.cancel_held_loop()
        self.rows[index].options_menu.hide()
        editor = self.lane_editors.pop(index, None)
        if editor is not None:
            editor.close()
            editor.deleteLater()
        self.rows[index].hide()
        self.rows[index].setParent(self.board_container)
        self.engine.remove_lane(index)
        self.arrows.clear()
        self.r_held = False
        remaining = [i for i, lane in enumerate(self.engine.lanes) if lane.active]
        self.arrange_board()
        if remaining:
            self.select(remaining[0] if self.selected == index else self.selected)
        else:
            self.selected = None
            self.update_lane_actions()
        self.status.setText("Lane removed. Use + beside Rhythm or Melody to add a lane.")

    def connect_audio(self):
        try:
            self.engine.open(self.device.currentData(), self.channels.currentData() or 0)
            self.output_status.setText(f"Stream active / {self.engine.rate} Hz")
            self.status.setText(f"Output: {self.device.currentText()} / {self.channels.currentText()}")
        except Exception as exc:
            logging.exception("Audio connection failed")
            self.output_status.setText("Disconnected")
            self.status.setText(f"Audio unavailable: {exc}")

    def refresh_devices(self, checked=False, reinitialize=True):
        old = self.device.currentText()
        old_pair = self.channels.currentData()
        self.engine.close()
        self.output_status.setText("Disconnected")
        try:
            if reinitialize:
                sd._terminate()
                sd._initialize()
            self.device.blockSignals(True)
            self.device.clear()
            self.device.addItem("Default output", None)
            for index, device in enumerate(sd.query_devices()):
                if device["max_output_channels"] >= 2:
                    host = sd.query_hostapis(device["hostapi"])["name"]
                    self.device.addItem(f"{device['name']} ({host})", index)
            found = self.device.findText(old)
            if found >= 0:
                self.device.setCurrentIndex(found)
            self.status.setText("Outputs refreshed. Select an output and connect.")
        except Exception as exc:
            logging.exception("Device refresh failed")
            self.status.setText(f"Output discovery failed: {exc}")
        finally:
            self.device.blockSignals(False)
        self.output_changed()
        match = self.channels.findData(old_pair)
        if match >= 0:
            self.channels.setCurrentIndex(match)

    def route_changed(self):
        self.engine.close()
        self.output_status.setText("Disconnected")
        self.channels.setToolTip(self.channels.currentText())

    def output_changed(self):
        self.route_changed()
        self.channels.blockSignals(True)
        self.channels.clear()
        self.panel.setEnabled(False)
        try:
            device = self.device.currentData()
            info = sd.query_devices(device, "output")
            is_asio = sd.query_hostapis(info["hostapi"])["name"] == "ASIO"
            self.panel.setEnabled(is_asio)
            if is_asio:
                if device is None:
                    device = sd.default.device[1]
                names = asio_channel_names(device)
                for channel in range(0, len(names) - 1, 2):
                    self.channels.addItem(f"{channel + 1}/{channel + 2}: {names[channel]} + {names[channel + 1]}", channel)
            else:
                self.channels.addItem("Stereo 1/2", 0)
            self.channels.setEnabled(is_asio)
        except Exception as exc:
            self.status.setText(f"Output unavailable: {exc}")
        finally:
            self.channels.blockSignals(False)
            self.channels.setToolTip(self.channels.currentText())

    def open_asio_panel(self):
        self.route_changed()
        try:
            device = self.device.currentData()
            if device is None:
                device = sd.default.device[1]
            show_asio_panel(device, int(self.winId()))
            self.refresh_devices()
        except Exception as exc:
            self.status.setText(f"ASIO panel unavailable: {exc}")

    def select(self, index):
        if self.loop_key_lane is not None and self.loop_key_lane != index:
            self.cancel_held_loop()
        if not self.engine.lanes[index].active:
            return
        if self.selected != index:
            self.selected_hit = None
            self.delete_hit_button.setEnabled(False)
            for row in self.rows:
                row.timeline.selected_hit = None
        self.arrows.clear()
        self.selected = index
        for i, row in enumerate(self.rows):
            row.select.setChecked(i == index)
            row.timeline.active = i == index
            row.timeline.update()
        self.lane_scroll.ensureWidgetVisible(self.rows[index])
        self.update_lane_actions()

    def update_lane_actions(self):
        if self.selected is None:
            self.load_selected.setEnabled(False)
            self.enter_selected.setEnabled(False)
            self.lane_help.setText("Add a lane to begin. Load any sound file, then play or record its loop.")
            return
        lane = self.engine.lanes[self.selected]
        self.load_selected.setEnabled(self.selected not in self.loading)
        self.load_selected.setText("Add sound (Ctrl+O)" if lane.sample else "Load sound (Ctrl+O)")
        self.load_selected.setToolTip("An additional sound gets its own lane; existing sounds keep playing." if lane.sample else "Load a sound into this empty lane.")
        self.enter_selected.setEnabled(lane.sample is not None and self.selected not in self.loading)
        self.enter_selected.setText(("Add kick (Enter)" if self.selected == self.engine.master_index and lane.playing and not lane.held_recording else "Tap (Enter)") if lane.kind == "rhythm" else "Play / pause (Enter)")
        if lane.sample is None:
            help_text = "Load a sound file from your computer, including sounds made in Alpha."
        elif lane.kind == "pitch":
            if lane.held_recording:
                help_text = "Keep L held and play Up/Down. Release L to repeat the pitch sequence. Escape cancels."
            elif lane.pitch_loop_enabled:
                help_text = "Pitch loop active. Double-click an interval: smooth / linear. The bass slider shifts all notes."
            else:
                help_text = "Enter: play / pause. Up/Down: live pitch. Hold L to record an optional pitch loop."
        elif lane.kind == "ambient":
            help_text = "Enter: play / pause. Enable Repeat to loop the sound."
        elif lane.held_recording:
            help_text = "Keep L held and tap Enter for each hit. Release L to set the loop end. Escape cancels."
        elif lane.state == "LOOP":
            help_text = "Double-click to add a hit; click a hit then Delete hit to remove it. L records a replacement loop."
        else:
            help_text = "Hold L to start the loop, tap Enter for each hit, then release L to repeat it."
        self.lane_help.setText(f"{lane.name}: {help_text}")

    def cancel_held_loop(self):
        if self.loop_key_lane is not None:
            index, self.loop_key_lane = self.loop_key_lane, None
            self.engine.cancel_loop(index)

    def trigger_selected(self):
        if self.selected is None:
            return
        index = self.selected
        if self.engine.lanes[index].sample is None:
            self.status.setText("Load a sound into the selected lane first.")
            return
        if self.live():
            if self.engine.lanes[index].kind == "rhythm":
                self.engine.tap(index)
            else:
                self.engine.toggle(index)

    def live(self):
        ready = self.engine.stream is not None and self.engine.stream.active
        if not ready:
            self.status.setText("Audio disconnected. Select an output and reconnect.")
        return ready

    def play(self, index):
        self.select(index)
        if self.live():
            self.engine.toggle(index)

    def restart(self, index):
        self.select(index)
        if self.live():
            self.engine.restart(index)

    def set_repeat(self, index, checked):
        with self.engine.lock:
            self.engine.lanes[index].repeat = checked

    def reset_pitch(self, index):
        self.engine.set_base_pitch(index, 0)

    def set_absolute_pitch(self, index, value):
        self.engine.set_base_pitch(index, value)

    def toggle_pitch_interval(self, index, interval):
        mode = self.engine.toggle_pitch_interval(index, interval)
        if mode:
            self.status.setText(f"{self.engine.lanes[index].name}: interval {interval + 1} uses {mode} pitch progression.")
            self.refresh()

    def drag_note_pitch(self, index, note, pitch):
        if self.engine.set_note_pitch(index, note, pitch):
            self.refresh()

    def load(self, index, replace=False):
        self.cancel_held_loop()
        self.arrows.clear()
        self.r_held = False
        if index in self.loading:
            return
        occupied = self.engine.lanes[index].sample is not None
        title = "Replace this lane's sound" if replace else "Add sound in a new lane" if occupied else "Load sound"
        path, _ = QFileDialog.getOpenFileName(self, title, str(ROOT),
                                            "Audio (*.wav *.flac *.aiff *.aif *.ogg *.mp3);;All files (*)")
        if not path:
            return
        if occupied and not replace:
            self.add_lane(self.engine.lanes[index].kind)
            index = self.selected
            self.apply_lane_name(index, Path(path).stem)
            self.open_lane_editor(index)
        self.select(index)
        self.loading.add(index)
        self.rows[index].load.setEnabled(False)
        job = Loader(index, path)
        job.signals.done.connect(self.loaded)
        job.signals.failed.connect(self.load_failed)
        self.jobs[index] = job
        self.pool.start(job)

    def loaded(self, index, sample, envelope):
        try:
            if self.engine.lanes[index].active:
                self.engine.set_sample(index, sample)
                if self.engine.lanes[index].kind == "rhythm":
                    if self.engine.master_index is None:
                        first = next(i for i, lane in enumerate(self.engine.lanes) if lane.active and lane.kind == "rhythm" and lane.sample)
                        self.engine.use_master(first)
                    elif self.engine.master_index == index:
                        self.engine.use_master(index)
                self.rows[index].timeline.envelope = envelope
                self.status.setText(f"{self.engine.lanes[index].name}: {Path(sample.path).name}")
        except Exception as exc:
            self.status.setText(str(exc))
        self.loading.discard(index)
        self.rows[index].load.setEnabled(True)
        self.jobs.pop(index, None)

    def load_failed(self, index, message):
        self.loading.discard(index)
        self.rows[index].load.setEnabled(True)
        self.jobs.pop(index, None)
        if self.engine.lanes[index].active:
            self.status.setText(f"Could not load sound: {message}")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self.arrows.clear()
            self.r_held = False
            self.cancel_held_loop()
        if event.type() not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return super().eventFilter(watched, event)
        key = event.key()
        if event.type() == QEvent.Type.KeyRelease and not event.isAutoRepeat():
            self.arrows.discard(key)
            if key == Qt.Key.Key_R:
                self.r_held = False
        # Complete the original lane even if modifiers or focus changed before L up.
        if event.type() == QEvent.Type.KeyRelease and key == Qt.Key.Key_L and not event.isAutoRepeat():
            if self.loop_key_lane is not None:
                index, self.loop_key_lane = self.loop_key_lane, None
                self.engine.finish_loop(index)
                return True
        if QApplication.activeModalWidget() or not self.owns_active_window():
            return super().eventFilter(watched, event)
        if key == Qt.Key.Key_O and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat() and self.selected is not None:
                self.load(self.selected)
            return True
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                                | Qt.KeyboardModifier.MetaModifier):
            return super().eventFilter(watched, event)
        lane_keys = {}
        for shortcut_key, name, kind, ordinal in ((Qt.Key.Key_A, "Ambience", "ambient", 0),
                (Qt.Key.Key_K, "Kick", "rhythm", 0), (Qt.Key.Key_I, "Impulse", "rhythm", 1),
                (Qt.Key.Key_B, "Bass", "pitch", 1), (Qt.Key.Key_M, "Melody", "pitch", 0)):
            matches = [i for i, lane in enumerate(self.engine.lanes) if lane.active and lane.name == name]
            candidates = [i for i, lane in enumerate(self.engine.lanes) if lane.active and lane.kind == kind]
            if matches:
                lane_keys[shortcut_key] = matches[0]
            elif len(candidates) > ordinal:
                lane_keys[shortcut_key] = candidates[ordinal]
        adjust_keys = {Qt.Key.Key_V, Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_Z}
        handled = key in (Qt.Key.Key_Escape, Qt.Key.Key_Enter, Qt.Key.Key_Return,
                                      Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Space,
                                      Qt.Key.Key_R, Qt.Key.Key_L, Qt.Key.Key_P, Qt.Key.Key_Delete) or key in lane_keys or key in adjust_keys
        if not handled:
            return super().eventFilter(watched, event)
        # Open menus own their navigation keys, including the pitch interval pickers.
        if QApplication.activePopupWidget():
            return super().eventFilter(watched, event)
        if event.isAutoRepeat() and key not in adjust_keys:
            return True
        if event.type() == QEvent.Type.KeyRelease:
            self.arrows.discard(key)
            return True
        index = self.selected
        if index is None:
            return True
        if key == Qt.Key.Key_R:
            self.r_held = True
        elif self.r_held:
            if key == Qt.Key.Key_A and key in lane_keys:
                self.restart(lane_keys[key])
            elif key in (Qt.Key.Key_B, Qt.Key.Key_M) and key in lane_keys:
                target = lane_keys[key]
                self.select(target)
                self.reset_pitch(target)
        elif key in lane_keys:
            self.select(lane_keys[key])
            if any(editor.isActiveWindow() for editor in self.lane_editors.values()):
                self.open_lane_editor(lane_keys[key])
        elif key in adjust_keys:
            slider = self.rows[index].volume if key in (Qt.Key.Key_V, Qt.Key.Key_C) else self.rows[index].tempo
            direction = 1 if key in (Qt.Key.Key_V, Qt.Key.Key_X) else -1
            slider.setValue(slider.value() + direction * slider.singleStep())
        elif key == Qt.Key.Key_L:
            if self.engine.lanes[index].kind in ("rhythm", "pitch"):
                if self.live() and self.engine.begin_loop(index):
                    self.loop_key_lane = index
            else:
                self.load(index)
        elif key == Qt.Key.Key_P:
            self.play(index)
        elif key == Qt.Key.Key_Delete:
            self.delete_selected_hit()
        elif key == Qt.Key.Key_Escape:
            self.cancel_held_loop()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.trigger_selected()
        elif key == Qt.Key.Key_Space:
            self.play(index)
        elif self.engine.lanes[index].kind == "pitch":
            self.arrows.add(key)
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                step = self.rows[index].interval.currentData()
                self.engine.pitch(index, step if key == Qt.Key.Key_Up else -step)
        return True

    def refresh(self):
        now = time.perf_counter()
        dt = min(0.1, now - self.last_tick)
        self.last_tick = now
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier and self.arrows:
            direction = int(Qt.Key.Key_Up in self.arrows) - int(Qt.Key.Key_Down in self.arrows)
            glide_speed = 3 if self.engine.lanes[self.selected].smooth_pitch else 6
            self.engine.pitch(self.selected, direction * dt * glide_speed)
        frame, lanes = self.engine.snapshot()
        clock = self.engine.clock_info()
        self.clock_label.setText(f"Master: {clock['name'][:16]} · beat {clock['beat']}/{clock['beats']} · {clock['bpm']:.2f} BPM" if clock else "Master: load a kick or choose a Rhythm lane")
        self.clock_tempo.blockSignals(True)
        self.clock_tempo.setValue(self.engine.master_bpm)
        self.clock_tempo.blockSignals(False)
        self.clock_tempo.setEnabled(self.loop_key_lane is None)
        self.overview.update_data(frame, self.engine.rate,
            [(i, dict(lane, view_envelope=self.rows[i].timeline.envelope)) for i, lane in enumerate(lanes) if self.engine.lanes[i].active], clock, self.selected)
        for i, (row, lane) in enumerate(zip(self.rows, lanes)):
            if not self.engine.lanes[i].active:
                continue
            row.timeline.frame, row.timeline.info = frame, lane
            row.timeline.rate = self.engine.rate
            row.timeline.clock = clock
            row.timeline.update()
            name = Path(lane["path"]).name if lane["path"] else "No sound loaded"
            row.file.setText(row.file.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, row.file.width()))
            row.file.setToolTip(lane["path"])
            state = "LOADING" if i in self.loading else lane["state"]
            if lane["playing"]:
                state = "PLAYING"
            elif lane["length"]:
                state = "PAUSED"
            if lane["kind"] == "rhythm":
                row.master.blockSignals(True)
                row.master.setChecked(lane["master_clock"])
                row.master.blockSignals(False)
                row.master.setEnabled(bool(lane["path"]) and lane["state"] != "RECORDING")
                row.tempo.setEnabled(not lane["master_clock"])
                row.add_hits.setEnabled(lane["state"] == "LOOP")
                row.add_hits.blockSignals(True)
                row.add_hits.setChecked(lane["adding_hits"])
                row.add_hits.blockSignals(False)
                if lane["adding_hits"] and lane["playing"]:
                    state = "ADDING HITS"
                if lane["state"] == "RECORDING":
                    state = "RECORDING"
            elif lane["kind"] == "pitch":
                row.curve.blockSignals(True)
                row.curve.setCurrentIndex(row.curve.findData(lane["pitch_curve"]))
                row.curve.blockSignals(False)
                row.curve.setEnabled(lane["state"] != "RECORDING")
                if not row.pitch_slider.isSliderDown():
                    row.pitch_slider.blockSignals(True)
                    row.pitch_slider.setRange(math.ceil(lane["base_min"] * 10), math.floor(lane["base_max"] * 10))
                    row.pitch_slider.setValue(round(lane["base_pitch"] * 10))
                    row.pitch_slider.blockSignals(False)
                row.pitch_value.setText(f"{lane['base_pitch']:+.1f} st")
                row.pitch_loop.setEnabled(bool(lane["pitch_events"] and lane["length"]) and lane["state"] != "RECORDING")
                row.pitch_loop.blockSignals(True)
                row.pitch_loop.setChecked(lane["pitch_loop_enabled"])
                row.pitch_loop.blockSignals(False)
                if lane["state"] == "RECORDING":
                    state = "RECORDING"
            row.state.setText(state)
            if hasattr(row, "beats"):
                row.beats.blockSignals(True)
                row.beats.setValue(lane["beats_per_loop"])
                row.beats.blockSignals(False)
                row.beats.setEnabled(bool(lane["path"]) and lane["state"] != "RECORDING" and (lane["kind"] == "rhythm" or bool(lane["pitch_events"])))
            row.play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause if lane["playing"]
                                                       else QStyle.StandardPixmap.SP_MediaPlay))
            row.play.setEnabled(bool(lane["path"]) and (lane["kind"] != "rhythm" or bool(lane["length"])))
            row.restart.setEnabled(row.play.isEnabled())
            if lane["kind"] == "rhythm":
                detail = f"{len(lane['hits'])} hits"
                if lane["length"]:
                    detail += f"  {lane['length'] / self.engine.rate:.2f}s"
            elif lane["kind"] == "pitch":
                detail = f"{lane['pitch']:+.1f} st"
                if lane["pitch_loop_enabled"] and lane["length"]:
                    detail += f"  {lane['length'] / self.engine.rate:.2f}s"
            else:
                seconds = lane["position"] / lane["source_rate"]
                duration = lane["duration"]
                detail = f"{int(seconds)//60}:{int(seconds)%60:02d} / {int(duration)//60}:{int(duration)%60:02d}"
            row.detail.setText(detail)
            if i in self.lane_editors:
                self.lane_editors[i].update_view(frame, self.engine.rate, lane, row, clock)
        self.update_lane_actions()
        if self.engine.error:
            self.output_status.setText("Disconnected")
            self.status.setText(self.engine.error)
        elif self.engine.underruns:
            self.status.setText(f"Audio underruns: {self.engine.underruns}")
        elif self.engine.voice_drops:
            self.status.setText(f"Voice limit reached: {self.engine.voice_drops} older hits shortened")

    def closeEvent(self, event):
        self.timer.stop()
        self.arrows.clear()
        self.r_held = False
        for editor in self.lane_editors.values():
            editor.close()
        self.engine.close()
        self.pool.waitForDone()
        QApplication.instance().removeEventFilter(self)
        event.accept()


def main():
    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Delta"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=log_dir / "delta.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    def failure(kind, value, trace):
        logging.error("Unhandled error", exc_info=(kind, value, trace))
        if "window" in locals_for_error:
            locals_for_error["window"].status.setText(f"Error: {value}. Details in delta.log.")
    locals_for_error = {}
    sys.excepthook = failure
    app = QApplication(sys.argv)
    window = Window()
    locals_for_error["window"] = window
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
