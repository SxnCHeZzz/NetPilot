"""Original, dependency-free vector icons with bounded semantic motion."""
from __future__ import annotations

from enum import Enum
from math import cos, pi, sin

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget


ACCENT = QColor('#66E3A4')
ACCENT_BRIGHT = QColor('#8AF0BC')
MUTED = QColor('#66736E')
TEXT = QColor('#F2F6F4')
WARNING = QColor('#F2BD62')
ERROR = QColor('#FF7285')


class IconKind(str, Enum):
    LOCK = 'lock'
    CLOUD = 'cloud'
    NETWORK = 'network'
    GEAR = 'gear'
    REFRESH = 'refresh'
    DOWNLOAD = 'download'
    BELL = 'bell'
    BACK = 'back'
    SUCCESS = 'success'
    ERROR = 'error'
    MINIMIZE = 'minimize'


class IconState(str, Enum):
    DISABLED = 'disabled'
    STARTING = 'starting'
    ENABLED = 'enabled'
    STOPPING = 'stopping'
    EXTERNAL = 'external'
    PARTIAL = 'partial'
    LOADING = 'loading'
    SUCCESS = 'success'
    ERROR = 'error'


class AnimatedIcon(QWidget):
    """One semantic icon surface; animations exist only during transitions."""
    def __init__(self, kind, size=24, parent=None):
        super().__init__(parent)
        self.kind = IconKind(kind)
        self.state = IconState.DISABLED
        self.activation_progress = 0.0
        self._motion_progress = 0.0
        self._hovered = False
        self._pressed = False
        self.setFixedSize(int(size), int(size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._activation = QVariantAnimation(self)
        self._activation.setDuration(180)
        self._activation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._activation.valueChanged.connect(self._activation_changed)

        self._motion = QVariantAnimation(self)
        self._motion.valueChanged.connect(self._motion_changed)
        self._motion.finished.connect(self._motion_finished)

    def _activation_changed(self, value):
        self.activation_progress = float(value);self.update()

    def _motion_changed(self, value):
        self._motion_progress = float(value);self.update()

    def _motion_finished(self):
        if self.state == IconState.LOADING and self.isVisible():
            self._start_motion(loop=True)

    def _start_motion(self, loop=False):
        self._motion.stop();self._motion.setStartValue(0.0);self._motion.setEndValue(1.0)
        self._motion.setDuration(720 if loop else 220)
        self._motion.setEasingCurve(QEasingCurve.Type.Linear if loop else QEasingCurve.Type.OutCubic)
        self._motion.start()

    def set_state(self, state, animate=True):
        state = IconState(state)
        self.state = state
        target = 1.0 if state == IconState.ENABLED else 0.0
        self._activation.stop()
        if animate and self.isVisible() and abs(self.activation_progress-target) > .001:
            self._activation.setStartValue(self.activation_progress)
            self._activation.setEndValue(target);self._activation.start()
        else:
            self.activation_progress=target

        self._motion.stop();self._motion_progress=0.0
        if animate and self.isVisible() and state == IconState.LOADING:
            self._start_motion(loop=True)
        elif animate and self.isVisible() and state in {IconState.SUCCESS, IconState.ERROR}:
            self._start_motion(loop=False)
        self.update()

    def set_interaction(self, hovered, pressed=False):
        self._hovered=bool(hovered);self._pressed=bool(pressed);self.update()

    def set_active(self, active):
        """Compatibility with the old status-dot surface during migration."""
        self.set_state(IconState.ENABLED if active else IconState.DISABLED)

    def is_animating(self):
        return (self._activation.state()==QAbstractAnimation.State.Running or
                self._motion.state()==QAbstractAnimation.State.Running)

    def hideEvent(self, event):
        self._activation.stop();self._motion.stop()
        super().hideEvent(event)

    def render_pixmap(self, logical_size=None, dpr=1.0):
        logical_size=int(logical_size or min(self.width(),self.height()))
        pixels=max(1,round(logical_size*float(dpr)))
        pixmap=QPixmap(pixels,pixels);pixmap.setDevicePixelRatio(float(dpr))
        pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap);self._paint(painter,logical_size);painter.end()
        return pixmap

    def paintEvent(self, event):
        painter=QPainter(self);self._paint(painter,min(self.width(),self.height()));painter.end()

    def _color(self):
        colors={
            IconState.DISABLED:MUTED, IconState.STARTING:WARNING,
            IconState.ENABLED:ACCENT, IconState.STOPPING:WARNING,
            IconState.EXTERNAL:ACCENT_BRIGHT, IconState.PARTIAL:WARNING,
            IconState.LOADING:ACCENT_BRIGHT, IconState.SUCCESS:ACCENT,
            IconState.ERROR:ERROR,
        }
        color=QColor(colors[self.state])
        if self._hovered:color=color.lighter(116)
        if self._pressed:color=color.darker(112)
        return color

    def _paint(self, painter, size):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale=float(size)/24.0
        painter.scale(scale,scale)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen=QPen(self._color(),1.75)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        angle=360*self._motion_progress if self.state==IconState.LOADING else 0
        if angle:painter.translate(12,12);painter.rotate(angle);painter.translate(-12,-12)
        getattr(self,f'_draw_{self.kind.value}')(painter)
        if self.state==IconState.EXTERNAL:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(ACCENT_BRIGHT)
            painter.drawEllipse(QPointF(19.5,4.5),2.3,2.3)
        elif self.state==IconState.PARTIAL:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(WARNING)
            painter.drawEllipse(QPointF(19.5,19.5),2.3,2.3)

    def _draw_lock(self,p):
        opened=self.activation_progress
        p.drawRoundedRect(5.5,10,13,10,2.4,2.4)
        path=QPainterPath();path.moveTo(8,10)
        path.cubicTo(8,4.5,16,4.5,16+3.2*opened,10-3.0*opened)
        p.drawPath(path);p.drawEllipse(QPointF(12,15),1.1,1.1)

    def _draw_cloud(self,p):
        path=QPainterPath();path.moveTo(6,18);path.cubicTo(2,18,2,12,7,11)
        path.cubicTo(7,5,16,4,18,11);path.cubicTo(23,11,23,18,18,18);path.closeSubpath();p.drawPath(path)

    def _draw_network(self,p):
        p.drawRoundedRect(4,4,16,5,1.5,1.5);p.drawRoundedRect(4,15,16,5,1.5,1.5)
        p.drawEllipse(QPointF(7,6.5),.8,.8);p.drawEllipse(QPointF(7,17.5),.8,.8)
        p.drawLine(16,6.5,18,6.5);p.drawLine(16,17.5,18,17.5)

    def _draw_gear(self,p):
        p.drawEllipse(QPointF(12,12),4.3,4.3);p.drawEllipse(QPointF(12,12),1.2,1.2)
        turn=(.08 if self._hovered else 0)*pi
        for index in range(8):
            angle=index*pi/4+turn
            p.drawLine(QPointF(12+6*cos(angle),12+6*sin(angle)),QPointF(12+8.5*cos(angle),12+8.5*sin(angle)))

    def _draw_refresh(self,p):
        path=QPainterPath();path.moveTo(18.5,9);path.cubicTo(16,4.5,8.5,4.5,6,9);p.drawPath(path)
        p.drawLine(18.5,9,18.5,5.5);p.drawLine(18.5,9,15,9)
        path=QPainterPath();path.moveTo(5.5,15);path.cubicTo(8,19.5,15.5,19.5,18,15);p.drawPath(path)
        p.drawLine(5.5,15,5.5,18.5);p.drawLine(5.5,15,9,15)

    def _draw_download(self,p):
        p.drawLine(12,4,12,15);p.drawLine(8,11,12,15);p.drawLine(16,11,12,15)
        p.drawLine(5,19,19,19)

    def _draw_bell(self,p):
        path=QPainterPath();path.moveTo(6,17);path.lineTo(8,14);path.lineTo(8,10)
        path.cubicTo(8,5,16,5,16,10);path.lineTo(16,14);path.lineTo(18,17);path.closeSubpath();p.drawPath(path)
        p.drawArc(10,17,4,3,180*16,180*16)

    def _draw_back(self,p):
        shift=-1.5 if self._hovered else 0
        p.drawLine(QPointF(14+shift,6),QPointF(8+shift,12));p.drawLine(QPointF(8+shift,12),QPointF(14+shift,18))
        p.drawLine(QPointF(8+shift,12),QPointF(19+shift,12))

    def _draw_success(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(8,12,11,15);p.drawLine(11,15,17,9)

    def _draw_error(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(9,9,15,15);p.drawLine(15,9,9,15)

    def _draw_minimize(self,p):
        p.drawLine(6,13,18,13)
