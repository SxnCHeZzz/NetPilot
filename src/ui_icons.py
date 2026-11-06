"""Original, dependency-free vector icons with bounded semantic motion."""
from __future__ import annotations

from enum import Enum
from math import cos, pi, sin

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QPushButton, QWidget
from ui_motion import MotionTokens
from ui_theme import ACCENT as THEME_ACCENT, ACCENT_HOVER as THEME_ACCENT_BRIGHT, MUTED as THEME_MUTED, TEXT as THEME_TEXT, ERROR as THEME_ERROR


ACCENT = QColor(THEME_ACCENT)
ACCENT_BRIGHT = QColor(THEME_ACCENT_BRIGHT)
MUTED = QColor(THEME_MUTED)
TEXT = QColor(THEME_TEXT)
WARNING = QColor('#F2BD62')
ERROR = QColor(THEME_ERROR)
GEAR_HOVER_DEGREES = 20


class IconKind(str, Enum):
    LOCK = 'lock'
    SHIELD = 'shield'
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
    BRAND = 'brand'
    CHEVRON = 'chevron'


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




def paint_brand_symbol(p):
    """Draw the compact original NetPilot network route at 24x24 coordinates."""
    p.drawRoundedRect(QRectF(2.9,2.9,18.2,18.2),4.8,4.8)
    route=QPainterPath()
    route.moveTo(7.2,16.7)
    route.lineTo(11.5,11.6)
    route.lineTo(17.3,7.3)
    p.drawPath(route)
    p.drawLine(QPointF(7.0,7.5),QPointF(11.5,11.6))
    for x,y in ((7.0,7.5),(7.2,16.7),(11.5,11.6),(17.3,7.3)):
        p.drawEllipse(QPointF(x,y),1.25,1.25)

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
        self.idle_glow = True
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
               (self.kind==IconKind.WARNING and state in {IconState.ENABLED,IconState.ERROR}))
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
        semantic_glow=.18 if self.idle_glow and self.state in {
            IconState.ENABLED,IconState.EXTERNAL,IconState.SUCCESS,IconState.ERROR} else 0.0
        glow_strength=semantic_glow+.12*self.interaction_progress
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

    def _draw_shield(self,p):
        """Stable, symmetric shield + lock; no awkward open-shackle geometry.

        Operational state is conveyed by color and the status text, not by
        bending the shackle in an unclear direction.
        """
        shield=QPainterPath()
        shield.moveTo(12,2.3)
        shield.cubicTo(9.7,3.6,6.5,4.3,4,5.0)
        shield.lineTo(4,12.1)
        shield.cubicTo(4,16.9,7.5,20.2,12,22.0)
        shield.cubicTo(16.5,20.2,20,16.9,20,12.1)
        shield.lineTo(20,5.0)
        shield.cubicTo(17.5,4.3,14.3,3.6,12,2.3)
        shield.closeSubpath()
        p.drawPath(shield)
        # Centered rounded padlock, deliberately small enough to read at 18 px.
        p.drawRoundedRect(QRectF(8.1,11.5,7.8,6.4),1.15,1.15)
        shackle=QPainterPath()
        shackle.moveTo(9.75,11.5)
        shackle.lineTo(9.75,9.6)
        shackle.cubicTo(9.75,6.55,14.25,6.55,14.25,9.6)
        shackle.lineTo(14.25,11.5)
        p.drawPath(shackle)
        p.drawPoint(QPointF(12,14.9))

    def _draw_cloud(self,p):
        path=QPainterPath();path.moveTo(6,18);path.cubicTo(2,18,2,12,7,11)
        path.cubicTo(7,5,16,4,18,11);path.cubicTo(23,11,23,18,18,18);path.closeSubpath();p.drawPath(path)

    def _draw_network(self,p):
        self._draw_telegram(p)

    def _draw_gear(self,p):
        p.drawEllipse(QPointF(12,12),4.3,4.3);p.drawEllipse(QPointF(12,12),1.2,1.2)
        turn=(GEAR_HOVER_DEGREES/180)*pi*self.interaction_progress
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
        shift=0.0  # Navigation arrow stays centered on hover; no sideways jump.
        p.drawLine(QPointF(14+shift,6),QPointF(8+shift,12));p.drawLine(QPointF(8+shift,12),QPointF(14+shift,18))
        p.drawLine(QPointF(8+shift,12),QPointF(19+shift,12))

    def _draw_success(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(8,12,11,15);p.drawLine(11,15,17,9)

    def _draw_error(self,p):
        p.drawEllipse(QPointF(12,12),8,8);p.drawLine(9,9,15,15);p.drawLine(15,9,9,15)

    def _draw_minimize(self,p):
        p.drawLine(QPointF(6,13),QPointF(18,13))

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

    def _draw_brand(self,p):
        # Three connected nodes inside a rounded-square route map.
        # Shared with the tray icon and installer, no old-school globe lines.
        paint_brand_symbol(p)

    def _draw_chevron(self,p):
        shift=1.2*self.interaction_progress
        p.drawLine(QPointF(9+shift,6),QPointF(15+shift,12));p.drawLine(QPointF(15+shift,12),QPointF(9+shift,18))


class PremiumIconButton(QPushButton):
    """Custom icon-only control with shared hover, press and glow motion."""
    def __init__(self,kind,accessible_name,size=42,parent=None,*,visual_size=None,
                 icon_size=None,corner_radius=None,idle_glow=True):
        super().__init__(parent)
        self.kind=IconKind(kind);self.hover_progress=0.0;self.press_progress=0.0
        self.visual_size=int(visual_size or size)
        self.corner_radius=float(corner_radius or min(11,self.visual_size/3))
        self.setFixedSize(size,size);self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(accessible_name);self.setAccessibleName(accessible_name)
        self.setStyleSheet('border:none;background:transparent;padding:0')
        icon_size=int(icon_size or (24 if size<=42 else 28))
        self.icon_view=AnimatedIcon(self.kind,icon_size,self)
        self.icon_view.idle_glow=bool(idle_glow)
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
        inset=(self.width()-self.visual_size)/2+.5
        press=.65*self.press_progress
        rect=QRectF(inset+press,inset+press,
                    self.visual_size-1-2*press,self.visual_size-1-2*press)
        compact=self.visual_size<self.width()
        if self.hover_progress and not compact:
            glow=QColor(ACCENT);glow.setAlpha(round(44*self.hover_progress))
            painter.setPen(QPen(glow,4));painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect,self.corner_radius,self.corner_radius)
        border=QColor('#365642');hover=QColor('#7DE7A1')
        border.setRed(round(border.red()+(hover.red()-border.red())*self.hover_progress))
        border.setGreen(round(border.green()+(hover.green()-border.green())*self.hover_progress))
        border.setBlue(round(border.blue()+(hover.blue()-border.blue())*self.hover_progress))
        background=QColor('#14291E');hover_background=QColor('#254634')
        for channel in ('red','green','blue'):
            setter=getattr(background,'set'+channel.capitalize())
            setter(round(getattr(background,channel)()+
                         (getattr(hover_background,channel)()-getattr(background,channel)())*self.hover_progress))
        painter.setPen(QPen(border,1));painter.setBrush(background)
        painter.drawRoundedRect(rect,self.corner_radius,self.corner_radius);painter.end()


