"""Central motion tokens, confirmed toggles and cancellable tray transitions."""
from __future__ import annotations

from enum import Enum
import os
from math import cos, pi, sin

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from ui_theme import ACCENT as THEME_ACCENT, ACCENT_HOVER as THEME_ACCENT_BRIGHT


class MotionTokens:
    HOVER_MS = 135
    TOGGLE_MS = 180
    CONFIRM_MS = 200
    HIDE_MS = 200
    RESTORE_MS = 240


class MotionState(str, Enum):
    DISABLED = 'disabled'
    STARTING = 'starting'
    ENABLED = 'enabled'
    STOPPING = 'stopping'
    LOADING = 'loading'
    SUCCESS = 'success'
    ERROR = 'error'


def reduced_motion_enabled():
    return os.environ.get('NETPILOT_REDUCED_MOTION','').strip().casefold() in {'1','true','yes','on'}


class AnimatedToggle(QPushButton):
    """A 47×26 switch whose green state means backend-confirmed success."""
    def __init__(self, checked=False, parent=None, *, reduced_motion=None):
        super().__init__(parent)
        self.setCheckable(True);self.setFixedSize(47,26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Переключатель')
        self._reduced_motion=reduced_motion_enabled() if reduced_motion is None else bool(reduced_motion)
        self.confirmed=bool(checked);self._requested=bool(checked)
        self.motion_state=MotionState.ENABLED if checked else MotionState.DISABLED
        self.handle_progress=1.0 if checked else 0.0
        self.pending_phase=0.0;self.hover_progress=0.0;self.press_progress=0.0
        QPushButton.setChecked(self,bool(checked))
        self._animation=QVariantAnimation(self);self._animation.setDuration(MotionTokens.TOGGLE_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._progress_changed)
        self._busy=QVariantAnimation(self);self._busy.setDuration(820)
        self._busy.setStartValue(0.0);self._busy.setEndValue(1.0)
        self._busy.setEasingCurve(QEasingCurve.Type.Linear)
        self._busy.valueChanged.connect(self._busy_changed);self._busy.finished.connect(self._busy_finished)
        self._hover=QVariantAnimation(self);self._hover.setDuration(MotionTokens.HOVER_MS)
        self._hover.setEasingCurve(QEasingCurve.Type.OutCubic);self._hover.valueChanged.connect(self._hover_changed)
        self._press=QVariantAnimation(self);self._press.setDuration(90)
        self._press.setEasingCurve(QEasingCurve.Type.OutCubic);self._press.valueChanged.connect(self._press_changed)

    @property
    def pending(self):
        return self._requested != self.confirmed or self.motion_state in {
            MotionState.STARTING,MotionState.STOPPING,MotionState.LOADING}

    def _progress_changed(self,value):
        self.handle_progress=float(value);self.update()

    def _busy_changed(self,value):self.pending_phase=float(value);self.update()
    def _hover_changed(self,value):self.hover_progress=float(value);self.update()
    def _press_changed(self,value):self.press_progress=float(value);self.update()

    def _busy_finished(self):
        if self.pending and self.isVisible() and not self._reduced_motion:self._start_busy()

    def _start_busy(self):
        self._busy.stop();self._busy.setStartValue(0.0);self._busy.setEndValue(1.0);self._busy.start()

    def _sync_busy(self):
        if self.pending and self.isVisible() and not self._reduced_motion:
            if self._busy.state()!=QAbstractAnimation.State.Running:self._start_busy()
        else:
            self._busy.stop();self.pending_phase=0.0;self.update()

    def _animate(self,animation,current,target):
        animation.stop();animation.setStartValue(float(current));animation.setEndValue(float(target));animation.start()

    def _move_handle(self,target):
        self._animation.stop()
        if self._reduced_motion or not self.isVisible():
            self.handle_progress=float(target);self.update();return
        self._animation.setStartValue(self.handle_progress);self._animation.setEndValue(float(target))
        self._animation.start()

    def nextCheckState(self):
        requested=not QPushButton.isChecked(self)
        self._requested=requested
        self.motion_state=MotionState.STARTING if requested else MotionState.STOPPING
        QPushButton.setChecked(self,requested)
        self._move_handle(1.0 if requested else 0.0)
        self._sync_busy()

    def setChecked(self,checked):
        self.set_confirmed(bool(checked),MotionState.ENABLED if checked else MotionState.DISABLED)

    def set_confirmed(self,checked,state=None):
        checked=bool(checked)
        self.confirmed=checked;self._requested=checked
        self.motion_state=MotionState(state or (MotionState.ENABLED if checked else MotionState.DISABLED))
        QPushButton.setChecked(self,checked)
        self._move_handle(1.0 if checked else 0.0)
        self._sync_busy()

    def is_animating(self):
        return (self._animation.state()==QAbstractAnimation.State.Running or
                self._busy.state()==QAbstractAnimation.State.Running or
                self._hover.state()==QAbstractAnimation.State.Running or
                self._press.state()==QAbstractAnimation.State.Running)

    def is_busy_animating(self):
        return self._busy.state()==QAbstractAnimation.State.Running

    def hideEvent(self,event):
        self._animation.stop();self._busy.stop();self._hover.stop();self._press.stop();super().hideEvent(event)

    def showEvent(self,event):
        super().showEvent(event);self._sync_busy()

    def enterEvent(self,event):
        self._animate(self._hover,self.hover_progress,1);super().enterEvent(event)

    def leaveEvent(self,event):
        self._animate(self._hover,self.hover_progress,0);super().leaveEvent(event)

    def mousePressEvent(self,event):
        self._animate(self._press,self.press_progress,1);super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        self._animate(self._press,self.press_progress,0);super().mouseReleaseEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track_rect=QRectF(1.5+self.press_progress*.5,1.5+self.press_progress*.5,
                          44- self.press_progress,23- self.press_progress)
        if self.hover_progress:
            glow=QColor('#99F5AF');glow.setAlpha(round(42*self.hover_progress))
            painter.setPen(QPen(glow,3));painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track_rect,12,12)
        if self.pending:
            painter.setPen(QPen(QColor('#6ABD80'),1.0));track=QColor('#24442D')
        else:
            painter.setPen(QPen(QColor('#7DE7A1') if self.confirmed else QColor('#5A715D'),.8))
            if self.confirmed:
                track=QLinearGradient(0,0,47,26);track.setColorAt(0,QColor('#3E9D62'));track.setColorAt(1,QColor('#99F5AF'))
            else:track=QColor('#344939')
        painter.setBrush(track);painter.drawRoundedRect(track_rect,12,12)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.confirmed and not self.pending:
            glow=QColor('#FFFFFF');glow.setAlpha(45);painter.setBrush(glow)
            x=4+21*max(0.0,min(1.0,self.handle_progress));painter.drawEllipse(QRectF(x-2,2,23,23))
        painter.setBrush(QColor('#F2F6F0' if self.isEnabled() else '#A0AAA6'))
        x=4+21*max(0.0,min(1.0,self.handle_progress))
        painter.drawEllipse(round(x),4,19,19);painter.end()
        if self.pending:
            painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            center=QPointF(x+9.5,13.5)
            for index in range(7):
                angle=2*pi*(index/7+self.pending_phase)
                alpha=55+round(180*(index+1)/7)
                dot=QColor('#99F5AF');dot.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(dot)
                painter.drawEllipse(QPointF(center.x()+6*cos(angle),center.y()+6*sin(angle)),1.05,1.05)
            painter.end()


