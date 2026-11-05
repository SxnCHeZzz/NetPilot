"""NetPilot 0.5 pre-release: a compact, opt-in controller of external network tools.

The GUI never executes untrusted commands from form fields. Networking and
privileged actions run off the UI thread. Third-party programs remain separate.
"""
from __future__ import annotations
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# The official Flowseal CLI backend runs as our own child, without the upstream
# Windows tray UI. Dispatch before loading Qt so child startup stays lightweight.
if '--tg-headless' in sys.argv:
    from telegram_headless import worker_main
    try:
        worker_main(sys.argv[sys.argv.index('--tg-headless') + 1:])
    except Exception as exc:
        from managed import ROOT
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / 'telegram-worker-error.log').write_text(
            f'{type(exc).__name__}: {str(exc)[:300]}\n', encoding='utf-8')
        raise
    sys.exit(0)

if '--privileged-request' in sys.argv:
    from privileged import privileged_cli_main
    try:
        request_id = sys.argv[sys.argv.index('--privileged-request') + 1]
    except IndexError:
        raise SystemExit(2)
    raise SystemExit(privileged_cli_main(request_id))

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPointF, QEventLoop, QEvent
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QBrush, QCursor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget, QFrame, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QProgressBar, QScrollArea, QComboBox,
    QCheckBox, QSystemTrayIcon, QMenu, QFileDialog, QMessageBox as NativeMessageBox, QLineEdit, QPlainTextEdit,
    QSizePolicy, QStackedWidget, QToolButton, QGraphicsDropShadowEffect,
)
import managed as m
import benchmark as b
from catalog import load_catalog
import telegram_headless as tg
from tray_position import Rect, find_overflow_rect, physical_to_qt, centered
from privileged import (PrivilegedRequest, elevate_request, read_response,
                        snapshot_digest, write_request)
from windows_zapret import WindowsZapretAdapter
from zapret_control import StopMode, load_backup
from zapret_inventory import ZapretSnapshot
from ui_icons import AnimatedIcon, IconKind, IconState
from ui_motion import AnimatedToggle, WindowTransition

VERSION = '0.5.4-rc6'
ACCENT = '#66E3A4'
ACCENT_BRIGHT = '#8AF0BC'
WARNING = '#F2BD62'
ERROR = '#FF7285'
CSS = '''
*{font-family:'Segoe UI';font-size:12px;color:#F2F6F4}
QMainWindow{background:transparent}
QDialog{background:#0A0D0F}
QWidget#root{background:#0A0D0F;border:1px solid #263039;border-radius:17px}
QFrame#surface{background:#13181C;border:1px solid #263039;border-radius:16px}
QFrame#row{background:#171C21;border:1px solid #29323A;border-radius:12px}
QFrame#row:hover{border-color:#3D4B52;background:#1B2227}
QFrame#detailPanel{background:#11161A;border:1px solid #29343B;border-radius:14px}
QFrame#separator{background:#2A343A;min-width:1px;max-width:1px}
QLabel#brand{font-size:19px;font-weight:800;letter-spacing:0.3px}
QLabel#heading{font-size:15px;font-weight:750}
QLabel#heroTitle{font-size:24px;font-weight:750}
QLabel#heroSubtitle{font-size:13px;color:#8F9A96}
QLabel#componentName{font-size:14px;font-weight:700}
QLabel#detailName{font-size:17px;font-weight:750}
QLabel#factValue{font-size:12px;color:#F2F6F4;font-weight:650}
QLabel#muted{font-size:11px;color:#8F9A96}
QLabel#small{font-size:10px;color:#9EAAA5}
QLabel#eyebrow{font-size:10px;color:#75CFA4;font-weight:750}
QLabel#status{color:#A9B6B0;font-size:11px}
QLabel#success{color:#8AF0BC;font-size:10px;font-weight:700;background:#122D22;border:1px solid #28543F;border-radius:7px;padding:3px 7px}
QLabel#warn{color:#F2C97F;font-size:10px;font-weight:700;background:#302719;border:1px solid #554429;border-radius:7px;padding:3px 7px}
QLabel#error{color:#FF8795;font-size:11px}
QPushButton{border:1px solid #303A42;background:#20272C;border-radius:10px;padding:9px 13px;font-weight:600}
QPushButton:hover{background:#283138;border-color:#4D625A}
QPushButton:pressed{background:#313D42}
QPushButton:disabled{color:#69736F;background:#171C20;border-color:#242C31}
QPushButton#primary{background:#66E3A4;border:1px solid #8AF0BC;color:#07110C;font-weight:750}
QPushButton#primary:hover{background:#7CEAB2}
QPushButton#link{background:transparent;border:none;color:#66E3A4;font-size:12px;font-weight:700;padding:3px 1px;text-align:left}
QPushButton#link:hover{color:#8AF0BC}
QPushButton#details{background:transparent;border:none;color:#66E3A4;font-size:15px;font-weight:700;padding:8px}
QPushButton#details:hover{color:#8AF0BC}
QPushButton#componentTab{background:transparent;border:none;border-bottom:2px solid transparent;border-radius:0;padding:7px 4px;color:#8F9A96;font-size:11px;font-weight:650}
QPushButton#componentTab:checked{color:#F2F6F4;border-bottom:2px solid #66E3A4}
QPushButton#componentTab:hover{color:#DCE5E1;background:transparent}
QPushButton#icon{background:#1D2429;border:1px solid #2D373E;min-width:27px;min-height:26px;padding:5px;font-size:18px;font-weight:800}
QFrame#dialogCard{background:#151B1F;border:1px solid #385047;border-radius:17px}
QLabel#dialogTitle{font-size:15px;font-weight:750}
QPushButton#dialogSecondary{background:#20272C;border:1px solid #354139}
QPushButton#dialogSecondary:hover{background:#29342F}
QWidget#dialogShade{background:rgba(5,5,9,188);border-radius:17px}
QPushButton#quiet{background:transparent;border:1px solid #293239;color:#B3BDB9}
QPushButton#quiet:hover{background:#1B2226}
QPushButton#danger{color:#FF9AA6;border:1px solid #54323B}
QProgressBar{background:#293238;border:none;border-radius:4px;min-height:7px;max-height:7px}
QProgressBar::chunk{background:#66E3A4;border-radius:4px}
QComboBox,QLineEdit{background:#1C2328;border:1px solid #344149;border-radius:8px;padding:7px;min-height:21px}
QComboBox QAbstractItemView{background:#151B1F;selection-background-color:#286348;outline:none}
QScrollArea,QScrollArea>QWidget>QWidget{border:none;background:transparent}
QScrollBar:vertical{background:transparent;width:6px;margin:3px}
QScrollBar::handle:vertical{background:#3D4A50;border-radius:3px;min-height:30px}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}
QToolTip{background:#1C2328;color:white;border:1px solid #3B4A45;padding:6px}
'''


class InlineModal(QWidget):
    """Same-window, blocking-by-choice panel: never creates a top-level dialog.

    A nested Qt event loop preserves existing synchronous confirm APIs while
    keeping rendering and worker signals alive. The shade consumes mouse input
    so the user cannot accidentally change controls behind a confirmation.
    """
    def __init__(self, owner, title='', message='', question=False, custom=None):
        super().__init__(owner.centralWidget())
        self.owner = owner
        self.value = False
        self.loop = QEventLoop(self)
        self.setObjectName('dialogShade')
        self.setGeometry(owner.centralWidget().rect())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addStretch(1)
        self.card = QFrame(objectName='dialogCard')
        self.card.setMaximumWidth(372)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(17, 18, 17, 17)
        col.setSpacing(13)
        if custom is not None:
            self.card.setMaximumWidth(385)
            col.setContentsMargins(7, 7, 7, 7)
            custom.setParent(self.card)
            col.addWidget(custom)
            custom._active_modal = self
        else:
            heading = QLabel(title)
            heading.setObjectName('dialogTitle')
            heading.setWordWrap(True)
            col.addWidget(heading)
            body = QLabel(str(message))
            body.setObjectName('muted')
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            col.addWidget(body)
            actions = QHBoxLayout()
            actions.setSpacing(9)
            if question:
                cancel = QPushButton('Отмена')
                cancel.setObjectName('dialogSecondary')
                cancel.clicked.connect(lambda: self.finish(False))
                actions.addWidget(cancel)
            else:
                actions.addStretch()
            go = QPushButton('Подтвердить' if question else 'Понятно')
            go.setObjectName('primary')
            go.clicked.connect(lambda: self.finish(True))
            actions.addWidget(go)
            col.addLayout(actions)
        layout.addWidget(self.card)
        layout.addStretch(1)
        # The root can be resized by DPI/layout updates while the sheet is up.
        owner.centralWidget().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.owner.centralWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finish(False)
        else:
            super().keyPressEvent(event)

    def finish(self, value):
        self.value = bool(value)
        if self.loop.isRunning():
            self.loop.quit()

    def exec(self):
        self.raise_()
        self.show()
        self.activateWindow()
        self.setFocus()
        self.loop.exec()
        self.owner.centralWidget().removeEventFilter(self)
        self.hide()
        self.deleteLater()
        return self.value


class QMessageBox:
    """Compatibility facade for existing call sites, rendered inside NetPilot."""
    StandardButton = NativeMessageBox.StandardButton

    @staticmethod
    def _owner(parent):
        current = parent
        while current is not None:
            if hasattr(current, 'inline_notice'):
                return current
            current = current.parentWidget() if hasattr(current, 'parentWidget') else None
        return None

    @staticmethod
    def warning(parent, title, message):
        owner = QMessageBox._owner(parent)
        if owner:
            owner.inline_notice(title, message)
        return NativeMessageBox.StandardButton.Ok

    @staticmethod
    def information(parent, title, message):
        return QMessageBox.warning(parent, title, message)

    @staticmethod
    def question(parent, title, message):
        owner = QMessageBox._owner(parent)
        if owner:
            return (NativeMessageBox.StandardButton.Yes if owner.inline_confirm(title, message)
                    else NativeMessageBox.StandardButton.No)
        return NativeMessageBox.StandardButton.No