class IconTextButton(PremiumIconButton):
    """Compact action button with an original vector icon and real button semantics."""
    def __init__(self,kind,text,parent=None,*,accented=False,trailing=False):
        QPushButton.__init__(self,parent)
        self.kind=IconKind(kind);self.hover_progress=0.0;self.press_progress=0.0
        self.accented=bool(accented);self.trailing=bool(trailing)
        self.setText(text);self.setAccessibleName(text);self.setCursor(Qt.CursorShape.PointingHandCursor)
        # These buttons paint their labels manually, so native QPushButton
        # size hints do not account for the icon + caption.  Reserve actual
        # label width instead of truncating 'Компоненты' at narrow DPI scales.
        caption_font=QFont('Segoe UI');caption_font.setPixelSize(12)
        caption_font.setWeight(QFont.Weight.DemiBold)
        caption_width=max((QFontMetrics(caption_font).horizontalAdvance(line)
                           for line in text.split('\n')), default=0)
        self.setMinimumSize(max(112, caption_width+49) if '\n' not in text else 84,36)
        self.setStyleSheet('border:none;background:transparent;padding:0')
        self.icon_view=AnimatedIcon(self.kind,22,self);self.icon_view.set_state(IconState.ENABLED,animate=False)
        self._hover_anim=QVariantAnimation(self);self._press_anim=QVariantAnimation(self)
        for animation,duration in ((self._hover_anim,MotionTokens.HOVER_MS),(self._press_anim,90)):
            animation.setDuration(duration);animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(lambda value:self._set_hover(value))
        self._press_anim.valueChanged.connect(lambda value:self._set_press(value))

    def resizeEvent(self,event):
        x=self.width()-32 if self.trailing else 10
        self.icon_view.setGeometry(x,(self.height()-22)//2,22,22);super().resizeEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(1.5+self.press_progress,1.5+self.press_progress,
                    self.width()-3-2*self.press_progress,self.height()-3-2*self.press_progress)
        base=QColor('#1A3926' if self.accented else '#15291F')
        hover=QColor('#29533A' if self.accented else '#254331')
        for channel in ('red','green','blue'):
            setter=getattr(base,'set'+channel.capitalize())
            setter(round(getattr(base,channel)()+(getattr(hover,channel)()-getattr(base,channel)())*self.hover_progress))
        border=QColor(ACCENT if self.accented else '#496D51')
        border.setAlpha(round((175 if self.accented else 120)+70*self.hover_progress))
        painter.setPen(QPen(border,1));painter.setBrush(base);painter.drawRoundedRect(rect,11,11)
        painter.setPen(QColor(THEME_TEXT))
        font=QFont('Segoe UI');font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold);painter.setFont(font)
        # Use a consistent, measurable text region; never paint beyond the
        # right edge of the button or under its icon.
        text_left=13 if self.trailing else 39
        text_right=self.width()-39 if self.trailing else self.width()-9
        available=max(1,text_right-text_left)
        text=QFontMetrics(font).elidedText(self.text(),Qt.TextElideMode.ElideRight,available)
        text_rect=QRectF(text_left,0,available,self.height())
        align=Qt.AlignmentFlag.AlignVCenter|(Qt.AlignmentFlag.AlignCenter if self.trailing else Qt.AlignmentFlag.AlignLeft)
        painter.drawText(text_rect,align,text)
        painter.end()


