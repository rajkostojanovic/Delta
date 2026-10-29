"""Horizontal score view: lane stems, loop brackets, and recorded sound events."""
import math
import numpy as np
from pitch_curve import interpolate_pitch
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy, QPushButton


PALETTE = ("#3c8277", "#be7542", "#7c67ad", "#b55465", "#527fa3", "#8b842d", "#416aab", "#96664c")


def lane_color(index):
    if index < len(PALETTE):
        return QColor(PALETTE[index])
    return QColor.fromHsv(round(index * 137.508) % 360, 155, 155)


def loop_frames(info, rate, frame):
    if info["length"] and (info["kind"] == "rhythm" or info.get("pitch_loop_enabled")):
        return max(1, info["length"])
    if info["state"] == "RECORDING":
        events = info["hits"] if info["kind"] == "rhythm" else [at for at, _ in info["pitch_events"]]
        return max(rate, frame - info["origin"] + rate // 4, max(events, default=0) + rate // 4)
    if info["kind"] != "rhythm" and info["duration"]:
        speed = info["tempo"] * (2 ** (info["pitch"] / 12) if info["kind"] == "pitch" else 1)
        return max(1, round(info["duration"] * rate / speed))
    return 0


def loop_phase(info, frame, length):
    if not length:
        return 0
    if info["kind"] == "rhythm":
        return ((frame - info["origin"]) % length if info["playing"] else info["paused_phase"]) / length
    if info.get("pitch_loop_enabled"):
        return ((frame - info["origin"]) % length if info["playing"] else info["loop_paused_phase"]) / length
    return info["position"] / max(1, info["duration"] * info["source_rate"])


def pitch_plot(info, length, count):
    tempo = 1 if info["state"] == "RECORDING" else info["tempo"]
    raw_length = info.get("raw_length", round(length * tempo)) if info["state"] != "RECORDING" else round(length * tempo)
    times = [round(at / tempo) for at, _ in info["pitch_events"] if at < raw_length]
    positions = np.unique(np.concatenate((np.linspace(0, length, count), times, [at - 1 for at in times if at > 0])))
    values = interpolate_pitch(info["pitch_events"], raw_length, positions, tempo,
                               info.get("pitch_curve", "smooth"), info.get("pitch_modes", {}))
    return positions, values


class GroupHeader(QWidget):
    add_lane = Signal()

    def __init__(self, title, count):
        super().__init__()
        self.title, self.count = title, count
        self.setFixedWidth(110)
        self.setMinimumHeight(100)
        self.add_button = QPushButton("+", self)
        self.add_button.setFixedSize(28, 28)
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.setToolTip(f"Add {title.lower()} lane")
        self.add_button.setAccessibleName(f"Add {title} lane")
        self.add_button.clicked.connect(self.add_lane.emit)

    def resizeEvent(self, event):
        self.add_button.move(25, self.height() // 2 + 8)
        super().resizeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = QColor("#3b443e")
        p.setPen(QPen(ink, 1.3))
        center = self.height() // 2
        p.drawRect(0, center - 30, 78, 32)
        font = p.font()
        font.setPointSize(11)
        p.setFont(font)
        p.drawText(0, center - 30, 78, 32, Qt.AlignmentFlag.AlignCenter, self.title)
        if not self.count:
            return
        p.drawLine(78, center - 14, 92, center - 14)
        centers = [round((i + .5) * self.height() / self.count) for i in range(self.count)]
        p.drawLine(92, centers[0], 92, centers[-1])
        p.drawLine(92, center - 14, 92, centers[0])
        for y in centers:
            p.drawLine(92, y, self.width(), y)


class Timeline(QWidget):
    selected = Signal()
    hit_selected = Signal(int)
    hit_added = Signal(int)
    pitch_interval = Signal(int)
    note_dragged = Signal(int, float)

    def __init__(self, index, expanded=False, kind=None):
        super().__init__()
        self.expanded, self.kind = expanded, kind
        self.index, self.frame, self.rate = index, 0, 48000
        self.info = None
        self.envelope = []
        self.active = False
        self.clock = None
        self.selected_hit = None
        self.drag_note = None
        self.drag_scale = None
        self.drag_y = self.drag_pitch = 0.
        self.setMouseTracking(True)
        self.setMinimumSize(520 if expanded else 260, 280 if expanded and kind == "pitch" else 100 if expanded else 85)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Single loop editor")

    def x_at(self, position):
        length = loop_frames(self.info, self.rate, self.frame) if self.info else 1
        return 14 + position / max(1, length) * (self.width() - 28)

    def position_at(self, x):
        length = loop_frames(self.info, self.rate, self.frame)
        return min(length - 1, max(0, (x - 14) / max(1, self.width() - 28) * length))

    def pitch_geometry(self):
        info = self.info
        if not info or info["kind"] != "pitch" or not info.get("pitch_events"):
            return [], 0., 4.
        length = loop_frames(info, self.rate, self.frame)
        scale = 1 if info["state"] == "RECORDING" else info["tempo"]
        points = [(i, at / scale, pitch) for i, (at, pitch) in enumerate(info["pitch_events"]) if at / scale < length]
        if not points:
            return [], 0., 4.
        low, high = min(pitch for _, _, pitch in points), max(pitch for _, _, pitch in points)
        middle, span = self.drag_scale or self.pitch_scale(low, high)
        return [(i, self.x_at(at), self.height() // 2 - (pitch - middle) / span * self.pitch_extent(), pitch)
                for i, at, pitch in points], middle, span

    def pitch_extent(self):
        return max(40, self.height() - 90) if self.expanded and self.kind == "pitch" else 40

    def pitch_scale(self, low, high):
        return (low + high) / 2, max(12, high - low + 4) if self.expanded else max(4, high - low)

    def note_at(self, position):
        if not self.info or not self.info.get("pitch_loop_enabled") or self.info["state"] == "RECORDING":
            return None
        points, _, _ = self.pitch_geometry()
        nearest = min(((math.hypot(x - position.x(), y - position.y()), i, pitch) for i, x, y, pitch in points), default=None)
        return nearest if nearest and nearest[0] <= (12 if self.expanded else 9) else None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit()
            note = self.note_at(event.position())
            if note is not None:
                _, self.drag_note, self.drag_pitch = note
                self.drag_y = event.position().y()
                _, middle, span = self.pitch_geometry()
                self.drag_scale = (middle, span)
                self.setCursor(Qt.CursorShape.SizeVerCursor)
                event.accept()
                return
            info = self.info
            if info and info["kind"] == "rhythm" and info["length"]:
                hits = [(abs(self.x_at(round(raw / info["tempo"])) - event.position().x()), raw)
                        for raw in info.get("raw_hits", []) if raw < round(info["length"] * info["tempo"])]
                closest = min(hits) if hits else None
                self.hit_selected.emit(closest[1] if closest and closest[0] <= 8 else -1)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_note is not None and event.buttons() & Qt.MouseButton.LeftButton:
            fine = .1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.
            pitch = self.drag_pitch + (self.drag_y - event.position().y()) * self.drag_scale[1] / self.pitch_extent() * fine
            pitch = round(pitch, 2 if fine < 1 else 1)
            self.note_dragged.emit(self.drag_note, pitch)
            event.accept()
            return
        self.setCursor(Qt.CursorShape.SizeVerCursor if self.note_at(event.position()) else Qt.CursorShape.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_note is not None:
            self.drag_note = None
            self.drag_scale = None
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.info and self.info["kind"] == "rhythm" and self.info["length"]:
            self.hit_added.emit(round(self.position_at(event.position().x()) * self.info["tempo"]))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.info and self.info["kind"] == "pitch" and self.info.get("pitch_loop_enabled") and self.info["length"]:
            self.selected.emit()
            if self.note_at(event.position()) is not None:
                event.accept()
                return
            position = self.position_at(event.position().x())
            candidates = [(i, round(at / self.info["tempo"])) for i, (at, _) in enumerate(self.info["pitch_events"])
                          if at < self.info.get("raw_length", round(self.info["length"] * self.info["tempo"]))]
            interval = next((i for i, at in reversed(candidates) if at <= position), None)
            if interval is not None:
                self.pitch_interval.emit(interval)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink = lane_color(self.index)
        if self.active:
            tint = QColor(ink)
            tint.setAlpha(18)
            p.fillRect(self.rect(), tint)
        info, center = self.info, self.height() // 2
        if self.expanded and self.kind == "pitch":
            # Relative pitch-height guides, deliberately without frequency labels or snapping.
            spacing = self.pitch_extent() / 12
            for n in range(-6, 7):
                y = round(center + n * spacing)
                if n % 2 == 0:
                    band = QColor(ink)
                    band.setAlpha(8)
                    p.fillRect(14, y, self.width() - 28, max(1, round(spacing)), band)
                p.setPen(QPen(QColor("#ccd5c7"), 1))
                p.drawLine(14, y, self.width() - 14, y)
        faint = QColor(ink)
        faint.setAlpha(65)
        p.setPen(QPen(faint, 1))
        p.drawLine(14, center, self.width() - 14, center)
        if not info or not info["path"]:
            p.setPen(ink)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a sound")
            return
        length = loop_frames(info, self.rate, self.frame)
        if not length:
            p.setPen(ink)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Hold L and tap Enter to record a loop")
            return
        beats = info.get("beats_per_loop", 0) if info["kind"] == "rhythm" or info.get("pitch_loop_enabled") else 0
        if beats:
            for n in range(beats + 1):
                x = self.x_at(n / beats * length)
                p.setPen(QPen(QColor("#d6ddd0"), 1))
                p.drawLine(int(x), 23, int(x), self.height() - 17)
        # Exactly one loop, bounded by a pair of brackets.
        p.setPen(QPen(ink, 1.5))
        for x, direction in ((14, 1), (self.width() - 14, -1)):
            extent = round(self.pitch_extent() / 2) if self.expanded and self.kind == "pitch" else 22
            p.drawLine(x, center - extent, x, center + extent)
            for y in (center - extent, center + extent):
                p.drawLine(x, y, x + direction * 8, y)
        if info["kind"] == "rhythm":
            for hit in info["hits"]:
                if not 0 <= hit < length:
                    continue
                selected = self.selected_hit is not None and round(self.selected_hit / info["tempo"]) == hit
                p.setPen(QPen(QColor("#b64931") if selected else ink, 4 if selected else 2.5))
                x = round(self.x_at(hit))
                p.drawLine(x, center - 17, x, center + 17)
        elif info["kind"] == "pitch" and info.get("pitch_events") and (info.get("pitch_loop_enabled") or info["state"] == "RECORDING"):
            scale = 1 if info["state"] == "RECORDING" else info["tempo"]
            events = [(at / scale, pitch) for at, pitch in info["pitch_events"] if at / scale < length]
            if events:
                low, high = min(pitch for _, pitch in events), max(pitch for _, pitch in events)
                middle, span = self.drag_scale or self.pitch_scale(low, high)
                positions, pitches = pitch_plot(info, length, max(32, min(256, self.width() // 3)))
                previous = None
                p.setPen(QPen(ink, 1.8))
                for at, pitch in zip(positions, pitches):
                    point = (self.x_at(at), center - (pitch - middle) / span * self.pitch_extent())
                    if previous:
                        p.drawLine(int(previous[0]), int(previous[1]), int(point[0]), int(point[1]))
                    previous = point
                for at, pitch in events:
                    radius = 5 if self.expanded else 3
                    p.drawEllipse(int(self.x_at(at)) - radius, int(center - (pitch - middle) / span * self.pitch_extent()) - radius, radius * 2, radius * 2)
                if self.drag_note is not None and self.drag_note < len(info["pitch_events"]):
                    at, pitch = info["pitch_events"][self.drag_note]
                    p.drawText(int(self.x_at(at / scale)) + 8, int(center - (pitch - middle) / span * self.pitch_extent()) - 8, f"{pitch:+.1f} st")
        else:
            # A live Melody or ambience lane shows one complete source-sound cycle.
            p.setPen(QPen(ink, 1))
            for n, value in enumerate(self.envelope):
                x = 14 + n / max(1, len(self.envelope) - 1) * (self.width() - 28)
                height = min(1, value) * 20
                p.drawLine(int(x), int(center - height), int(x), int(center + height))
        text = "Recording…" if info["state"] == "RECORDING" else f"One loop · {length / self.rate:.2f}s" + (f" · {beats} beats" if beats else "")
        p.setPen(ink)
        p.drawText(14, 15, text)
        if info["state"] != "RECORDING":
            x = round(self.x_at(loop_phase(info, self.frame, length) * length))
            p.setPen(QPen(QColor("#454d43"), 1.5))
            p.drawLine(x, 23, x, self.height() - 17)


class Overview(QWidget):
    lane_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.frame, self.rate = 0, 48000
        self.lanes, self.clock = [], None
        self.mode = "Combined"
        self.zoom = 200
        self.selected_index = None
        self.legend = []
        self.setMinimumWidth(280)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("All lanes on a shared timeline")
        self.setToolTip("Colors match the loops above. Click a colored lane name to select it.")

    def set_mode(self, mode):
        self.mode = mode
        self.update_data(self.frame, self.rate, self.lanes, self.clock, self.selected_index)

    def set_zoom(self, percent):
        self.zoom = max(50, min(800, int(percent)))
        self.update()

    def update_data(self, frame, rate, lanes, clock, selected):
        self.frame, self.rate, self.lanes, self.clock, self.selected_index = frame, rate, lanes, clock, selected
        self.update()

    def time_window(self):
        lengths = [loop_frames(info, self.rate, self.frame) for _, info in self.lanes if info["path"]]
        longest = max(lengths, default=self.rate)
        if self.clock:
            counts = [info["beats_per_loop"] for _, info in self.lanes if info["beats_per_loop"] and info["path"] and (info["kind"] == "rhythm" or info.get("pitch_loop_enabled"))]
            common = math.lcm(self.clock["beats"], *counts)
            beats = common if 1 < len(counts) and common <= 64 else self.clock["beats"] * 4
            span = max(longest, round(beats * self.clock["beat_frames"]))
            origin = self.clock["origin"]
        else:
            span, origin = max(12 * self.rate, longest), 0
        span = max(1, round(span * 100 / self.zoom))
        start = origin + math.floor((self.frame - origin) / span) * span
        return start, span

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, index in self.legend:
                if rect.contains(event.position()):
                    self.lane_selected.emit(index)
                    break
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#efeee4"))
        self.legend = []
        x, legend_y = 12, 17
        for index, info in self.lanes:
            if x + 125 > self.width():
                x, legend_y = 12, legend_y + 19
            color = lane_color(index)
            p.setPen(QPen(color, 3))
            p.drawLine(x, legend_y - 4, x + 12, legend_y - 4)
            p.setPen(color)
            name = p.fontMetrics().elidedText(info["name"], Qt.TextElideMode.ElideRight, 100)
            p.drawText(x + 18, legend_y, name)
            self.legend.append((QRectF(x, legend_y - 15, 120, 19), index))
            x += 130
        if not self.lanes:
            p.setPen(QColor("#8b9485"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Add lanes above to see how their loops line up")
            return
        top, bottom = legend_y + 22, self.height() - 24
        if bottom <= top:
            return
        start, span = self.time_window()
        def x_at(frame):
            return 12 + (frame - start) / span * (self.width() - 24)
        p.setPen(QPen(QColor("#cbd2c5"), 1))
        p.drawLine(12, bottom + 1, self.width() - 12, bottom + 1)
        if self.mode == "Combined":
            p.drawLine(12, round((top + bottom) / 2), self.width() - 12, round((top + bottom) / 2))
        if self.clock:
            beat = self.clock["beat_frames"]
            count = max(1, math.floor(span / beat))
            step = max(1, math.ceil(count / 16))
            ticks = [start + n * beat for n in range(0, count + 1, step)]
        else:
            ticks = [start + span * n / 8 for n in range(9)]
        for at in ticks:
            x = x_at(at)
            p.setPen(QPen(QColor("#d9ddcf"), 1))
            p.drawLine(int(x), top - 8, int(x), bottom)
            p.setPen(QColor("#6e7869"))
            text = f"{(at - start) / self.rate:.1f}s"
            p.drawText(int(min(self.width() - 42, max(12, x - 8))), self.height() - 7, text)
        p.save()
        p.setClipRect(QRectF(12, top - 8, self.width() - 24, bottom - top + 16))
        magnification = self.zoom / 100
        available = (bottom - top) / max(1, len(self.lanes)) if self.mode == "Separate rows" else bottom - top
        half_height = max(3, min(7 * magnification, available / 2 - 2))
        pitch_height = max(4, min(9 * magnification, available - 6))
        for ordinal, (index, info) in enumerate(self.lanes):
            if not info["path"]:
                continue
            length = loop_frames(info, self.rate, self.frame)
            if not length or info["state"] == "RECORDING":
                continue
            if self.mode == "Combined":
                center = (top + bottom) / 2 + (ordinal - (len(self.lanes) - 1) / 2) * min(6 * magnification, (bottom - top) / max(1, len(self.lanes)))
            else:
                center = top + (ordinal + .5) * (bottom - top) / max(1, len(self.lanes))
            color = lane_color(index)
            if index != self.selected_index:
                color.setAlpha(170 if info["playing"] else 100)
            origin = info["origin"]
            if not info["playing"]:
                origin = self.frame - loop_phase(info, self.frame, length) * length
            elif info["kind"] != "rhythm" and not info.get("pitch_loop_enabled"):
                origin = self.frame - loop_phase(info, self.frame, length) * length
            first = math.floor((start - origin) / length)
            if info["playing"] and (info["kind"] == "rhythm" or info.get("pitch_loop_enabled")):
                first = max(0, first)
            last = min(first + 256, math.ceil((start + span - origin) / length))
            for cycle in range(first, last):
                edge = origin + cycle * length
                left, right = x_at(edge), x_at(edge + length)
                p.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                p.drawRect(QRectF(left, center - half_height, max(1, right - left), half_height * 2))
                p.setPen(QPen(color, 2))
                if info["kind"] == "rhythm":
                    for hit in info["hits"]:
                        x = x_at(edge + hit)
                        p.drawLine(int(x), int(center - half_height), int(x), int(center + half_height))
                elif info.get("pitch_loop_enabled") and info["pitch_events"]:
                    notes = [pitch for at, pitch in info["pitch_events"] if at / info["tempo"] < length]
                    if not notes:
                        continue
                    middle, scale = (min(notes) + max(notes)) / 2, max(4, max(notes) - min(notes))
                    previous = None
                    positions, pitches = pitch_plot(info, length, max(8, min(65, round((right - left) / 3))))
                    for at, pitch in zip(positions, pitches):
                        point = (x_at(edge + at), center - (pitch - middle) / scale * pitch_height)
                        if previous:
                            p.drawLine(int(previous[0]), int(previous[1]), int(point[0]), int(point[1]))
                        previous = point
                else:
                    envelope = info.get("view_envelope", [])
                    if envelope:
                        for n, value in enumerate(envelope):
                            x = left + n / max(1, len(envelope) - 1) * (right - left)
                            amplitude = min(1, value) * half_height
                            p.drawLine(int(x), int(center - amplitude), int(x), int(center + amplitude))
                    else:
                        p.drawLine(int(left), int(center), int(right), int(center))
        now = x_at(self.frame)
        p.setPen(QPen(QColor("#414b40"), 2))
        p.drawLine(int(now), top - 8, int(now), bottom)
        p.restore()