class StopZapretPanel(QWidget):
    """Compact same-window chooser for the two intentionally distinct stops."""
    def __init__(self, own_count, external_count, parent=None):
        super().__init__(parent)
        self.mode = None
        layout=QVBoxLayout(self);layout.setContentsMargins(10,10,10,10);layout.setSpacing(9)
        title=QLabel('Остановить весь Zapret');title.setObjectName('dialogTitle');layout.addWidget(title)
        detail=QLabel(f'Свой: {own_count} · внешний: {external_count}. Файлы не удаляются.')
        detail.setObjectName('muted');detail.setWordWrap(True);layout.addWidget(detail)
        temporary=QPushButton('Остановить сейчас');temporary.setObjectName('primary')
        temporary.clicked.connect(lambda:self.choose(StopMode.TEMPORARY));layout.addWidget(temporary)
        persistent=QPushButton('Остановить и отключить автозапуск');persistent.setObjectName('danger')
        persistent.clicked.connect(lambda:self.choose(StopMode.DISABLE_RESTART));layout.addWidget(persistent)
        cancel=QPushButton('Отмена');cancel.setObjectName('quiet')
        cancel.clicked.connect(lambda:self._active_modal.finish(False));layout.addWidget(cancel)

    def choose(self, mode):
        self.mode=mode
        self._active_modal.finish(True)


class HeaderAction(QPushButton):
    """Consistent vector navigation icons, independent of platform glyph fonts."""
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip({'back':'Назад', 'settings':'Настройки', 'minimize':'Свернуть в трей'}[kind])
        self.setAccessibleName(self.toolTip())
        self.icon_view=AnimatedIcon({'back':IconKind.BACK,'settings':IconKind.GEAR,
                                     'minimize':IconKind.MINIMIZE}[kind],24,self)
        self.icon_view.setGeometry(6,6,24,24)
        self.icon_view.set_state(IconState.ENABLED,animate=False)

    def set_kind(self, kind):
        self.kind = kind
        self.setToolTip({'back':'Назад', 'settings':'Настройки', 'minimize':'Свернуть в трей'}[kind])
        self.setAccessibleName(self.toolTip())
        self.icon_view.kind={'back':IconKind.BACK,'settings':IconKind.GEAR,
                             'minimize':IconKind.MINIMIZE}[kind]
        self.icon_view.update();self.update()

    def enterEvent(self,event):
        self.icon_view.set_interaction(True,False);super().enterEvent(event)

    def leaveEvent(self,event):
        self.icon_view.set_interaction(False,False);super().leaveEvent(event)

    def mousePressEvent(self,event):
        self.icon_view.set_interaction(True,True);super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        self.icon_view.set_interaction(self.underMouse(),False);super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor('#557064') if self.underMouse() else QColor('#303A40'), 1))
        p.setBrush(QColor('#24312C') if self.underMouse() else QColor('#1C2328'))
        p.drawRoundedRect(1, 1, self.width()-2, self.height()-2, 11, 11)
        p.end()


class StatusOrb(QWidget):
    """Large, quiet health signal from the approved overview design."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_count = 0
        self.busy = False
        self.error = False
        self.setFixedSize(64, 64)

    def set_state(self, active_count, busy=False, error=False):
        self.active_count = int(active_count)
        self.busy = bool(busy)
        self.error = bool(error)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ERROR if self.error else WARNING if self.busy else ACCENT if self.active_count else '#65716C')
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(5, 5, 54, 54)
        glow = QColor(color); glow.setAlpha(42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(14, 14, 36, 36)
        painter.setBrush(color)
        painter.drawEllipse(20, 20, 24, 24)
        painter.end()


class SummaryItem(QWidget):
    def __init__(self, name, kind, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.dot = AnimatedIcon({'zapret':IconKind.LOCK,'telegram':IconKind.NETWORK,
                                 'warp':IconKind.CLOUD}[kind],20)
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout(); text.setSpacing(1); row.addLayout(text)
        label = QLabel(name); label.setObjectName('componentName'); text.addWidget(label)
        self.status = QLabel('Готов'); self.status.setObjectName('muted'); text.addWidget(self.status)

    def set_state(self, active, label):
        self.dot.set_active(active)
        self.status.setText(label)

    def set_icon_state(self,state):
        self.dot.set_state(state)


def status_icon(active_count=0, busy=False, error=False):
    """Render a tray-safe NetPilot mark whose color mirrors live state."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    state = ERROR if error else WARNING if busy else ACCENT if active_count else '#73807B'
    p.setPen(QPen(QColor(state), 5))
    p.setBrush(QColor('#11171A'))
    p.drawRoundedRect(8, 8, 48, 48, 15, 15)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor('#F5F8F6'), 5))
    p.drawLine(36, 13, 25, 33)
    p.drawLine(25, 33, 38, 33)
    p.drawLine(38, 33, 29, 49)
    p.setPen(Qt.PenStyle.NoPen)
    for index in range(3):
        p.setBrush(QColor(ACCENT if index < max(0, min(3, active_count)) else '#3B4642'))
        p.drawEllipse(17 + index * 13, 54, 6, 6)
    p.end()
    return QIcon(pm)


def app_icon():
    return status_icon()


class Toggle(AnimatedToggle):
    """Backwards-compatible name for the confirmed-state switch."""
    pass


class Bus(QObject):
    done = Signal(str, object, str)
    progress = Signal(int, str)


class SetupDialog(QFrame):
    """First-run component acquisition happens after the installer, with consent."""
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.setWindowTitle('NetPilot · Подготовка')
        self.setFixedWidth(278)
        box = QVBoxLayout(self); box.setContentsMargins(14, 14, 14, 14); box.setSpacing(10)
        title = QLabel('Подготовим NetPilot'); title.setObjectName('brand'); box.addWidget(title)
        text = QLabel('Загрузим Zapret и официальный Cloudflare WARP.\nTelegram Proxy уже готов к работе.')
        text.setObjectName('muted'); text.setWordWrap(True); box.addWidget(text)
        choose = QHBoxLayout(); box.addLayout(choose)
        self.path = QLineEdit(cfg['components_dir']); choose.addWidget(self.path, 1)
        browse = QPushButton('…'); browse.clicked.connect(self.browse); choose.addWidget(browse)
        self.z = QCheckBox('Zapret · Discord / YouTube'); self.z.setChecked(True); box.addWidget(self.z)
        self.w = QCheckBox('Cloudflare WARP · официальный клиент')
        self.w.setChecked(m.warp_cli() is None)
        box.addWidget(self.w)
        hint = QLabel('WARP устанавливается через winget в системный каталог Cloudflare, независимо от выбранной папки.')
        hint.setWordWrap(True); hint.setObjectName('small'); box.addWidget(hint)
        note = QLabel('Windows может запросить разрешение UAC для установки WARP. NetPilot не подтверждает такие запросы автоматически.')
        note.setWordWrap(True); note.setObjectName('muted'); box.addWidget(note)
        actions = QHBoxLayout(); box.addLayout(actions)
        skip = QPushButton('Позже'); skip.clicked.connect(self.reject); actions.addWidget(skip)
        go = QPushButton('Загрузить'); go.setObjectName('primary'); go.clicked.connect(self.accept); actions.addWidget(go)

    def exec(self):
        return QDialog.DialogCode.Accepted if self.parent().inline_custom(self) else QDialog.DialogCode.Rejected

    def accept(self): self._active_modal.finish(True)
    def reject(self): self._active_modal.finish(False)

    def browse(self):
        directory = QFileDialog.getExistingDirectory(self, 'Папка компонентов', self.path.text())
        if directory: self.path.setText(directory)