class ComponentTabButton(IconTextButton):
    """Icon-led two-line component selector used by the compact details view."""
    def __init__(self,kind,title,status='Готов',parent=None):
        self._title=str(title);self._status=str(status)
        super().__init__(kind,self._title+'\n'+self._status,parent)
        self.icon_view.set_state(IconState.DISABLED,animate=False)
        self.setCheckable(True);self.setMinimumSize(84,52);self.setMaximumHeight(52)
        self.setToolTip('')

    def set_icon_state(self,state,animate=True):
        self.icon_view.set_state(state,animate=animate)

    def setText(self,text):
        parts=str(text).split('\n',1)
        self._title=parts[0];self._status=parts[1] if len(parts)>1 else getattr(self,'_status','')
        QPushButton.setText(self,self._title+'\n'+self._status)
        # Both strings are visible on the tab. A hover tooltip covered the
        # neighbouring WARP label and looked like overlapping UI elements.
        self.setToolTip('')
        self.setAccessibleName(self._title+' · '+self._status)
        self.update()

    def resizeEvent(self,event):
        self.icon_view.setGeometry(9,13,19,19);QPushButton.resizeEvent(self,event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hover_progress or self.isChecked():
            shade=QColor('#203E2D');shade.setAlpha(round(55+45*self.hover_progress))
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(shade)
            painter.drawRoundedRect(QRectF(1,2,self.width()-2,self.height()-5),9,9)
        if self.isChecked():
            painter.setPen(QPen(ACCENT,2));painter.drawLine(5,self.height()-2,self.width()-5,self.height()-2)
        title=QColor(TEXT if self.isChecked() else '#DCE9DC')
        painter.setPen(title);font=painter.font();font.setWeight(QFont.Weight.DemiBold);painter.setFont(font)
        title_metrics=QFontMetrics(font)
        title_text=title_metrics.elidedText(self._title,Qt.TextElideMode.ElideRight,max(1,self.width()-39))
        painter.drawText(QRectF(35,6,self.width()-39,20),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,title_text)
        painter.setPen(QColor(ACCENT if self.isChecked() else '#AABCA9'))
        small=painter.font();small.setPixelSize(10);small.setWeight(QFont.Weight.Medium);painter.setFont(small)
        status_metrics=QFontMetrics(small)
        status_text=status_metrics.elidedText(self._status,Qt.TextElideMode.ElideRight,max(1,self.width()-39))
        painter.drawText(QRectF(35,27,self.width()-39,18),Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,status_text)
        painter.end()
