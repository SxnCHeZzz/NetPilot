"""Central motion tokens, confirmed toggles and cancellable tray transitions."""
from __future__ import annotations

from enum import Enum
import os

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QPoint, QRect, Qt,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QPushButton


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
        QPushButton.setChecked(self,bool(checked))
        self._animation=QVariantAnimation(self);self._animation.setDuration(MotionTokens.TOGGLE_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._progress_changed)

    @property
    def pending(self):
        return self._requested != self.confirmed or self.motion_state in {
            MotionState.STARTING,MotionState.STOPPING,MotionState.LOADING}

    def _progress_changed(self,value):
        self.handle_progress=float(value);self.update()

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

    def setChecked(self,checked):
        self.set_confirmed(bool(checked),MotionState.ENABLED if checked else MotionState.DISABLED)

    def set_confirmed(self,checked,state=None):
        checked=bool(checked)
        self.confirmed=checked;self._requested=checked
        self.motion_state=MotionState(state or (MotionState.ENABLED if checked else MotionState.DISABLED))
        QPushButton.setChecked(self,checked)
        self._move_handle(1.0 if checked else 0.0)

    def is_animating(self):
        return self._animation.state()==QAbstractAnimation.State.Running

    def hideEvent(self,event):
        self._animation.stop();super().hideEvent(event)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.pending:
            painter.setPen(QPen(QColor('#F2BD62'),1.2));track=QColor('#35413D')
        else:
            painter.setPen(Qt.PenStyle.NoPen);track=QColor('#66E3A4' if self.confirmed else '#3E4845')
        painter.setBrush(track);painter.drawRoundedRect(1,1,45,24,12,12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#F7FAF8' if self.isEnabled() else '#A0AAA6'))
        x=4+21*max(0.0,min(1.0,self.handle_progress))
        painter.drawEllipse(round(x),4,19,19);painter.end()


class WindowTransition:
    """Cancellable opacity/position transition that never touches app state."""
    def __init__(self,window,*,reduced_motion=None):
        self.window=window
        self.reduced_motion=reduced_motion_enabled() if reduced_motion is None else bool(reduced_motion)
        self.final_geometry=QRect(window.geometry())
        self._mode=None;self._start=QPoint();self._end=QPoint()
        self._animation=QVariantAnimation(window)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._advance)
        self._animation.finished.connect(self._finished)

    def is_animating(self):
        return self._animation.state()==QAbstractAnimation.State.Running

    def _cancel(self):
        self._animation.stop();self._mode=None

    def _available(self,geometry):
        screen=QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(geometry)

    def _clamp(self,geometry):
        result=QRect(geometry);area=self._available(result)
        width=min(result.width(),area.width());height=min(result.height(),area.height())
        result.setSize(result.size().boundedTo(area.size()))
        result.moveLeft(min(max(result.left(),area.left()),area.right()-width+1))
        result.moveTop(min(max(result.top(),area.top()),area.bottom()-height+1))
        return result

    def hide_to_tray(self,target=None):
        self._cancel();self.final_geometry=self._clamp(self.window.geometry())
        if self.reduced_motion:
            self.window.setWindowOpacity(1.0);self.window.setGeometry(self.final_geometry);self.window.hide();return
        self._mode='hide';self._start=self.final_geometry.topLeft()
        target_point=(target.center() if isinstance(target,QRect) and target.isValid()
                      else target if isinstance(target,QPoint) else self._start+QPoint(0,8))
        dx=max(-8,min(8,target_point.x()-self._start.x()))
        dy=max(-8,min(8,target_point.y()-self._start.y()))
        self._end=self._start+QPoint(dx,dy)
        self._animation.setDuration(MotionTokens.HIDE_MS)
        self._animation.setStartValue(0.0);self._animation.setEndValue(1.0);self._animation.start()

    def restore(self,geometry=None):
        self._cancel();requested=QRect(geometry) if geometry is not None else QRect(self.final_geometry)
        self.final_geometry=self._clamp(requested)
        if self.reduced_motion:
            self.window.setGeometry(self.final_geometry);self.window.setWindowOpacity(1.0)
            self.window.show();return
        self._mode='restore';self._end=self.final_geometry.topLeft();self._start=self._end+QPoint(0,8)
        self.window.setGeometry(QRect(self._start,self.final_geometry.size()))
        self.window.setWindowOpacity(.2);self.window.show();self.window.raise_()
        self._animation.setDuration(MotionTokens.RESTORE_MS)
        self._animation.setStartValue(0.0);self._animation.setEndValue(1.0);self._animation.start()

    def _advance(self,value):
        progress=float(value)
        point=QPoint(round(self._start.x()+(self._end.x()-self._start.x())*progress),
                     round(self._start.y()+(self._end.y()-self._start.y())*progress))
        self.window.move(point)
        self.window.setWindowOpacity((1.0-progress) if self._mode=='hide' else (.2+.8*progress))

    def _finished(self):
        mode=self._mode;self._mode=None
        self.window.setGeometry(self.final_geometry);self.window.setWindowOpacity(1.0)
        if mode=='hide':self.window.hide()
        elif mode=='restore':self.window.show()

    def finish_immediately(self):
        if self.is_animating():self._animation.setCurrentTime(self._animation.duration())
