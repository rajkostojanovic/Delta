"""Independent modeless lane editors sharing the main window's audio engine."""
from pathlib import Path
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from board import Timeline, lane_color


class LaneEditor(QDialog):
    def __init__(self, owner, index):
        super().__init__(owner, Qt.WindowType.Window)
        self.owner, self.index = owner, index
        lane = owner.engine.lanes[index]
        self.kind = lane.kind
        self.setModal(False)
        self.setStyleSheet(owner.styleSheet())
        self.resize(1080, 620) if self.kind == "pitch" else self.resize(940, 310)
        self.setMinimumSize(680, 420 if self.kind == "pitch" else 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        toolbar = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {lane_color(index).name()};")
        toolbar.addWidget(self.title, 1)
        self.load = QPushButton("Load / add sound")
        self.load.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load.clicked.connect(lambda: owner.load(index))
        toolbar.addWidget(self.load)
        self.play = QPushButton("Play / pause")
        self.play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play.clicked.connect(lambda: owner.play(index))
        toolbar.addWidget(self.play)
        self.options = QPushButton("Options")
        self.options.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.options.setMenu(owner.rows[index].options_menu)
        toolbar.addWidget(self.options)
        layout.addLayout(toolbar)
        self.timeline = Timeline(index, expanded=True, kind=self.kind)
        self.timeline.selected.connect(lambda: owner.select(index))
        self.timeline.hit_selected.connect(lambda hit: owner.select_hit(index, hit))
        self.timeline.hit_added.connect(lambda hit: owner.add_timeline_hit(index, hit))
        self.timeline.pitch_interval.connect(lambda interval: owner.toggle_pitch_interval(index, interval))
        self.timeline.note_dragged.connect(lambda note, pitch: owner.drag_note_pitch(index, note, pitch))
        layout.addWidget(self.timeline, 1)
        self.details = QLabel()
        layout.addWidget(self.details)
        footer = QHBoxLayout()
        self.hint = QLabel("Hold L, play Up/Down, release L to loop. Drag points freely; double-click an interval for linear / smooth.\nHorizontal guides show pitch height only; there is no snapping." if self.kind == "pitch" else
                           "Hold L, tap Enter, release L to loop. Double-click to add a hit; select a hit and press Delete.")
        self.hint.setWordWrap(True)
        footer.addWidget(self.hint, 1)
        if self.kind == "rhythm":
            self.delete_hit = QPushButton("Delete hit")
            self.delete_hit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.delete_hit.clicked.connect(owner.delete_selected_hit)
            footer.addWidget(self.delete_hit)
        layout.addLayout(footer)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if self.index < len(self.owner.engine.lanes) and self.owner.engine.lanes[self.index].active:
                self.owner.select(self.index)
        super().changeEvent(event)

    def update_view(self, frame, rate, info, row, clock):
        kind = "Melody" if self.kind == "pitch" else "Sound" if self.kind == "ambient" else "Rhythm"
        self.setWindowTitle(f"{info['name']} — {kind} editor")
        self.title.setText(f"{kind} · {info['name']}")
        self.timeline.frame, self.timeline.rate, self.timeline.info = frame, rate, info
        self.timeline.clock, self.timeline.envelope = clock, row.timeline.envelope
        self.timeline.active = self.owner.selected == self.index
        self.timeline.selected_hit = row.timeline.selected_hit
        self.timeline.update()
        self.load.setEnabled(self.index not in self.owner.loading)
        self.play.setEnabled(row.play.isEnabled())
        self.play.setText("Pause" if info["playing"] else "Play")
        self.details.setText(f"{Path(info['path']).name if info['path'] else 'No sound loaded'} · {row.state.text()} · {row.detail.text()}")
        if self.kind == "rhythm":
            self.delete_hit.setEnabled(self.owner.selected_hit is not None and self.owner.selected_hit[0] == self.index)

    def closeEvent(self, event):
        if self.owner.loop_key_lane == self.index:
            self.owner.cancel_held_loop()
        # Closing an editor hides its window; its lane and audio keep running.
        event.accept()
