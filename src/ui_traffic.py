"""Tiny, non-networked traffic visualization using OS counter deltas."""
from __future__ import annotations
from collections import deque
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget
from ui_theme import ACCENT, MUTED, BORDER_SUBTLE
from traffic import format_rate


class Sparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values=deque([0.0]*20,maxlen=20)
        self.setFixedSize(56,22)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def add(self, value):
        self.values.append(max(0.,float(value)))
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        values=list(self.values)
        peak=max(1024.0,max(values)*1.15)
        path=QPainterPath()
        for i,value in enumerate(values):
            x=2+i*(self.width()-4)/(len(values)-1)
            y=self.height()-3-(value/peak)*(self.height()-7)
            if i==0:path.moveTo(x,y)
            else:path.lineTo(x,y)
        painter.setPen(QPen(QColor(ACCENT),1.6))
        painter.drawPath(path)
        painter.end()


class TrafficMiniPanel(QFrame):
    """Explicitly labelled as total PC traffic; never imply per-proxy totals."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('trafficMiniPanel')
        self.setFixedHeight(34)
        self.setToolTip('Общий сетевой трафик этого компьютера, не только выбранного компонента. Без Speedtest.')
        row=QHBoxLayout(self)
        row.setContentsMargins(7,3,7,3)
        row.setSpacing(4)
        label=QLabel('Сеть ПК');label.setObjectName('small');row.addWidget(label)
        self.value=QLabel('↓ 0 Б/с  ↑ 0 Б/с')
        self.value.setObjectName('trafficValue')
        row.addWidget(self.value,1)
        self.graph=Sparkline();row.addWidget(self.graph)

    def update_reading(self,reading):
        self.value.setText(f'↓ {format_rate(reading.download_bps)}   ↑ {format_rate(reading.upload_bps)}')
        self.graph.add(reading.download_bps+reading.upload_bps)