class ExpertDialog(QFrame):
    """Advanced upstream controls stay hidden unless explicitly requested."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setFixedSize(278, 354)
        outer = QVBoxLayout(self); outer.setContentsMargins(10, 10, 10, 10)
        title = QLabel('Расширенные параметры Zapret'); title.setObjectName('heading'); title.setWordWrap(True); outer.addWidget(title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll, 1)
        content = QWidget(); scroll.setWidget(content)
        layout = QVBoxLayout(content); layout.setContentsMargins(2, 2, 4, 2); layout.setSpacing(8)
        folder = m.sub_dir(owner.cfg, 'zapret')
        try: options = m.zapret_options(folder)
        except Exception: options = {'game':'disabled','ipset':'unknown','update':False}
        layout.addWidget(QLabel('Game Filter'))
        self.game = QComboBox()
        for label, mode in [('Выключен','disabled'), ('TCP + UDP','all'), ('Только TCP','tcp'), ('Только UDP','udp')]:
            self.game.addItem(label, mode)
        self.game.setCurrentIndex(max(0, self.game.findData(options['game']))); layout.addWidget(self.game)
        btn = QPushButton('Применить Game Filter'); btn.clicked.connect(self.apply_game); layout.addWidget(btn)
        layout.addWidget(QLabel('IPSet Filter'))
        self.ipset = QComboBox()
        for label, mode in [('Загруженный список','loaded'), ('Выключен','none'), ('Весь трафик (риск)','any')]:
            self.ipset.addItem(label, mode)
        self.ipset.setCurrentIndex(max(0, self.ipset.findData(options['ipset']))); layout.addWidget(self.ipset)
        btn = QPushButton('Применить IPSet'); btn.clicked.connect(self.apply_ipset); layout.addWidget(btn)
        for title, callback in [
            ('Подключить профиль доменов', self.add_domains),
            ('Проверяемые домены', self.show_domains),
            ('Обновить IPSet', self.update_ipset),
            ('Диагностика служб', self.diagnostics),
            ('Service Manager Flowseal…', self.upstream),
        ]:
            btn = QPushButton(title); btn.clicked.connect(callback); layout.addWidget(btn)
        hint = QLabel('Изменения настроек Flowseal требуют повторного запуска стратегии.\nПолные upstream-тесты, замена fake-файлов и установка службы доступны в оригинальном service.bat.')
        hint.setObjectName('muted'); hint.setWordWrap(True); layout.addWidget(hint)
        layout.addStretch()
        close = QPushButton('Готово'); close.clicked.connect(self.close); outer.addWidget(close)

    def exec(self):
        return self.owner.inline_custom(self)

    def close(self):
        self._active_modal.finish(False)

    def err(self, title, op):
        try: op(); self.owner.note('Сохранено. Перезапусти Zapret для применения.')
        except Exception as e: QMessageBox.warning(self, title, str(e))

    def apply_game(self): self.err('Game Filter', lambda: m.set_game_filter(m.sub_dir(self.owner.cfg,'zapret'), self.game.currentData()))

    def apply_ipset(self):
        mode = self.ipset.currentData()
        if mode == 'any' and not self.owner.confirm('IPSet any', 'Режим any может нарушить работу других сайтов. Продолжить?'): return
        self.err('IPSet', lambda: m.set_ipset_mode(m.sub_dir(self.owner.cfg,'zapret'), mode))

    def add_domains(self):
        def action():
            n, bad = m.merge_domains(m.sub_dir(self.owner.cfg, 'zapret'), include_extended=True)
            self.owner.note(f'Добавлено адресов: {n}; пропущено: {len(bad)}')
        self.err('Списки доменов', action)

    def show_domains(self):
        try:
            catalog = load_catalog(m.sub_dir(self.owner.cfg, 'zapret'))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Адреса проверки', str(exc))
            return
        dialog = QFrame(self.owner)
        dialog.setFixedSize(340, 395)
        lay = QVBoxLayout(dialog)
        lay.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f'Проверяемые адреса · {len(catalog.hosts)}')
        title.setObjectName('heading'); lay.addWidget(title)
        hint = QLabel('Взяты из текущих списков Flowseal, включая Cloudflare из пользовательского списка. HTTPS-тест не подтверждает работу UDP или видео.')
        hint.setWordWrap(True); hint.setObjectName('muted'); lay.addWidget(hint)
        source_text = ', '.join(f'{name}: {count}' for name, count in catalog.sources.items())
        label = QLabel(source_text); label.setObjectName('small'); label.setWordWrap(True); lay.addWidget(label)
        search = QLineEdit(); search.setPlaceholderText('Поиск по доменам…'); lay.addWidget(search)
        editor = QPlainTextEdit(); editor.setReadOnly(True); editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(editor, 1)
        def update(text=''):
            filtered = [h for h in catalog.hosts if text.casefold().strip() in h.casefold()]
            editor.setPlainText('\n'.join(filtered))
        search.textChanged.connect(update)
        update()
        close = QPushButton('Закрыть'); close.clicked.connect(lambda: dialog._active_modal.finish(False)); lay.addWidget(close)
        self.owner.inline_custom(dialog)

    def update_ipset(self):
        if self.owner.confirm('Обновить IPSet', 'Загрузить официальный IPSet и сохранить резервную копию?'):
            self.owner.job('ipset', lambda: m.update_ipset(m.sub_dir(self.owner.cfg, 'zapret')))

    def diagnostics(self):
        names = ('zapret','WinDivert','WinDivert14','BFE')
        report = '\n'.join(f'{n}: {m.service_state(n)}' for n in names)
        QMessageBox.information(self, 'Службы Windows', report)

    def upstream(self):
        script = m.sub_dir(self.owner.cfg, 'zapret') / 'service.bat'
        if not script.is_file():
            QMessageBox.warning(self, 'Flowseal', 'Сначала загрузите Zapret.'); return
        if not self.owner.confirm('Оригинальный менеджер Flowseal',
            'Откроется официальное консольное меню Flowseal для сложных операций со службами. Оно может потребовать UAC. Продолжить?'):
            return
        if os.name != 'nt': return
        os.startfile(str(script))


class NetPilot(QMainWindow):
    def __init__(self, smoke_mode=False):
        super().__init__()
        self.smoke_mode=bool(smoke_mode)
        self.cfg = dict(m.DEFAULT,installed={}) if self.smoke_mode else m.read_cfg()
        self.bus = Bus(); self.bus.done.connect(self.completed); self.bus.progress.connect(self.progress_update)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='NetPilotWorker')
        self.running = set(); self.test_running = False; self.cancel = threading.Event()
        self.managed_pids = []; self.telegram_proc = None; self.warp_connected = False
        self._component_states = {}
        self._overflow_rect = None
        self.warp_pending = False; self.latest = {}; self.latest_test = None
        self._health_failures = 0; self.latest_auto_health = None; self._automatic_test = False
        self.suppress_toggle = False
        self.zapret_snapshot = ZapretSnapshot()
        self.zapret_semantic_state = 'disabled'
        self._zapret_inventory_ready = False
        self._zapret_generation = 0
        self._zapret_operation_generation = 0
        self._zapret_operation = None
        self._prompted_strategies = set()
        self.setWindowTitle('NetPilot'); self.setWindowIcon(app_icon())
        # Small frameless flyout like Volty. The shell notification overflow is
        # measured for centering, not guessed from the cursor or screen edge.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 324x414 keeps the same readable proportions at about 40% less area.
        self.setFixedSize(324, 414)
        self._build()
        self.window_transition=WindowTransition(self)
        self._tray_error_until = 0.0
        self._tray_active_count = 0
        self.tray = QSystemTrayIcon(status_icon(), self)
        menu = QMenu(self)
        action = QAction('Открыть NetPilot', self); action.triggered.connect(self.show_panel); menu.addAction(action)
        self.tray_summary = QAction('Нет активных подключений', self)
        self.tray_summary.setEnabled(False); menu.addAction(self.tray_summary)
        menu.addSeparator()
        self.tray_actions = {}
        for title, callback in [('Zapret', self.toggle_zapret), ('Telegram Proxy', self.toggle_telegram), ('WARP', self.toggle_warp)]:
            a = QAction(title, self); a.setCheckable(True); a.triggered.connect(callback); menu.addAction(a)
            self.tray_actions[title.lower().replace(' proxy','')] = a
        a = QAction('Подобрать стратегию', self); a.triggered.connect(self.start_test); menu.addAction(a)
        menu.addSeparator()
        a = QAction('Выйти', self); a.triggered.connect(self.quit); menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_clicked)
        self.tray.setToolTip('NetPilot · ожидание')
        self.tray.show()
        self.status_timer = QTimer(self); self.status_timer.timeout.connect(self.refresh)
        self.daily_timer=QTimer(self);self.daily_timer.timeout.connect(lambda:self.check_updates(False))
        self.auto_strategy_timer=QTimer(self);self.auto_strategy_timer.timeout.connect(self.auto_strategy_check)
        if not self.smoke_mode:
            self.status_timer.start(15000);self.daily_timer.start(6*60*60*1000)
            self.auto_strategy_timer.start(15*60*1000)
            self.refresh();self.refresh_zapret_inventory()
            QTimer.singleShot(500, self.on_first_run)
            if self.cfg.get('auto_check',True): QTimer.singleShot(1500, self.check_updates)
            if self.cfg.get('auto_strategy',True): QTimer.singleShot(12000, self.auto_strategy_check)

    def _build(self):
        root = QWidget(objectName='root'); self.setCentralWidget(root)
        box = QVBoxLayout(root); box.setContentsMargins(14, 12, 14, 10); box.setSpacing(8)
        header = QHBoxLayout(); box.addLayout(header)
        self.back_btn = HeaderAction('back'); self.back_btn.clicked.connect(lambda:self.show_page(0))
        self.back_btn.setVisible(False); header.addWidget(self.back_btn)
        title = QLabel('◆  NetPilot'); title.setObjectName('brand'); header.addWidget(title)
        header.addStretch()
        self.settings_btn = HeaderAction('settings')
        self.settings_btn.clicked.connect(self.toggle_page); header.addWidget(self.settings_btn)
        self.hide_btn = HeaderAction('minimize')
        self.hide_btn.clicked.connect(self.hide_to_tray); header.addWidget(self.hide_btn)
        self.pages = QStackedWidget(); box.addWidget(self.pages, 1)
        self._overview(); self._details(); self._settings()
        self.message = QLabel('Готов к работе'); self.message.setObjectName('muted')
        self.message.setWordWrap(True); self.message.setMaximumHeight(34); self.message.setVisible(False); box.addWidget(self.message)
        self.message_timer=QTimer(self);self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self._hide_message)
        self.strategy_offer_widget=QWidget();offer=QHBoxLayout(self.strategy_offer_widget)
        offer.setContentsMargins(0,0,0,0);offer.setSpacing(6)
        self.strategy_offer_label=QLabel('');self.strategy_offer_label.setObjectName('muted')
        self.strategy_offer_label.setWordWrap(True);offer.addWidget(self.strategy_offer_label,1)
        self.strategy_offer_accept=QPushButton('Включить');self.strategy_offer_accept.setObjectName('primary')
        self.strategy_offer_accept.clicked.connect(self._accept_strategy_offer);offer.addWidget(self.strategy_offer_accept)
        self.strategy_offer_decline=QPushButton('Не сейчас');self.strategy_offer_decline.setObjectName('quiet')
        self.strategy_offer_decline.clicked.connect(self._decline_strategy_offer);offer.addWidget(self.strategy_offer_decline)
        self.strategy_offer_widget.setVisible(False);box.addWidget(self.strategy_offer_widget)

    def _bottom_row(self, parent):
        footer=QWidget();bottom=QHBoxLayout(footer);bottom.setContentsMargins(0,0,0,0);parent.addWidget(footer)
        button=QPushButton('↓  Компоненты');button.setObjectName('quiet');button.clicked.connect(self.install_dialog);bottom.addWidget(button)
        bottom.addStretch()
        about=QLabel('v'+VERSION.upper().replace('-RC',' RC'));about.setObjectName('small');bottom.addWidget(about)
        return button,footer

    def _overview(self):
        page = QWidget(); self.pages.addWidget(page)
        content = QVBoxLayout(page); content.setContentsMargins(0, 6, 0, 0); content.setSpacing(5)
        content.addStretch(1)
        self.status_orb = StatusOrb(); content.addWidget(self.status_orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.home_title = QLabel('Соединение в порядке'); self.home_title.setObjectName('heroTitle')
        self.home_title.setAlignment(Qt.AlignmentFlag.AlignCenter); content.addWidget(self.home_title)
        self.home_subtitle = QLabel('NetPilot следит за сетью автоматически'); self.home_subtitle.setObjectName('heroSubtitle')
        self.home_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter); content.addWidget(self.home_subtitle)
        content.addSpacing(5)
        services=QHBoxLayout();services.setSpacing(10);content.addLayout(services)
        self.home_summaries={}
        for index,(key,title) in enumerate((('zapret','Zapret'),('telegram','Telegram'),('warp','WARP'))):
            item=SummaryItem(title,key);self.home_summaries[key]=item;services.addWidget(item,1)
            if index<2:
                divider=QFrame(objectName='separator');divider.setFixedHeight(42);services.addWidget(divider)
        self.details_button=QPushButton('Подробности  ›');self.details_button.setObjectName('details')
        self.details_button.clicked.connect(self.open_details);content.addWidget(self.details_button,0,Qt.AlignmentFlag.AlignHCenter)
        content.addStretch(2)
        self.install_button,self.overview_footer=self._bottom_row(content)

    def _fact_row(self, parent, title, value):
        row=QHBoxLayout();row.setSpacing(8);parent.addLayout(row)
        label=QLabel(title);label.setObjectName('muted');row.addWidget(label,1)
        result=QLabel(value);result.setObjectName('factValue');result.setAlignment(Qt.AlignmentFlag.AlignRight);row.addWidget(result)
        return result

    def _details(self):
        page=QWidget();self.pages.addWidget(page)
        content=QVBoxLayout(page);content.setContentsMargins(0,7,0,0);content.setSpacing(8)
        heading=QLabel('Подробности');heading.setObjectName('heading');content.addWidget(heading)
        tabs=QHBoxLayout();tabs.setSpacing(4);content.addLayout(tabs)
        self.detail_tabs={}
        for index,(key,title) in enumerate((('zapret','Zapret'),('telegram','Telegram'),('warp','WARP'))):
            button=QPushButton(title+'\nГотов');button.setObjectName('componentTab');button.setCheckable(True)
            button.clicked.connect(lambda checked=False,i=index:self.select_detail(i));tabs.addWidget(button,1)
            self.detail_tabs[key]=button
            if index<2:
                divider=QFrame(objectName='separator');divider.setFixedHeight(38);tabs.addWidget(divider)
        self.detail_stack=QStackedWidget();self.detail_stack.setFixedHeight(204);content.addWidget(self.detail_stack)
        self.controls={};self.detail_status_labels={};self.detail_facts={}
        specs={
            'zapret':(('Стратегия','Не выбрана'),('Результат','Нет проверки'),('Запуск сервиса','Выключен')),
            'telegram':(('Прокси','127.0.0.1:1443'),('Подключение','Автоматически')),
            'warp':(('Клиент','Cloudflare WARP'),('Подключение','Отключено')),
        }
        handlers={'telegram':self.toggle_telegram,'warp':self.toggle_warp}
        for key,title in (('zapret','Zapret'),('telegram','Telegram'),('warp','WARP')):
            panel=QFrame(objectName='detailPanel');self.detail_stack.addWidget(panel)
            wrap=QVBoxLayout(panel);wrap.setContentsMargins(12,8,12,8);wrap.setSpacing(4)
            head=QHBoxLayout();wrap.addLayout(head)
            dot=AnimatedIcon({'zapret':IconKind.LOCK,'telegram':IconKind.NETWORK,
                              'warp':IconKind.CLOUD}[key],24);head.addWidget(dot)
            names=QVBoxLayout();names.setSpacing(0);head.addLayout(names,1)
            name=QLabel(title);name.setObjectName('detailName');names.addWidget(name)
            state=QLabel('Готов');state.setObjectName('muted');names.addWidget(state)
            self.controls[key]=(None,state);self.detail_status_labels[key]=(dot,state)
            facts={}
            for fact,value in specs[key]:facts[fact]=self._fact_row(wrap,fact,value)
            self.detail_facts[key]=facts
            wrap.addStretch()
            if key=='zapret':
                service=QHBoxLayout();service.setSpacing(9);wrap.addLayout(service)
                self.detail_service_toggle=Toggle(False if self.smoke_mode else m.service_state('zapret')!='not_installed')
                self.detail_service_toggle.toggled.connect(self.change_zapret_autostart);service.addWidget(self.detail_service_toggle)
                label=QLabel('Запускать вместе с Windows');label.setWordWrap(True);service.addWidget(label,1)
                action=QPushButton('Подобрать лучшую стратегию  ›');action.setObjectName('link');action.clicked.connect(self.start_test);wrap.addWidget(action)
                self.test_button=action
                self.stop_all_zapret_button=QPushButton('Остановить весь Zapret  ›')
                self.stop_all_zapret_button.setObjectName('danger')
                self.stop_all_zapret_button.clicked.connect(self.show_stop_zapret_options)
                self.stop_all_zapret_button.setVisible(False);wrap.addWidget(self.stop_all_zapret_button)
            elif key=='telegram':
                control=QHBoxLayout();control.setSpacing(9);wrap.addLayout(control)
                runtime=Toggle();runtime.clicked.connect(handlers[key]);control.addWidget(runtime)
                control.addWidget(QLabel('Включить Telegram Proxy'),1);self.controls[key]=(runtime,state)
            else:
                control=QHBoxLayout();control.setSpacing(9);wrap.addLayout(control)
                runtime=Toggle();runtime.clicked.connect(handlers[key]);control.addWidget(runtime)
                control.addWidget(QLabel('Подключить WARP'),1);self.controls[key]=(runtime,state)
        self.strategy_name=self.detail_facts['zapret']['Стратегия']
        progress_row=QHBoxLayout();progress_row.setSpacing(9);content.addLayout(progress_row)
        self.test_progress=QProgressBar();self.test_progress.setRange(0,100);self.test_progress.setValue(0)
        self.test_progress.setTextVisible(False);self.test_progress.setVisible(False);progress_row.addWidget(self.test_progress,1)
        self.test_percent=QLabel('0%');self.test_percent.setObjectName('small');self.test_percent.setFixedWidth(32)
        self.test_percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.test_percent.setVisible(False);progress_row.addWidget(self.test_percent)
        self.test_details=QLabel('');self.test_details.setObjectName('small');self.test_details.setWordWrap(True)
        self.test_details.setVisible(False);content.addWidget(self.test_details)
        content.addStretch()
        self.details_install_button,self.details_footer=self._bottom_row(content)
        self.select_detail(0)
        self.load_last_test()

    def _setting_row(self, parent, name, value, handler, subtitle=''):
        frame=QFrame(objectName='row'); parent.addWidget(frame)
        line=QHBoxLayout(frame); line.setContentsMargins(12,10,10,10);line.setSpacing(10)
        text=QVBoxLayout();text.setSpacing(2);line.addLayout(text,1)
        label=QLabel(name);label.setStyleSheet('font-weight:650');text.addWidget(label)
        if subtitle:
            detail=QLabel(subtitle);detail.setObjectName('muted');detail.setWordWrap(True);text.addWidget(detail)
        toggle=Toggle(value);toggle.toggled.connect(handler); line.addWidget(toggle)
        return toggle,frame

    def _settings(self):
        page=QWidget();self.pages.addWidget(page)
        whole=QVBoxLayout(page);whole.setContentsMargins(0,8,0,0);whole.setSpacing(8)
        label=QLabel('Настройки');label.setObjectName('heading');whole.addWidget(label)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);whole.addWidget(scroll,1)
        content=QWidget();content.setObjectName('settingsContent');scroll.setWidget(content)
        self.prefs=QVBoxLayout(content);self.prefs.setContentsMargins(0,4,5,4);self.prefs.setSpacing(8)
        launch=QHBoxLayout();self.prefs.addLayout(launch)
        divider=QLabel('ЗАПУСК И АВТОМАТИЗАЦИЯ');divider.setObjectName('eyebrow');launch.addWidget(divider)
        launch.addStretch();self.service_badge=QLabel('');launch.addWidget(self.service_badge)
        self.autostart_toggle,_=self._setting_row(
            self.prefs,'NetPilot вместе с Windows',self.autostart_enabled(),self.change_autostart,
            'Открывает приложение в трее после входа в систему')
        service_present=False if self.smoke_mode else m.service_state('zapret')!='not_installed'
        self.zapret_service_toggle,self.zapret_service_row=self._setting_row(
            self.prefs,'Zapret вместе с Windows',service_present,self.change_zapret_autostart,
            'Системная служба · выбранная стратегия запускается до входа в аккаунт')
        self.start_hidden_toggle,_=self._setting_row(
            self.prefs,'Запускать свёрнутым',self.cfg.get('start_hidden',False),
            lambda v:self.preference('start_hidden',v),'Не показывать окно при автоматическом запуске NetPilot')
        self.auto_strategy_toggle,_=self._setting_row(
            self.prefs,'Автоматически подбирать стратегию',self.cfg.get('auto_strategy',True),
            self.change_auto_strategy,'Проверять активное соединение и менять стратегию после двух сбоев')
        updates=QLabel('ОБНОВЛЕНИЯ');updates.setObjectName('eyebrow');self.prefs.addWidget(updates)
        self._setting_row(self.prefs,'Проверять обновления',self.cfg.get('auto_check',True),lambda v:self.preference('auto_check',v))
        self._setting_row(self.prefs,'Автоустановка обновлений',self.cfg.get('auto_upgrade',False),lambda v:self.preference('auto_upgrade',v))
        divider=QLabel('КОМПОНЕНТЫ И ОБСЛУЖИВАНИЕ');divider.setObjectName('eyebrow');self.prefs.addWidget(divider)
        for title, handler in [
            ('Установить компоненты',self.install_dialog),
            ('Проверить новые версии',lambda:self.check_updates(True)),
            ('Вернуть Zapret до обновления',lambda:self.rollback('zapret')),
                        ('Расширенные настройки Zapret',self.expert),
            ('Диагностика',self.diagnostics),
        ]:
            button=QPushButton(title);button.setObjectName('quiet');button.clicked.connect(handler);self.prefs.addWidget(button)
        self.restore_autostart_button=QPushButton('Восстановить внешний автозапуск Zapret')
        self.restore_autostart_button.setObjectName('quiet')
        self.restore_autostart_button.clicked.connect(self.restore_external_autostart)
        self.prefs.addWidget(self.restore_autostart_button)
        label=QLabel('NetPilot — неофициальное приложение. Flowseal и Cloudflare — самостоятельные проекты.');label.setWordWrap(True)
        label.setObjectName('muted');self.prefs.addWidget(label)
        self.prefs.addStretch()
        self.refresh_service_setting()
        self.refresh_restore_autostart_action()

    def toggle_page(self):
        self.show_page(2 if self.pages.currentIndex()==0 else 0)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.back_btn.setVisible(index!=0)
        self.settings_btn.setVisible(index==0)
        self._sync_page_footer()

    def open_details(self):
        self.show_page(1)

    def select_detail(self, index):
        if not hasattr(self,'detail_stack'):return
        self.detail_stack.setCurrentIndex(index)
        keys=list(self.detail_tabs)
        for current,button in enumerate(self.detail_tabs.values()):
            button.setChecked(current==index)
            key=keys[current]
            title={'zapret':'Zapret','telegram':'Telegram','warp':'WARP'}[key]
            label=self._component_states.get(key,(False,'Готов'))[1]
            button.setText(title+'\n'+('Выбран' if current==index else label))

    def note(self, message, error=False):
        full=str(message)
        self._decline_strategy_offer()
        self.message.setText(full[:180]);self.message.setToolTip(full)
        self.message.setObjectName('error' if error else 'muted');self.message.setVisible(True)
        self.message.style().unpolish(self.message); self.message.style().polish(self.message)
        self._sync_page_footer()
        self.message_timer.start(8000 if error else 5000)
        if error and hasattr(self, 'tray'):
            self._tray_error_until = time.monotonic() + 8
            self._update_tray()
            QTimer.singleShot(8100, self._update_tray)

    def _sync_page_footer(self):
        visible=self.message.isHidden() and self.strategy_offer_widget.isHidden()
        if hasattr(self,'overview_footer'):self.overview_footer.setVisible(visible)
        if hasattr(self,'details_footer'):self.details_footer.setVisible(visible)

    def _hide_message(self):
        self.message.hide()
        self._sync_page_footer()

    def apply_zapret_snapshot(self, snapshot, generation=None):
        """Render only a current immutable inventory snapshot."""
        if not isinstance(snapshot, ZapretSnapshot):
            return self.apply_zapret_inventory_error('Некорректный результат проверки')
        if generation is not None and generation != self._zapret_generation:
            return
        self.zapret_snapshot=snapshot;self._zapret_inventory_ready=True
        own=tuple(item for item in snapshot.active if item.origin=='netpilot')
        external=tuple(item for item in snapshot.active if item.origin!='netpilot')
        if snapshot.active_count > 1:
            semantic,label='multiple','Работают несколько Zapret'
        elif own:
            semantic,label='managed','Работает'
        elif external:
            semantic,label='external','Внешний Zapret работает'
        else:
            semantic,label='disabled','Готов'
        self._render_zapret_state(semantic,label,bool(own),bool(snapshot.active))
        self.refresh_restore_autostart_action()

    def _render_zapret_state(self, semantic, label, own_active, any_active):
        self.zapret_semantic_state=semantic
        self._zapret_own_active=bool(own_active)
        self._component_states['zapret']=(bool(any_active),label)
        icon_state={'disabled':IconState.DISABLED,'managed':IconState.ENABLED,
                    'external':IconState.EXTERNAL,'multiple':IconState.PARTIAL,
                    'partial':IconState.PARTIAL,'error':IconState.ERROR,
                    'starting':IconState.STARTING,'stopping':IconState.STOPPING}.get(semantic,IconState.DISABLED)
        dot,status=self.detail_status_labels['zapret'];dot.set_state(icon_state);status.setText(label)
        self.home_summaries['zapret'].set_state(bool(any_active),label)
        self.home_summaries['zapret'].set_icon_state(icon_state)
        selected=self.detail_tabs['zapret'].isChecked()
        self.detail_tabs['zapret'].setText('Zapret\n'+('Выбран' if selected else label))
        self.stop_all_zapret_button.setVisible(bool(any_active))
        self.test_button.setVisible(not any_active)
        self._set_zapret_actions_enabled(self._zapret_operation is None)
        self._update_tray()

    def apply_zapret_operation_result(self, result):
        snapshot=result.final_snapshot or self.zapret_snapshot
        any_active=bool(snapshot.active)
        own=any(item.origin=='netpilot' for item in snapshot.active)
        if result.status in {'partial','error'} and any_active:
            self.zapret_snapshot=snapshot;self._zapret_inventory_ready=True
            self._render_zapret_state('partial','Zapret остановлен не полностью',own,True)
        elif result.status=='error':
            self.apply_zapret_inventory_error('; '.join(result.errors))
        else:
            self.apply_zapret_snapshot(snapshot)

    def apply_zapret_inventory_error(self, error):
        self.zapret_semantic_state='error'
        dot,status=self.detail_status_labels['zapret'];dot.set_state(IconState.ERROR)
        status.setText('Не удалось проверить Zapret');status.setToolTip(str(error))
        self.home_summaries['zapret'].set_state(False,'Проверка недоступна')
        self.home_summaries['zapret'].set_icon_state(IconState.ERROR)
        self._component_states['zapret']=(False,'Проверка недоступна')
        self._update_tray()

    def refresh_zapret_inventory(self):
        if self._zapret_operation is not None:
            return
        if any(str(item).startswith('zapret_inventory:') for item in self.running):
            return
        self._zapret_generation+=1
        generation=self._zapret_generation
        key=f'zapret_inventory:{generation}'
        folder=m.sub_dir(self.cfg,'zapret')
        self.running.add(key);self._set_zapret_actions_enabled(False)
        def scan():
            try:
                result=WindowsZapretAdapter((folder,)).snapshot()
                self.bus.done.emit(key,result,'')
            except Exception as exc:
                self.bus.done.emit(key,None,str(exc))
        self.pool.submit(scan)

    def show_stop_zapret_options(self):
        own=sum(item.origin=='netpilot' for item in self.zapret_snapshot.active)
        external=sum(item.origin!='netpilot' for item in self.zapret_snapshot.active)
        panel=StopZapretPanel(own,external)
        if self.inline_custom(panel) and panel.mode is not None:
            self.stop_all_zapret(panel.mode, confirmed=True)

    def stop_all_zapret(self, mode, confirmed=False):
        mode=StopMode(mode)
        if not self.zapret_snapshot.active:
            self.note('Активный Zapret не найден.');return
        own=sum(item.origin=='netpilot' for item in self.zapret_snapshot.active)
        external=sum(item.origin!='netpilot' for item in self.zapret_snapshot.active)
        if not confirmed:
            suffix=(' Автозапуск подтверждённых служб и заданий будет отключён обратимо.'
                    if mode==StopMode.DISABLE_RESTART else ' Настройки автозапуска не изменятся.')
            if not self.inline_confirm('Остановить весь Zapret',
                    f'Свой: {own}, внешний: {external}.{suffix}'):
                return
        operation='disable_restart' if mode==StopMode.DISABLE_RESTART else 'stop_all'
        self.begin_privileged_operation(operation)

    def _set_zapret_actions_enabled(self, enabled):
        for name in ('stop_all_zapret_button','zapret_service_toggle','detail_service_toggle'):
            widget=getattr(self,name,None)
            if widget is not None:widget.setEnabled(bool(enabled))

    def begin_privileged_operation(self, operation, strategy=''):
        if self._zapret_operation is not None:
            self.note('Дождитесь завершения операции Zapret.',True);return False
        if not self._zapret_inventory_ready:
            self.note('Сначала дождитесь проверки Zapret.',True);self.refresh_zapret_inventory();return False
        request_id=str(uuid.uuid4())
        request=PrivilegedRequest(request_id,operation,time.time(),
                                  snapshot_digest(self.zapret_snapshot),strategy)
        request_path=None
        try:
            request_path=write_request(m.ROOT/'privileged',request)
            if not elevate_request(request_id):
                raise RuntimeError('Windows не запустила подтверждение UAC')
        except Exception as exc:
            if request_path is not None:request_path.unlink(missing_ok=True)
            self.note(str(exc),True);return False
        self._zapret_operation_generation+=1
        generation=self._zapret_operation_generation
        self._zapret_operation=(request_id,operation,strategy,time.monotonic()+180)
        self._render_zapret_state('stopping' if operation!='transfer' else 'starting',
                                  'Останавливаем…' if operation!='transfer' else 'Настраиваем автозапуск…',
                                  self._zapret_own_active,bool(self.zapret_snapshot.active))
        QTimer.singleShot(250,lambda:self._poll_privileged_response(request_id,generation))
        return True

    def _poll_privileged_response(self, request_id, generation):
        if generation!=self._zapret_operation_generation or not self._zapret_operation:
            return
        try:
            response=read_response(request_id,m.ROOT/'privileged')
        except FileNotFoundError:
            if time.monotonic()<self._zapret_operation[3]:
                QTimer.singleShot(250,lambda:self._poll_privileged_response(request_id,generation));return
            response=None
        except Exception as exc:
            self._zapret_operation=None;self.note('Операция Zapret: '+str(exc),True)
            self.refresh_zapret_inventory();return
        operation=self._zapret_operation[1];self._zapret_operation=None
        if response is None:
            self.note('Операция Zapret не завершилась вовремя.',True)
        elif response.status=='success':
            copy={'stop_all':'Весь Zapret остановлен',
                  'disable_restart':'Zapret остановлен, автозапуск отключён',
                  'transfer':'Автозапуск выбранной стратегии включён',
                  'restore_autostart':'Внешний автозапуск восстановлен'}[operation]
            self.note(copy)
        else:
            self.note('Операция Zapret: '+('; '.join(response.errors) or response.status),True)
        self.refresh_zapret_inventory()

    def request_zapret_transfer(self, strategy):
        active=bool(self.zapret_snapshot.active)
        message=(f'Передать автозапуск стратегии «{strategy}» службе NetPilot?')
        if active:
            message+=' Работающие экземпляры Zapret будут остановлены, а конфликтующий автозапуск сохранён для восстановления.'
        if self.inline_confirm('Zapret вместе с Windows',message):
            self.begin_privileged_operation('transfer',strategy)

    def refresh_restore_autostart_action(self):
        button=getattr(self,'restore_autostart_button',None)
        if button is None:return
        if self.smoke_mode:button.hide();return
        try:
            backup=load_backup()
            visible=bool(backup and (backup.services or backup.tasks))
            button.setToolTip('')
        except Exception as exc:
            visible=True;button.setToolTip('Резерв требует проверки: '+str(exc))
        button.setVisible(visible)

    def restore_external_autostart(self):
        if self.inline_confirm('Восстановить внешний автозапуск',
                'Вернуть сохранённые режимы запуска внешних служб и заданий Zapret?'):
            self.begin_privileged_operation('restore_autostart')

    def offer_strategy_autostart(self, strategy):
        if not strategy or strategy in self._prompted_strategies:
            return
        if m.zapret_service_owned() and m.zapret_service_strategy()==strategy:
            return
        self._prompted_strategies.add(strategy)
        self._offered_strategy=strategy
        self.message.hide();self.strategy_offer_label.setText('Запускать выбранную стратегию вместе с Windows?')
        self.strategy_offer_widget.show();self._sync_page_footer()

    def _accept_strategy_offer(self):
        strategy=getattr(self,'_offered_strategy','');self._decline_strategy_offer()
        if strategy:self.request_zapret_transfer(strategy)

    def _decline_strategy_offer(self):
        if hasattr(self,'strategy_offer_widget'):
            self.strategy_offer_widget.hide();self._sync_page_footer()

    def inline_confirm(self, title, message):
        return InlineModal(self, title, message, question=True).exec()

    def inline_notice(self, title, message):
        return InlineModal(self, title, message).exec()

    def inline_custom(self, widget):
        return InlineModal(self, custom=widget).exec()

    def confirm(self, title, text):
        return QMessageBox.question(self,title,text)==QMessageBox.StandardButton.Yes

    def preference(self, key, value):
        self.cfg[key]=value; m.write_cfg(self.cfg)

    def change_auto_strategy(self, value):
        self.preference('auto_strategy',value)
        if value:QTimer.singleShot(500,self.auto_strategy_check)

    def autostart_cmd(self):
        if getattr(sys,'frozen',False):return '"'+sys.executable+'" --tray'
        script=Path(__file__).resolve()
        exe=Path(sys.executable)
        if exe.name.lower()=='python.exe': exe=exe.with_name('pythonw.exe')
        return f'"{exe}" "{script}" --tray'

    def autostart_enabled(self):
        if os.name!='nt':return False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                value,_=winreg.QueryValueEx(key,'NetPilot')
                return value==self.autostart_cmd()
        except OSError:return False

    def change_autostart(self, value):
        if os.name!='nt':return
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                if value:winreg.SetValueEx(key,'NetPilot',0,winreg.REG_SZ,self.autostart_cmd())
                else:
                    try:winreg.DeleteValue(key,'NetPilot')
                    except FileNotFoundError:pass
        except OSError as exc:
            QMessageBox.warning(self,'Автозапуск',str(exc))
            self.sender().setChecked(not value)

    def refresh_service_setting(self):
        if not hasattr(self,'zapret_service_toggle'):return
        if self.smoke_mode:state='not_installed';present=False;owned=False
        else:state=m.service_state('zapret');present=state!='not_installed';owned=m.zapret_service_owned()
        self.zapret_service_toggle.blockSignals(True);self.zapret_service_toggle.setChecked(present and owned)
        self.zapret_service_toggle.blockSignals(False)
        if hasattr(self,'detail_service_toggle'):
            self.detail_service_toggle.blockSignals(True);self.detail_service_toggle.setChecked(present and owned)
            self.detail_service_toggle.blockSignals(False)
            self.detail_service_toggle.setEnabled(self._zapret_operation is None)
        if hasattr(self,'detail_facts'):
            self.detail_facts['zapret']['Запуск сервиса'].setText(
                'Включён' if present and owned else 'Внешний' if present else 'Выключен')
        if present and owned:
            strategy=m.zapret_service_strategy() or 'выбранная стратегия'
            self.service_badge.setText('АВТО · '+strategy.replace('general','').strip(' ()') or 'GENERAL')
            self.service_badge.setObjectName('success');self.zapret_service_toggle.setEnabled(True)
            self.zapret_service_toggle.setToolTip('Служба создана NetPilot')
        elif present:
            self.service_badge.setText('СЛУЖБА FLOWSEAL');self.service_badge.setObjectName('warn')
            self.zapret_service_toggle.setEnabled(self._zapret_operation is None)
            self.zapret_service_toggle.setToolTip('Можно передать выбранную стратегию службе NetPilot')
        else:
            self.service_badge.setText('ВЫКЛ');self.service_badge.setObjectName('muted')
            self.zapret_service_toggle.setEnabled(True);self.zapret_service_toggle.setToolTip('Установить службу Zapret')
        if 'service' in self.running:self.zapret_service_toggle.setEnabled(False)
        self.service_badge.style().unpolish(self.service_badge);self.service_badge.style().polish(self.service_badge)

    def change_zapret_autostart(self, value):
        folder=m.sub_dir(self.cfg,'zapret')
        if value and not folder.is_dir():
            self.refresh_service_setting();self.install_dialog();return
        if self.test_running:
            self.note('Сначала остановите проверку стратегии.',True)
            self.refresh_service_setting();return
        strategy=self.cfg.get('strategy','general.bat')
        choices=m.list_strategies(folder) if folder.is_dir() else []
        if value and strategy not in choices:strategy=choices[0] if choices else ''
        if value:
            self.request_zapret_transfer(strategy);return
        else:
            message='Удалить автозапуск Zapret? Будет удалена только служба, созданная NetPilot.'
            flag='--remove-zapret-service'
        if not self.confirm('Zapret вместе с Windows',message):
            self.refresh_service_setting();return
        if not b.is_admin():
            self.elevate(flag);return
        self.apply_zapret_service(value)

    def apply_zapret_service(self, enabled):
        folder=m.sub_dir(self.cfg,'zapret');strategy=self.cfg.get('strategy','general.bat')
        choices=m.list_strategies(folder) if folder.is_dir() else []
        if enabled and strategy not in choices:strategy=choices[0] if choices else strategy
        self.zapret_service_toggle.setEnabled(False)
        self.note('Настраиваем службу Zapret…')
        self.job('service',lambda:m.install_zapret_service(folder,strategy) if enabled else m.remove_zapret_service())

    def _update_tray(self):
        """Keep the small Windows surface useful without opening the window."""
        if not hasattr(self, 'tray'):
            return
        states = self._component_states
        active_count = sum(bool(value[0]) for value in states.values())
        busy = bool(self.running or self.test_running or self.warp_pending)
        error = time.monotonic() < self._tray_error_until
        signature = (active_count, busy, error)
        if signature != getattr(self, '_tray_signature', None):
            self.tray.setIcon(status_icon(active_count, busy, error))
            self._tray_signature = signature
        self._tray_active_count = active_count

        if error:
            summary = 'Требуется внимание'
        elif busy:
            summary = 'Выполняется операция…'
        elif active_count:
            summary = f'Активно {active_count} из 3'
        else:
            summary = 'Нет активных подключений'
        self.tray_summary.setText(summary)

        labels = {'zapret':'Zapret', 'telegram':'Telegram', 'warp':'WARP'}
        details = []
        for key, title in labels.items():
            active, label = states.get(key, (False, 'Проверяется'))
            action = self.tray_actions.get(key)
            if action is not None:
                action.setChecked(bool(self._zapret_own_active) if key=='zapret' and hasattr(self,'_zapret_own_active') else bool(active))
                action.setText(f'{title} · {label}')
            details.append(f'{title}: {label.lower()}')
        self.tray.setToolTip('NetPilot · ' + summary + '\n' + ' · '.join(details))

    def tray_clicked(self, reason):
        if reason==QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():self.hide_to_tray()
            else:self.show_panel()

    def hide_to_tray(self):
        target=self.tray.geometry() if self.tray.isVisible() else None
        self.window_transition.hide_to_tray(target)

    def show_panel(self):
        # Windows can hide its hidden-icon flyout as soon as this window gets
        # focus. Preserve the last real HWND rectangle, exactly as in Volty.
        detected = find_overflow_rect()
        if detected is not None:
            self._overflow_rect = detected
        self._position_panel()
        final=self.geometry()
        self.window_transition.restore(final)
        self.activateWindow()

    def _position_panel(self):
        tray_rect = self.tray.geometry() if self.tray.isVisible() else None
        screen = (QApplication.screenAt(tray_rect.center()) if tray_rect and tray_rect.isValid()
                  else QApplication.screenAt(QCursor.pos())) or QApplication.primaryScreen()
        if screen is None:
            return
        a = screen.availableGeometry()
        available = Rect(a.left(), a.top(), a.right() + 1, a.bottom() + 1)
        measured = find_overflow_rect()
        if measured:
            self._overflow_rect = measured
        overflow = self._overflow_rect
        if overflow:
            logical = physical_to_qt(overflow, Rect(screen.geometry().left(),
                                  screen.geometry().top(), screen.geometry().right()+1,
                                  screen.geometry().bottom()+1), screen.devicePixelRatio())
            x, y = centered(logical, self.width(), self.height(), available)
        elif tray_rect and tray_rect.isValid():
            x, y = centered(Rect(tray_rect.left(), tray_rect.top(), tray_rect.right()+1,
                                 tray_rect.bottom()+1), self.width(), self.height(), available)
        else:
            pos = QCursor.pos()
            x, y = centered(Rect(pos.x()-5, pos.y()-5, pos.x()+5, pos.y()+5),
                            self.width(), self.height(), available)
        self.move(x, y)

    def closeEvent(self,event):
        self.hide_to_tray();event.ignore()

    def on_first_run(self):
        if self.cfg.get('start_hidden',False) and ('--tray' in sys.argv):self.hide()
        if not self.cfg.get('installed') and not m.sub_dir(self.cfg,'zapret').is_dir():self.install_dialog()

    def install_dialog(self):
        if self.test_running or self.running:
            self.note('Сначала завершите текущую операцию.',True);return
        dialog=SetupDialog(self,self.cfg)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        path=dialog.path.text().strip()
        if not path:
            QMessageBox.warning(self,'Каталог','Укажите папку компонентов.');return
        destination=Path(path).expanduser().resolve()
        if (' ' in str(destination) or not str(destination).isascii()):
            if not self.confirm('Путь для Flowseal','Flowseal рекомендует папку без пробелов и кириллицы. Выбранный путь может не работать со стратегиями. Использовать его всё равно?'):
                return
        self.cfg['components_dir']=str(destination);m.write_cfg(self.cfg)
        selected=[]
        if dialog.z.isChecked(): selected.append('zapret')
        if dialog.w.isChecked(): selected.append('warp')
        if not selected:return
        if self.managed_pids or (self.telegram_proc and self.telegram_proc.poll() is None) or self.test_running:
            QMessageBox.warning(self,'Обновление','Сначала отключите запущенные компоненты NetPilot.');return
        # The wizard's checkbox is explicit consent to initiate the official
        # WARP installer; Windows' elevation prompts remain outside our control.
        def task():
            installed=[]
            failures=[]
            for i,kind in enumerate(selected):
                start=int(i*100/len(selected))
                end=int((i+1)*100/len(selected))
                self.bus.progress.emit(start, 'Подготовка ' + ('WARP' if kind=='warp' else 'Zapret') + '…')
                try:
                    if kind=='warp':
                        if m.warp_cli():
                            installed.append('WARP уже установлен')
                        else:
                            m.install_warp_silently()
                            installed.append('Cloudflare WARP')
                    else:
                        meta=m.release(kind)
                        m.install_component(self.cfg,meta,
                            lambda pct,txt,start=start,end=end:self.bus.progress.emit(
                                start+int((end-start)*pct/100),txt))
                        installed.append(f"Zapret {meta['tag']}")
                except Exception as exc:
                    failures.append(f'{kind}: {exc}')
                self.bus.progress.emit(end, 'Завершено: '+kind)
            return {'installed':installed,'failures':failures}
        self.job('install',task)

    def job(self,key,fn):
        if key in self.running:return
        self.running.add(key)
        if hasattr(self, 'tray'):
            self._update_tray()
        def call():
            try:self.bus.done.emit(key,fn(),'')
            except Exception as e:self.bus.done.emit(key,None,str(e))
        self.pool.submit(call)

    def progress_update(self,percent,text):
        self.test_progress.setVisible(True)
        self.test_percent.setVisible(True)
        self.test_progress.setValue(percent)
        self.test_percent.setText(f'{percent}%')
        self.test_details.setText(text)
        self.test_progress.setToolTip(text)
        if self.test_running:
            self.test_button.setText(f'Остановить подбор · {percent}%')
        self._update_tray()

    def completed(self,key,result,error):
        self.running.discard(key)
        if str(key).startswith('zapret_inventory:'):
            try:generation=int(str(key).split(':',1)[1])
            except ValueError:return
            if generation!=self._zapret_generation:return
            if error:self.apply_zapret_inventory_error(error)
            else:self.apply_zapret_snapshot(result,generation)
            self._set_zapret_actions_enabled(self._zapret_operation is None)
            return
        if error:
            if key=='autohealth':
                self.latest_auto_health={'error':error}
                self._update_tray();return
            if key=='warpstatus':
                self.warp_connected=False
                self.detail_facts['warp']['Подключение'].setText('Недоступно')
                self._update_tray();return
            if key=='test':self.test_running=False;self.cancel.clear();self.test_button.setText('Подобрать лучшую стратегию  ›');self.test_button.setEnabled(True);self._automatic_test=False
            if key=='warp':self.warp_pending=False
            self.note(('Служба Zapret: ' if key=='service' else f'{key}: ')+error,True)
            self.test_progress.setVisible(False)
            self.test_percent.setVisible(False)
            if key=='service':self.refresh_service_setting()
            self._update_tray()
            return
        if key=='test':
            was_automatic=self._automatic_test
            self.test_running=False;self.cancel.clear();self.test_button.setText('Подобрать лучшую стратегию  ›');self.test_button.setEnabled(True);self._automatic_test=False
            self.latest_test=result
            best=result.get('best')
            self.managed_pids=result.get('active_pids',[])
            if best and best.get('passed',0)>0:
                self.cfg['strategy']=best['strategy'];m.write_cfg(self.cfg)
                self.strategy_name.setText(best['strategy'].replace('general','').replace('.bat','').strip(' ()') or 'general')
                self.detail_facts['zapret']['Результат'].setText(f"{best['passed']} из {best['total']} адресов")
                self.note(('Автоподбор завершён: ' if was_automatic else 'Подбор завершён: ')+best['strategy'])
                if not was_automatic:self.offer_strategy_autostart(best['strategy'])
                if self.tray.isVisible():self.tray.showMessage('NetPilot',('Применена: ' if self.managed_pids else 'Подобрана: ')+best['strategy'],QSystemTrayIcon.MessageIcon.Information,5000)
            else:
                self.strategy_name.setText('Нет подтверждённых ответов')
                self.detail_facts['zapret']['Результат'].setText('Нет ответа')
                errors=[r.get('error') for r in result.get('results',[]) if r.get('error')]
                details=(errors[0][:120] if errors else 'Проверьте curl.exe, DNS и настройки сети.')
                self.note('Подбор не подтвердил доступность. '+details,True)
            self.test_progress.setValue(100);self.test_percent.setText('100%')
            QTimer.singleShot(1600, self._hide_completed_progress)
        elif key=='autohealth':
            self.latest_auto_health=result
            passed=int(result.get('passed',0));total=max(1,int(result.get('total',0)))
            if passed/total >= .75:
                self._health_failures=0
            else:
                self._health_failures+=1
                if self._health_failures>=2:
                    self._health_failures=0
                    service=m.service_state('zapret')
                    if service!='not_installed':
                        self.note('Связь ухудшилась. Автоподбор не меняет работающую службу Zapret без UAC.',True)
                    elif not b.is_admin():
                        self.note('Связь ухудшилась. Для автоматической смены стратегии запустите NetPilot от администратора.',True)
                    elif self.managed_pids:
                        QTimer.singleShot(0,lambda:self.start_test(True))
        elif key=='activate':
            self.managed_pids=result
            self.note('Zapret запущен до выхода из NetPilot')
        elif key=='warp':
            self.warp_pending=False;self.note(str(result)[:140]);self._last_warp=0
        elif key=='warpstatus':
            s=str(result).lower()
            self.warp_connected='connected' in s and 'disconnected' not in s
            self.detail_facts['warp']['Подключение'].setText('Подключено' if self.warp_connected else 'Отключено')
        elif key=='service':
            if result is False:self.note('Служба Zapret уже отсутствует')
            elif isinstance(result,str):self.note('Автозапуск Zapret включён · '+result)
            else:self.note('Автозапуск Zapret выключен')
            self.refresh_service_setting()
        elif key=='install':
            installed=result.get('installed',[]) if isinstance(result,dict) else result
            failures=result.get('failures',[]) if isinstance(result,dict) else []
            if installed:
                self.note('Готово: '+', '.join(installed))
            if failures:
                message='\n'.join(failures)
                # Networking issues should be actionable rather than exposing
                # only raw WinSock errors in the tiny status line.
                if '10013' in message:
                    message += ('\nWindows запретила сетевой доступ. Проверьте брандмауэр, '
                                'антивирус, прокси и разрешения NetPilot; затем повторите.')
                QMessageBox.warning(self,'Не всё удалось установить',message[:800])
            self.test_progress.setVisible(False);self.test_percent.setVisible(False)
        elif key=='updates':
            self.latest=result
            changed=[(k,v) for k,v in result.items()
                     if v.get('tag')!=self.cfg.get('installed',{}).get(k,{}).get('tag')]
            self.note('Новые версии: '+', '.join(f'{k}: {v["tag"]}' for k,v in changed) if changed else 'Все компоненты актуальны')
            if changed and self.cfg.get('auto_upgrade', False):
                if self.test_running or self.managed_pids or m.service_state('zapret')=='RUNNING' or (self.telegram_proc and self.telegram_proc.poll() is None):
                    self.note('Есть обновление. Оно отложено: сетевой компонент сейчас работает.')
                else:
                    def upgrade():
                        output=[]
                        for i,(kind,meta) in enumerate(changed):
                            m.install_component(self.cfg,meta,lambda p,t:self.bus.progress.emit(int((i*100+p)/len(changed)),t))
                            output.append(f'{kind}: {meta["tag"]}')
                        return output
                    self.job('install',upgrade)

        elif key=='ipset':self.note(f'IPSet обновлён: {result} строк')
        elif key=='stop':self.managed_pids=[];self.note('Zapret остановлен')
        self.refresh()

    def _hide_completed_progress(self):
        if not self.test_running and 'install' not in self.running:
            self.test_progress.setVisible(False)
            self.test_percent.setVisible(False)

    def refresh(self):
        z=m.sub_dir(self.cfg,'zapret');t=m.sub_dir(self.cfg,'telegram')/'TgWsProxy_windows.exe'
        # A preexisting Windows service is detected as running but is not owned by NetPilot.
        if self.managed_pids:
            try:
                import psutil
                self.managed_pids=[pid for pid in self.managed_pids if psutil.pid_exists(pid)]
            except Exception:pass
        zap_running=bool(self.managed_pids) or m.service_state('zapret')=='RUNNING'
        zap_label='Работает' if zap_running else 'Готов' if z.is_dir() else 'Не установлен'
        if self._zapret_inventory_ready:
            zap_running=bool(self.zapret_snapshot.active)
            zap_label=self._component_states.get('zapret',(zap_running,'Готов'))[1]
        tele_running=bool(self.telegram_proc and self.telegram_proc.poll() is None)
        states={
            'zapret':(zap_running,zap_label),
            'telegram':(tele_running,'Работает' if tele_running else 'Готов'),
            'warp':(self.warp_connected,'Подключён' if self.warp_connected else 'Готов' if m.warp_cli() else 'Не установлен'),
        }
        self._component_states = states
        for name,(active,label) in states.items():
            switch,detail=self.controls[name]
            if switch is not None:
                switch.blockSignals(True);switch.setChecked(active);switch.blockSignals(False)
            detail.setText(label)
            dot,status=self.detail_status_labels[name];dot.set_active(active);status.setText(label)
            self.home_summaries[name].set_state(active,label)
            tab_title={'zapret':'Zapret','telegram':'Telegram','warp':'WARP'}[name]
            self.detail_tabs[name].setText(tab_title+'\n'+('Выбран' if self.detail_tabs[name].isChecked() else label))
        active_count=sum(s[0] for s in states.values())
        busy=bool(self.running or self.test_running or self.warp_pending)
        error=time.monotonic()<getattr(self,'_tray_error_until',0)
        self.status_orb.set_state(active_count,busy,error)
        if error:
            self.home_title.setText('Требуется внимание')
            self.home_subtitle.setText('Откройте подробности и проверьте состояние')
        elif busy:
            self.home_title.setText('Проверяем соединение')
            self.home_subtitle.setText('NetPilot выполняет операцию автоматически')
        else:
            self.home_title.setText('Соединение в порядке')
            self.home_subtitle.setText('NetPilot следит за сетью автоматически')
        self.detail_facts['warp']['Подключение'].setText('Подключено' if self.warp_connected else 'Отключено')
        self._update_tray()
        self.refresh_service_setting()
        self.refresh_restore_autostart_action()
        self.refresh_zapret_inventory()
        if m.warp_cli() and not self.warp_pending and 'warpstatus' not in self.running:
            if not hasattr(self,'_last_warp') or time.monotonic()-self._last_warp>30:
                self._last_warp=time.monotonic();self.job('warpstatus',lambda:m.warp_action('status'))

    def toggle_zapret(self):
        if self.test_running:self.note('Подожди окончания теста',True);self.refresh();return
        if self.managed_pids:
            folder=m.sub_dir(self.cfg,'zapret');pids=list(self.managed_pids)
            self.job('stop',lambda:b.stop_managed_pids(pids,folder));return
        folder=m.sub_dir(self.cfg,'zapret')
        if not folder.exists():self.install_dialog();return
        if m.service_state('zapret')=='RUNNING':
            QMessageBox.information(self,'Служба Zapret','Zapret запущен как служба Windows. Для управления службой откройте расширенные настройки Flowseal. NetPilot не удаляет и не останавливает её автоматически.')
            self.refresh();return
        if not b.is_admin():
            if self.confirm('Нужны права администратора','Для WinDivert требуется UAC. Перезапустить NetPilot с правами администратора?'):
                self.elevate('--zapret')
            self.refresh();return
        try:b.preflight(folder)
        except Exception as exc:QMessageBox.warning(self,'Zapret',str(exc));self.refresh();return
        name=self.cfg.get('strategy','general.bat')
        if name not in m.list_strategies(folder):name=m.list_strategies(folder)[0]
        if self.confirm('Запустить Zapret',f'Включить {name}? Запуск не создаёт службу Windows.'):
            self.job('activate',lambda:[p.pid for p in b.launch_hidden(folder,folder/name)])
        self.refresh()

    def toggle_telegram(self):
        if self.telegram_proc and self.telegram_proc.poll() is None:
            try:
                tg.stop(self.telegram_proc)
                self.telegram_proc = None
                self.note('Прокси остановлен. Если Telegram остался без сети, отключите прокси в Telegram Desktop.')
            except Exception as exc:
                self.note('Не удалось остановить Telegram Proxy: ' + str(exc), True)
            self.refresh()
            return
        try:
            proc = tg.spawn(self.cfg)
            m.write_cfg(self.cfg)  # Persist the 32-character MTProto secret.
            self.telegram_proc = proc
            self.note('Подключаем Telegram Proxy…')
            self._tg_checks=0
            QTimer.singleShot(900, self.check_telegram_started)
        except Exception as exc:
            self.telegram_proc = None
            self.note('Telegram Proxy: ' + str(exc), True)
        self.refresh()

    def check_telegram_started(self):
        proc=self.telegram_proc
        if not proc:
            return
        if proc.poll() is not None:
            self.note('Telegram Proxy завершился. Проверьте telegram-worker-error.log.', True)
            self.telegram_proc=None
            self.refresh()
            return
        if tg.port_in_use():
            self.note('Telegram Proxy включён · Telegram Desktop подключается…')
            if self.cfg.get('telegram_auto_connect',True):
                # Official upstream uses tg://proxy; Telegram may ask confirmation.
                self.connect_telegram()
            self.refresh()
            return
        self._tg_checks=getattr(self,'_tg_checks',0)+1
        if self._tg_checks<9:
            QTimer.singleShot(700,self.check_telegram_started)
        else:
            self.note('Прокси не открыл порт 1443. Проверьте журнал.',True)

    def connect_telegram(self):
        from PySide6.QtCore import QUrl
        secret = tg.get_secret(self.cfg)
        m.write_cfg(self.cfg)
        if not QDesktopServices.openUrl(QUrl(tg.connect_url(secret))):
            self.note('Не удалось открыть Telegram Desktop. Проверьте установку Telegram.',True)

    def toggle_warp(self):
        if self.warp_pending:return
        if not m.warp_cli():
            if self.confirm('Установить WARP','Установить официальный Cloudflare WARP через winget? Windows может запросить UAC.'):
                self.warp_pending=True
                self.job('warp',m.install_warp_silently)
            self.refresh();return
        action='disconnect' if self.warp_connected else 'connect'
        if action=='connect' and self.managed_pids and not self.confirm('Одновременные подключения',
            'WARP и Zapret могут конфликтовать. Продолжить подключение WARP?'):
            self.refresh();return
        self.warp_pending=True
        self.job('warp',lambda:m.warp_action(action));self.refresh()

    def start_test(self, automatic=False):
        automatic=bool(automatic)
        if self.test_running:
            if automatic:return
            self.cancel.set();self.test_button.setEnabled(False);self.test_details.setText('Останавливаем проверку…');return
        if self.running:
            if not automatic:self.note('Сначала дождитесь завершения другой операции.',True)
            return
        folder=m.sub_dir(self.cfg,'zapret')
        if not folder.is_dir():
            if not automatic:self.install_dialog()
            return
        if self.warp_connected:
            if not automatic:QMessageBox.warning(self,'WARP','Перед сравнением стратегий отключите WARP, иначе оценка будет некорректной.')
            return
        if not b.is_admin():
            if automatic:return
            if self.confirm('Тестирование','Для запуска WinDivert требуются права администратора. Перезапустить NetPilot с UAC?'):
                self.elevate('--test')
            return
        try:
            catalog=load_catalog(folder)
            files=m.list_strategies(folder)
            if not files:raise RuntimeError('Стратегии general*.bat не найдены')
            if m.service_state('zapret')!='not_installed':
                raise RuntimeError('Обнаружена служба Zapret. Для теста отключите её в Flowseal.')
            import psutil
            owned=set(self.managed_pids)
            external=[p.pid for p in psutil.process_iter(['name'])
                      if (p.info.get('name') or '').lower()=='winws.exe' and p.pid not in owned]
            if external:raise RuntimeError('Есть другой процесс winws.exe. NetPilot не будет его останавливать.')
        except Exception as exc:
            if automatic:self.note('Автоподбор пропущен: '+str(exc),True)
            else:QMessageBox.warning(self,'Тестирование',str(exc))
            return
        checks=len(files)*len(catalog.hosts)
        if not automatic and not self.confirm('Подбор стратегии',f'{len(files)} стратегий, {len(catalog.hosts)} доменов, всего {checks} HTTPS-запросов. Проверка может длиться долго и временно влиять на сеть. Начать?'):
            return
        self.cancel.clear();self.test_running=True;self._automatic_test=automatic
        self.test_progress.setVisible(True);self.test_percent.setVisible(True);self.test_progress.setValue(0);self.test_percent.setText('0%')
        self.test_button.setText('Остановить подбор · 0%');self.test_button.setEnabled(True)
        self.test_progress.setToolTip('Автоматически сравниваем стратегии…' if automatic else 'Сравниваем стратегии и выбираем лучшую…')
        # A successful benchmark always applies its chosen configuration.
        # Legacy 'auto_apply' settings must not silently disable this.
        apply=True
        previously_owned=list(self.managed_pids)
        previous_name=self.cfg.get('strategy','general.bat')
        self.job('test',lambda:b.benchmark(folder,lambda n,s:self.bus.progress.emit(n,s),self.cancel,
            keep_best=apply,owned_pids=previously_owned,previous_strategy=previous_name))

    def elevate(self,flag='--elevated'):
        if os.name!='nt':return
        if getattr(sys,'frozen',False): exe=sys.executable;args=flag
        else:exe=sys.executable;args=f'"{Path(__file__).resolve()}" {flag}'
        ok=ctypes.windll.shell32.ShellExecuteW(None,'runas',exe,args,None,1)
        if ok<=32:QMessageBox.warning(self,'Windows','Не удалось запросить права администратора.')
        else:self.quit()

    def auto_strategy_check(self):
        """Monitor an active Zapret connection without disturbing its process."""
        if not self.cfg.get('auto_strategy',True) or self.test_running or self.running or self.warp_connected:
            return
        folder=m.sub_dir(self.cfg,'zapret')
        if not folder.is_dir():return
        service_running=m.service_state('zapret')=='RUNNING'
        if not self.managed_pids and not service_running:return
        self.job('autohealth',lambda:b.quick_health(folder))

    def check_updates(self, manual=False):
        if not manual and not self.cfg.get('auto_check',True):return
        if 'updates' in self.running or self.test_running:return
        self.job('updates',lambda:{kind:m.release(kind) for kind in ('zapret',)})

    def rollback(self,kind):
        if self.test_running or self.managed_pids or (self.telegram_proc and self.telegram_proc.poll() is None):
            self.note('Сначала остановите компоненты.',True);return
        component='Zapret' if kind=='zapret' else kind
        message=(f'Вернуть {component} к версии, которая была установлена до последнего обновления?\n\n'
                 'Текущая версия будет заменена резервной копией. Пользовательские списки из той копии также вернутся.')
        if self.confirm('Вернуть предыдущую версию',message):
            try:m.rollback_component(self.cfg,kind);self.note(f'{component}: предыдущая версия восстановлена')
            except FileNotFoundError:
                self.note(f'{component}: резервной версии нет. Она появится после первого обновления.',True)
            except Exception as e:self.note(str(e),True)

    def expert(self):
        d=ExpertDialog(self);d.exec();self.refresh()

    def diagnostics(self):
        info='\n'.join(f'{n}: {m.service_state(n)}' for n in ('zapret','WinDivert','WinDivert14','BFE'))
        QMessageBox.information(self,'Диагностика Windows',info)

    def load_last_test(self):
        try:
            data=json.loads((m.ROOT/'benchmark-last.json').read_text(encoding='utf-8'))
            best=data.get('best')
            if best and best.get('passed',0)>0:
                self.strategy_name.setText(best['strategy'].replace('general','').replace('.bat','').strip(' ()') or 'general')
                self.detail_facts['zapret']['Результат'].setText(f"{best['passed']} из {best['total']} адресов")
        except (OSError, ValueError, TypeError, KeyError):pass

    def quit(self):
        if self.test_running:
            QMessageBox.information(self,'NetPilot','Сначала остановите тестирование.');return
        if self.managed_pids:
            if self.confirm('Завершить NetPilot','Остановить запущенную NetPilot стратегию Zapret перед выходом?'):
                b.stop_managed_pids(self.managed_pids,m.sub_dir(self.cfg,'zapret'))
        if self.telegram_proc and self.telegram_proc.poll() is None:
            try: tg.stop(self.telegram_proc)
            except (OSError, subprocess.SubprocessError) as exc:
                self.note('Telegram Proxy: ' + str(exc), True)
        self.auto_strategy_timer.stop()
        self.message_timer.stop()
        self.window_transition.finish_immediately()
        self.tray.hide()
        self.pool.shutdown(wait=False,cancel_futures=True)
        QApplication.instance().quit()


def run_packaged_smoke(application, window):
    """Exercise packaged UI surfaces without reading or mutating system state."""
    if not window.smoke_mode:
        raise ValueError('packaged smoke requires an isolated NetPilot window')
    window.window_transition.reduced_motion=True
    window.show_panel();application.processEvents()
    if window.size().toTuple()!=(324,414) or not window.tray.isVisible():
        raise RuntimeError('compact window or single tray surface was not created')
    window.open_details();window.select_detail(0);application.processEvents()
    for icon,_label in window.detail_status_labels.values():
        if icon.render_pixmap(24,1.0).isNull():
            raise RuntimeError('vector icon did not render')
    window.show_page(2);application.processEvents()
    window.hide_to_tray();application.processEvents()
    if window.isVisible():raise RuntimeError('window did not minimize to tray')
    window.show_panel();application.processEvents()
    if not window.isVisible():raise RuntimeError('window did not restore from tray')
    return 0


def main():
    app=QApplication(sys.argv)
    app.setApplicationName('NetPilot');app.setQuitOnLastWindowClosed(False);app.setWindowIcon(app_icon());app.setStyleSheet(CSS)
    smoke='--smoke-test' in sys.argv
    window=NetPilot(smoke_mode=smoke)
    if smoke:
        try:return run_packaged_smoke(app,window)
        except Exception:return 3
        finally:
            window.window_transition.finish_immediately()
            window.tray.hide();window.hide();window.pool.shutdown(wait=False,cancel_futures=True)
            window.deleteLater();app.processEvents()
    if '--tray' not in sys.argv and not ('--elevated' in sys.argv and window.cfg.get('start_hidden')):
        window.show_panel()
    if '--test' in sys.argv:QTimer.singleShot(1100,window.start_test)
    if '--zapret' in sys.argv:QTimer.singleShot(900,window.toggle_zapret)
    if '--install-zapret-service' in sys.argv:QTimer.singleShot(700,lambda:window.apply_zapret_service(True))
    if '--remove-zapret-service' in sys.argv:QTimer.singleShot(700,lambda:window.apply_zapret_service(False))
    return app.exec()

if __name__=='__main__': sys.exit(main())
