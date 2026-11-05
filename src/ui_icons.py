"""Original, dependency-free vector icons with bounded semantic motion."""
from __future__ import annotations

from enum import Enum
from math import cos, pi, sin

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton, QWidget
from ui_motion import MotionTokens


ACCENT = QColor('#66E3A4')
ACCENT_BRIGHT = QColor('#8AF0BC')
MUTED = QColor('#8A9AA4')
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
    TELEGRAM = 'telegram'
    COMPONENTS = 'components'
    WARNING = 'warning'
    ACTIVITY = 'activity'
    LAYERS = 'layers'
    WINDOWS = 'windows'
    STRATEGY = 'strategy'


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
        self.interaction_progress = 0.0
        self.setFixedSize(int(size), int(size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._activation = QVariantAnimation(self)
        self._activation.setDuration(MotionTokens.CONFIRM_MS)
        self._activation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._activation.valueChanged.connect(self._activation_changed)

        self._motion = QVariantAnimation(self)
        self._motion.valueChanged.connect(self._motion_changed)
        self._motion.finished.connect(self._motion_finished)
        self._motion_loop = False

        self._interaction = QVariantAnimation(self)
        self._interaction.setDuration(MotionTokens.HOVER_MS)
        self._interaction.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._interaction.valueChanged.connect(self._interaction_changed)

    def _activation_changed(self, value):
        self.activation_progress = float(value);self.update()

    def _motion_changed(self, value):
        self._motion_progress = float(value);self.update()

    @property
    def motion_progress(self):
        return self._motion_progress

    def _interaction_changed(self,value):
        self.interaction_progress=float(value);self.update()

    def _motion_finished(self):
        if self._motion_loop and self.isVisible():
            self._start_motion(loop=True)

    def _start_motion(self, loop=False):
        self._motion_loop=bool(loop)
        self._motion.stop();self._motion.setStartValue(0.0);self._motion.setEndValue(1.0)
        self._motion.setDuration(1050 if loop else MotionTokens.CONFIRM_MS)
        self._motion.setEasingCurve(QEasingCurve.Type.InOutSine if loop else QEasingCurve.Type.OutCubic)
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

        self._motion.stop();self._motion_loop=False;self._motion_progress=0.0
        loops=(state==IconState.LOADING or
               (self.kind==IconKind.CLOUD and state==IconState.ENABLED) or
               (self.kind==IconKind.TELEGRAM and state==IconState.STARTING) or
               (self.kind==IconKind.WARNING and state==IconState.ERROR))
        if animate and self.isVisible() and loops:
            self._start_motion(loop=True)
        elif animate and self.isVisible() and state in {IconState.SUCCESS, IconState.ERROR}:
            self._start_motion(loop=False)
        self.update()

    def set_interaction(self, hovered, pressed=False):
        self._hovered=bool(hovered);self._pressed=bool(pressed)
        target=1.0 if self._hovered else 0.0
        self._interaction.stop()
        if self.isVisible() and abs(self.interaction_progress-target)>.001:
            self._interaction.setStartValue(self.interaction_progress)
            self._interaction.setEndValue(target);self._interaction.start()
        else:self.interaction_progress=target
        self.update()

    def set_active(self, active):
        """Compatibility with the old status-dot surface during migration."""
        self.set_state(IconState.ENABLED if active else IconState.DISABLED)

    def is_animating(self):
        return (self._activation.state()==QAbstractAnimation.State.Running or
                self._motion.state()==QAbstractAnimation.State.Running or
                self._interaction.state()==QAbstractAnimation.State.Running)

    def hideEvent(self, event):
        self._activation.stop();self._motion.stop();self._interaction.stop();self._motion_loop=False
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
        if self.interaction_progress:color=color.lighter(round(100+16*self.interaction_progress))
        if self._pressed:color=color.darker(112)
        return color

    def _paint(self, painter, size):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale=float(size)/24.0
        painter.scale(scale,scale)
        if self._pressed:
            painter.translate(12,12);painter.scale(.92,.92);painter.translate(-12,-12)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        color=self._color()
        glow_strength=(.18+.18*self.interaction_progress) if self.state in {
            IconState.ENABLED,IconState.EXTERNAL,IconState.SUCCESS,IconState.ERROR} else .12*self.interaction_progress
        if glow_strength:
            glow=QColor(color);glow.setAlphaF(min(.42,glow_strength))
            glow_pen=QPen(glow,5.2);glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);painter.setPen(glow_pen)
            self._draw_shape(painter)
        pen=QPen(color,1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        angle=360*self._motion_progress if (self.state==IconState.LOADING and self.kind in {
            IconKind.REFRESH,IconKind.GEAR}) else 0
        if angle:painter.translate(12,12);painter.rotate(angle);painter.translate(-12,-12)
        if self.kind==IconKind.CLOUD and self._motion_loop:
            pulse=1+.045*sin(self._motion_progress*pi)
            painter.translate(12,12);painter.scale(pulse,pulse);painter.translate(-12,-12)
        elif self.kind==IconKind.TELEGRAM and self._motion_loop:
            painter.translate(1.2*sin(self._motion_progress*pi),-1.0*sin(self._motion_progress*pi))
        elif self.kind==IconKind.WARNING and self._motion_loop:
            pulse=1+.035*sin(self._motion_progress*pi)
            painter.translate(12,12);painter.scale(pulse,pulse);painter.translate(-12,-12)
        self._draw_shape(painter)
        if self.state==IconState.EXTERNAL:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(ACCENT_BRIGHT)
            painter.drawEllipse(QPointF(19.5,4.5),2.3,2.3)
        elif self.state==IconState.PARTIAL:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(WARNING)
            painter.drawEllipse(QPointF(19.5,19.5),2.3,2.3)

    def _draw_shape(self,painter):
        getattr(self,f'_draw_{self.kind.value}')(painter)

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
        self._draw_telegram(p)

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
        shift=-1.5*self.interaction_progress
        p.drawLine(QPointF(14+shift,6),QPointF(8+shift,12));p.drawLine(QPointF(8+shift,12),QPointF(14+shift,18))
        p.drawLine(QPointF(8+shift,12),QPointF(19+shift,12))

    def _draw_success(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(8,12,11,15);p.drawLine(11,15,17,9)

    def _draw_error(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(9,9,15,15);p.drawLine(15,9,9,15)

    def _draw_minimize(self,p):
        lift=-.8*self.interaction_progress
        p.drawLine(QPointF(6,13+lift),QPointF(18,13+lift))

    def _draw_telegram(self,p):
        path=QPainterPath();path.moveTo(3.5,11.2);path.lineTo(20.2,4.5)
        path.cubicTo(21.1,4.1,21.7,4.7,21.3,5.7);path.lineTo(15.8,20)
        path.cubicTo(15.5,20.8,14.7,20.9,14.2,20.2);path.lineTo(10.8,15.6)
        path.lineTo(7.1,17.9);path.lineTo(7.7,13.5);path.closeSubpath();p.drawPath(path)
        p.drawLine(QPointF(7.8,13.4),QPointF(17.5,7.3));p.drawLine(QPointF(10.8,15.6),QPointF(17.5,7.3))

    def _draw_components(self,p):
        top=QPolygonF([QPointF(12,3.6),QPointF(20,8),QPointF(12,12.4),QPointF(4,8)])
        left=QPolygonF([QPointF(4,8),QPointF(12,12.4),QPointF(12,21),QPointF(4,16.5)])
        right=QPolygonF([QPointF(20,8),QPointF(12,12.4),QPointF(12,21),QPointF(20,16.5)])
        p.drawPolygon(top);p.drawPolygon(left);p.drawPolygon(right)

    def _draw_warning(self,p):
        path=QPainterPath();path.moveTo(12,3.5);path.cubicTo(12.8,3.5,13.4,4,13.9,4.9)
        path.lineTo(21,17.7);path.cubicTo(21.8,19.2,20.9,20.5,19.3,20.5)
        path.lineTo(4.7,20.5);path.cubicTo(3.1,20.5,2.2,19.2,3,17.7)
        path.lineTo(10.1,4.9);path.cubicTo(10.6,4,11.2,3.5,12,3.5);path.closeSubpath();p.drawPath(path)
        p.drawLine(12,8.5,12,14);p.drawPoint(QPointF(12,17.2))

    def _draw_activity(self,p):
        p.drawLine(2.5,13,6.2,13);p.drawLine(6.2,13,8.2,7);p.drawLine(8.2,7,11.3,18)
        p.drawLine(11.3,18,14,10);p.drawLine(14,10,16.2,13);p.drawLine(16.2,13,21.5,13)

    def _draw_layers(self,p):
        for y in (6.2,11.3,16.4):
            p.drawPolyline(QPolygonF([QPointF(4,y),QPointF(12,y+4),QPointF(20,y),QPointF(12,y-4),QPointF(4,y)]))

    def _draw_windows(self,p):
        p.drawPolygon(QPolygonF([QPointF(4,5),QPointF(11,4),QPointF(11,11),QPointF(4,11)]))
        p.drawPolygon(QPolygonF([QPointF(13,3.8),QPointF(20,2.8),QPointF(20,11),QPointF(13,11)]))
        p.drawPolygon(QPolygonF([QPointF(4,13),QPointF(11,13),QPointF(11,20),QPointF(4,19)]))
        p.drawPolygon(QPolygonF([QPointF(13,13),QPointF(20,13),QPointF(20,21.2),QPointF(13,20.2)]))

    def _draw_strategy(self,p):
        path=QPainterPath();path.moveTo(13.2,2.8);path.lineTo(5.5,13);path.lineTo(11,13)
        path.lineTo(9.9,21.2);path.lineTo(18.5,10);path.lineTo(13,10);path.closeSubpath();p.drawPath(path)


class PremiumIconButton(QPushButton):
    """Custom icon-only control with shared hover, press and glow motion."""
    def __init__(self,kind,accessible_name,size=42,parent=None):
        super().__init__(parent)
        self.kind=IconKind(kind);self.hover_progress=0.0;self.press_progress=0.0
        self.setFixedSize(size,size);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(accessible_name);self.setAccessibleName(accessible_name)
        self.setStyleSheet('border:none;background:transparent;padding:0')
        icon_size=24 if size<=42 else 28
        self.icon_view=AnimatedIcon(self.kind,icon_size,self)
        self.icon_view.setGeometry((size-icon_size)//2,(size-icon_size)//2,icon_size,icon_size)
        self.icon_view.set_state(IconState.ENABLED,animate=False)
        self._hover_anim=QVariantAnimation(self);self._press_anim=QVariantAnimation(self)
        for animation,duration in ((self._hover_anim,MotionTokens.HOVER_MS),(self._press_anim,90)):
            animation.setDuration(duration);animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(lambda value:self._set_hover(value))
        self._press_anim.valueChanged.connect(lambda value:self._set_press(value))

    def _animate(self,animation,current,target):
        animation.stop();animation.setStartValue(current);animation.setEndValue(float(target));animation.start()

    def _set_hover(self,value):self.hover_progress=float(value);self.update()
    def _set_press(self,value):self.press_progress=float(value);self.update()

    def enterEvent(self,event):
        self._animate(self._hover_anim,self.hover_progress,1);self.icon_view.set_interaction(True,self.isDown())
        super().enterEvent(event)

    def leaveEvent(self,event):
        self._animate(self._hover_anim,self.hover_progress,0);self.icon_view.set_interaction(False,False)
        super().leaveEvent(event)

    def mousePressEvent(self,event):
        self._animate(self._press_anim,self.press_progress,1);self.icon_view.set_interaction(True,True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        self._animate(self._press_anim,self.press_progress,0);self.icon_view.set_interaction(self.underMouse(),False)
        super().mouseReleaseEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(1.5+self.press_progress,1.5+self.press_progress,
                    self.width()-3-2*self.press_progress,self.height()-3-2*self.press_progress)
        if self.hover_progress:
            glow=QColor(ACCENT);glow.setAlpha(round(44*self.hover_progress))
            painter.setPen(QPen(glow,4));painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect,12,12)
        border=QColor('#2D393D');hover=QColor('#2E9E75')
        border.setRed(round(border.red()+(hover.red()-border.red())*self.hover_progress))
        border.setGreen(round(border.green()+(hover.green()-border.green())*self.hover_progress))
        border.setBlue(round(border.blue()+(hover.blue()-border.blue())*self.hover_progress))
        painter.setPen(QPen(border,1));painter.setBrush(QColor('#15201D' if self.hover_progress>.01 else '#151B1F'))
        painter.drawRoundedRect(rect,11,11);painter.end()


class IconTextButton(PremiumIconButton):
    """Compact action button with an original vector icon and real button semantics."""
    def __init__(self,kind,text,parent=None,*,accented=False):
        QPushButton.__init__(self,parent)
        self.kind=IconKind(kind);self.hover_progress=0.0;self.press_progress=0.0;self.accented=bool(accented)
        self.setText(text);self.setAccessibleName(text);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(112,36);self.setStyleSheet('border:none;background:transparent;padding:0')
        self.icon_view=AnimatedIcon(self.kind,22,self);self.icon_view.set_state(IconState.ENABLED,animate=False)
        self._hover_anim=QVariantAnimation(self);self._press_anim=QVariantAnimation(self)
        for animation,duration in ((self._hover_anim,MotionTokens.HOVER_MS),(self._press_anim,90)):
            animation.setDuration(duration);animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(lambda value:self._set_hover(value))
        self._press_anim.valueChanged.connect(lambda value:self._set_press(value))

    def resizeEvent(self,event):
        self.icon_view.setGeometry(10,(self.height()-22)//2,22,22);super().resizeEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(1.5+self.press_progress,1.5+self.press_progress,
                    self.width()-3-2*self.press_progress,self.height()-3-2*self.press_progress)
        base=QColor('#13211D' if self.accented else '#11171A')
        hover=QColor('#17372A' if self.accented else '#18231F')
        for channel in ('red','green','blue'):
            setter=getattr(base,'set'+channel.capitalize())
            setter(round(getattr(base,channel)()+(getattr(hover,channel)()-getattr(base,channel)())*self.hover_progress))
        border=QColor(ACCENT if self.accented else '#365047')
        border.setAlpha(round((175 if self.accented else 120)+70*self.hover_progress))
        painter.setPen(QPen(border,1));painter.setBrush(base);painter.drawRoundedRect(rect,11,11)
        painter.setPen(QColor('#F2F6F4'));font=painter.font();font.setWeight(650);painter.setFont(font)
        painter.drawText(QRectF(39,0,self.width()-47,self.height()),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.text())
        painter.end()