class _WindowSnapshot(QWidget):
    """Lightweight animated proxy for the real window; no layout recalculation."""
    def __init__(self,pixmap,parent=None):
        super().__init__(None)
        self._snapshot=pixmap
        self._alpha=1.0
        self._paint_rect=QRect()
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground,True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_render_state(self,geometry,alpha):
        # Animating pixels inside one constant compositor surface avoids
        # resizing a native transparent window on every animation frame.
        rect=QRect(geometry)
        self._paint_rect=rect.translated(-self.geometry().topLeft())
        self._alpha=max(0.0,min(1.0,float(alpha)))
        self.update()

    def set_alpha(self,value):
        self._alpha=max(0.0,min(1.0,float(value)))
        self.update()

    def paintEvent(self,event):
        if self._snapshot.isNull():return
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._alpha)
        painter.drawPixmap(self._paint_rect if self._paint_rect.isValid() else self.rect(),self._snapshot)
        painter.end()


class WindowTransition:
    """macOS-inspired window-to-tray zoom with a low-cost snapshot animation.

    Animating a live frameless window's geometry and opacity together can make
    DWM/Qt repaint all child widgets on every frame. Capture once and animate
    a lightweight snapshot instead. Real connections and Qt widgets stay intact.
    The exact macOS Genie mesh distortion is not supported by Qt Widgets.
    """
    def __init__(self,window,*,reduced_motion=None):
        self.window=window
        self.reduced_motion=reduced_motion_enabled() if reduced_motion is None else bool(reduced_motion)
        self.final_geometry=QRect(window.geometry())
        self._mode=None
        self._start_rect=QRect()
        self._end_rect=QRect()
        self._overlay=None
        self._snapshot=None
        self._tray_point=None
        self._fallback=False
        self._animation=QVariantAnimation(window)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._advance)
        self._animation.finished.connect(self._finished)

    def is_animating(self):
        return self._animation.state()==QAbstractAnimation.State.Running

    def _destroy_overlay(self):
        overlay=self._overlay
        self._overlay=None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def _cancel(self):
        self._animation.stop()
        self._destroy_overlay()
        self._mode=None
        self.window.setWindowOpacity(1.0)
        self._fallback=False

    def _available(self,geometry):
        screen=QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(geometry)

    def _clamp(self,geometry):
        result=QRect(geometry)
        area=self._available(result)
        width=min(result.width(),area.width())
        height=min(result.height(),area.height())
        result.setSize(result.size().boundedTo(area.size()))
        result.moveLeft(min(max(result.left(),area.left()),area.right()-width+1))
        result.moveTop(min(max(result.top(),area.top()),area.bottom()-height+1))
        return result

    def _mini_rect(self,full,point):
        width=max(24,round(full.width()*.14))
        height=max(22,round(full.height()*.14))
        target=QPoint(point) if point is not None else full.bottomRight()
        return QRect(target.x()-width//2,target.y()-height//2,width,height)

    def _make_overlay(self,rect):
        if self._snapshot is None or self._snapshot.isNull():return False
        try:
            self._overlay=_WindowSnapshot(self._snapshot)
            canvas=QRect(self._start_rect).united(self._end_rect)
            if canvas.width()>1200 or canvas.height()>1100:
                # Large cross-monitor movements should not allocate a huge
                # permanent transparent compositor surface.
                canvas=QRect(rect)
                self._overlay_dynamic_geometry=True
            else:
                self._overlay_dynamic_geometry=False
            self._overlay.setGeometry(canvas)
            self._overlay.set_render_state(rect,1.0)
            self._overlay.show()
            self._overlay.raise_()
            return True
        except RuntimeError:
            self._destroy_overlay()
            return False

    def hide_to_tray(self,target=None):
        if not self.window.isVisible():return
        self._cancel()
        self.final_geometry=self._clamp(self.window.geometry())
        if self.reduced_motion:
            self.window.hide()
            return
        self._mode='hide'
        self._tray_point=(target.center() if isinstance(target,QRect) and target.isValid()
                          else QPoint(target) if isinstance(target,QPoint) else
                          self.final_geometry.bottomRight()+QPoint(-30,10))
        self._start_rect=QRect(self.final_geometry)
        self._end_rect=self._mini_rect(self.final_geometry,self._tray_point)
        self._snapshot=self.window.grab()
        self._fallback=not self._make_overlay(self._start_rect)
        if not self._fallback:
            # One opacity update instead of repainting the entire live UI 60x.
            self.window.setWindowOpacity(0.0)
        self._animation.setDuration(MotionTokens.HIDE_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def restore(self,geometry=None):
        self._cancel()
        requested=QRect(geometry) if geometry is not None else QRect(self.final_geometry)
        self.final_geometry=self._clamp(requested)
        self.window.setGeometry(self.final_geometry)
        if self.reduced_motion:
            self.window.show()
            return
        self._mode='restore'
        self._end_rect=QRect(self.final_geometry)
        self._start_rect=self._mini_rect(self.final_geometry,self._tray_point)
        self._fallback=not self._make_overlay(self._start_rect)
        if self._fallback:
            self.window.move(self.final_geometry.topLeft()+QPoint(0,7))
            self.window.setWindowOpacity(.2)
        else:
            self.window.setWindowOpacity(0.0)
        self.window.show()
        self._animation.setDuration(MotionTokens.RESTORE_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.start()

    def _advance(self,value):
        progress=max(0.,min(1.,float(value)))
        if self._fallback:
            # Fallback on platforms unable to show transparent snapshot windows.
            if self._mode=='hide':
                self.window.setWindowOpacity(1.0-progress)
            elif self._mode=='restore':
                self.window.move(self.final_geometry.left(),self.final_geometry.top()+round((1-progress)*7))
                self.window.setWindowOpacity(.2+.8*progress)
            return
        if self._overlay is None:return
        a,b=self._start_rect,self._end_rect
        new_rect=QRect(round(a.x()+(b.x()-a.x())*progress),
                       round(a.y()+(b.y()-a.y())*progress),
                       max(1,round(a.width()+(b.width()-a.width())*progress)),
                       max(1,round(a.height()+(b.height()-a.height())*progress)))
        if getattr(self,'_overlay_dynamic_geometry',False):
            self._overlay.setGeometry(new_rect)
        self._overlay.set_render_state(
            new_rect,1-.30*progress if self._mode=='hide' else .70+.30*progress)

    def _finished(self):
        mode=self._mode
        self._mode=None
        self.window.setGeometry(self.final_geometry)
        if mode=='hide':
            self.window.hide()
            self.window.setWindowOpacity(1.0)
        elif mode=='restore':
            self.window.setWindowOpacity(1.0)
            self.window.show()
        self._destroy_overlay()
        self._fallback=False

    def finish_immediately(self):
        if self.is_animating():
            self._animation.setCurrentTime(self._animation.duration())
