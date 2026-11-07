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


_FROZEN_DLL_DIR_HANDLES = []
_FROZEN_DLL_DIAGNOSTICS = []


def _load_frozen_bridge(library):
    loader = ctypes.windll.kernel32.LoadLibraryExW
    loader.argtypes = (ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32)
    loader.restype = ctypes.c_void_p
    # Resolve the target directory, AddDllDirectory entries and the normal
    # Windows runtime directories for bridge dependencies.
    return loader(str(library), None, 0x00000100 | 0x00000400 | 0x00001000)


def _configure_frozen_qt_paths():
    """Keep Qt and Shiboken sibling DLL directories visible to Windows."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    dll_directories = (root, root / 'shiboken6', root / 'PySide6')
    existing_directories = [directory for directory in dll_directories if directory.is_dir()]
    if existing_directories:
        os.environ['PATH'] = os.pathsep.join(
            [*(str(directory) for directory in existing_directories), os.environ.get('PATH', '')])
    for directory in dll_directories:
        if not directory.is_dir():
            continue
        try:
            _FROZEN_DLL_DIR_HANDLES.append(os.add_dll_directory(str(directory)))
        except (AttributeError, OSError) as exc:
            _FROZEN_DLL_DIAGNOSTICS.append(f'directory {directory}: {exc!r}')
    # Some PyInstaller/Windows combinations do not resolve the sibling
    # Shiboken runtime while importing QtCore even after both directories
    # are registered. Loading the two stable bridge DLLs first keeps the
    # regular PySide import deterministic; failures still surface normally.
    for library in (root / 'icuuc.dll',
                    root / 'PySide6' / 'Qt6Core.dll',
                    root / 'shiboken6' / 'shiboken6.abi3.dll',
                    root / 'PySide6' / 'pyside6.abi3.dll'):
        if not library.is_file():
            continue
        try:
            handle = _load_frozen_bridge(library)
            if not handle:
                raise OSError(ctypes.get_last_error(), f'cannot load {library}')
            _FROZEN_DLL_DIR_HANDLES.append(handle)
        except (AttributeError, OSError) as exc:
            _FROZEN_DLL_DIAGNOSTICS.append(f'library {library}: {exc!r}')
    diagnostic_path = os.environ.get('NETPILOT_DLL_DEBUG')
    if diagnostic_path:
        Path(diagnostic_path).write_text(
            f'root={root}\n' + '\n'.join(_FROZEN_DLL_DIAGNOSTICS), encoding='utf-8')


_configure_frozen_qt_paths()

# Privileged Flowseal service operations are dispatched in a one-shot child
# before Qt starts; the upstream interactive menu is not launched. Dispatch before loading Qt so child startup stays lightweight.
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

from PySide6.QtCore import Qt, QObject, Signal, QTimer, QPoint, QPointF, QEventLoop, QEvent
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QBrush, QCursor, QDesktopServices, QPixmap, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget, QFrame, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QProgressBar, QScrollArea, QComboBox,
    QCheckBox, QRadioButton, QSystemTrayIcon, QMenu, QFileDialog, QMessageBox as NativeMessageBox, QLineEdit, QPlainTextEdit,
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
from ui_icons import (AnimatedIcon, ComponentTabButton, IconKind, IconState,
                      IconTextButton, PremiumIconButton, paint_brand_symbol)
from ui_motion import AnimatedToggle, WindowTransition
from ui_theme import ACCENT as THEME_ACCENT, ACCENT_HOVER as THEME_ACCENT_BRIGHT
from traffic import TrafficSampler
from ui_traffic import TrafficMiniPanel
from event_journal import EventJournal, safe_text
from operation_policy import has_foreground_work, humanize_zapret_error

VERSION = '0.5.4-rc11'
ACCENT = THEME_ACCENT
ACCENT_BRIGHT = THEME_ACCENT_BRIGHT
WARNING = '#F2BD62'
ERROR = '#FF6B6B'
CSS = '''
*{font-family:'Segoe UI';font-size:12px;color:#F2F6F0}
QMainWindow{background:transparent}
QDialog{background:#0F1F17}
QWidget#root{background:#0B1711;border:1px solid #3E5948;border-radius:17px}
QFrame#surface{background:#14251B;border:1px solid #3E5948;border-radius:15px}
QFrame#row{background:#17291F;border:1px solid #334D3D;border-radius:11px}
QFrame#row:hover{border-color:#658D70;background:#20382A}
QFrame#detailPanel{background:#14271D;border:1px solid #496653;border-radius:15px}
QFrame#trafficMiniPanel{background:#1A3426;border:1px solid #385543;border-radius:9px}
QLabel#trafficValue{color:#BAF0C5;font-size:9px;font-weight:650}
QFrame#noticeCard{background:#192C20;border:1px solid #44644E;border-radius:12px}
QFrame#noticeCardError{background:#241316;border:1px solid #A3474A;border-radius:12px}
QFrame#separator{background:#355344;min-width:1px;max-width:1px}
QLabel#brand{font-size:19px;font-weight:800;letter-spacing:0.3px}
QLabel#heading{font-size:15px;font-weight:750}
QLabel#heroTitle{font-size:21px;font-weight:750}
QLabel#heroSubtitle{font-size:13px;color:#AABCA9}
QLabel#componentName{font-size:14px;font-weight:700}
QLabel#detailName{font-size:16px;font-weight:750}
QLabel#sectionHint{font-size:10px;color:#9CAC9D}
QFrame#settingsGroup{background:#16291F;border:1px solid #335442;border-radius:12px}
QFrame#settingsSidebar{background:#101E16;border:1px solid #304D39;border-radius:12px}
QPushButton#settingsTab{border:1px solid transparent;background:transparent;border-radius:9px;padding:7px 8px;font-size:11px;text-align:left;color:#C7CBB8}
QPushButton#settingsTab:checked{background:#284A33;border:1px solid #7DE7A1;color:#F0FFF1}
QPushButton#settingsTab:hover{background:#223B2C;border-color:#476A50}
QFrame#progressCard{background:#182C21;border:1px solid #426A4A;border-radius:10px}
QLabel#noticeTitle{font-size:11px;font-weight:750;color:#F2F6F0}
QLabel#noticeBody{font-size:10px;color:#D9E5D8}
QToolButton#noticeClose{background:transparent;border:1px solid transparent;border-radius:6px;color:#D9E5D8;font-size:19px;padding:0px}
QToolButton#noticeClose:hover{background:#3C292C;border-color:#A3474A;color:#FFFFFF}
QLabel#factValue{font-size:12px;color:#F2F6F0;font-weight:650}
QLabel#muted{font-size:11px;color:#AABCA9}
QLabel#small{font-size:10px;color:#C7CBB8}
QLabel#eyebrow{font-size:10px;color:#98D69F;font-weight:750}
QLabel#status{color:#AABCA9;font-size:11px}
QLabel#success{color:#99F5AF;font-size:10px;font-weight:700;background:#244631;border:1px solid #456A50;border-radius:7px;padding:3px 7px}
QLabel#warn{color:#F2C97F;font-size:10px;font-weight:700;background:#302719;border:1px solid #554429;border-radius:7px;padding:3px 7px}
QLabel#error{color:#FF8585;font-size:11px}
QPushButton{border:1px solid #365743;background:#20392B;border-radius:10px;padding:9px 13px;font-weight:600}
QPushButton:hover{background:#294837;border-color:#638269}
QPushButton:pressed{background:#355543}
QPushButton:disabled{color:#69736F;background:#171C20;border-color:#294234}
QPushButton#primary{background:#7DE7A1;border:1px solid #99F5AF;color:#07110C;font-weight:750}
QPushButton#primary:hover{background:#7CEAB2}
QPushButton#link{background:transparent;border:none;color:#7DE7A1;font-size:12px;font-weight:700;padding:3px 1px;text-align:left}
QPushButton#link:hover{color:#99F5AF}
QPushButton#details{background:transparent;border:none;color:#7DE7A1;font-size:15px;font-weight:700;padding:8px}
QPushButton#details:hover{color:#99F5AF}
QPushButton#componentTab{background:transparent;border:none;border-bottom:2px solid transparent;border-radius:0;padding:7px 4px;color:#AABCA9;font-size:11px;font-weight:650}
QPushButton#componentTab:checked{color:#F2F6F0;border-bottom:2px solid #7DE7A1}
QPushButton#componentTab:hover{color:#DCE5E1;background:transparent}
QPushButton#icon{background:#1D3426;border:1px solid #2D373E;min-width:27px;min-height:26px;padding:5px;font-size:18px;font-weight:800}
QFrame#dialogCard{background:#172C20;border:1px solid #385047;border-radius:17px}
QLabel#dialogTitle{font-size:15px;font-weight:750}
QPushButton#dialogSecondary{background:#20392B;border:1px solid #354139}
QPushButton#dialogSecondary:hover{background:#29342F}
QWidget#dialogShade{background:rgba(5,5,9,188);border-radius:17px}
QPushButton#quiet{background:transparent;border:1px solid #304C3A;color:#B3BDB9}
QPushButton#quiet:hover{background:#264030}
QPushButton#settingsAction{background:transparent;border:none;text-align:left;font-size:11px;padding:3px 2px;color:#E8F1E5}
QPushButton#settingsAction:hover{color:#7DE7A1}
QPushButton#danger{color:#FF9A9A;border:1px solid #54323B;padding:5px 9px;font-size:12px}
QProgressBar{background:#304636;border:none;border-radius:4px;min-height:7px;max-height:7px}
QProgressBar::chunk{background:#7DE7A1;border-radius:4px}
QComboBox,QLineEdit{background:#1C3226;border:1px solid #405B48;border-radius:8px;padding:7px;min-height:21px}
QComboBox QAbstractItemView{background:#172C20;selection-background-color:#286348;outline:none}
QScrollArea,QScrollArea>QWidget>QWidget{border:none;background:transparent}
QScrollBar:vertical{background:transparent;width:6px;margin:3px}
QScrollBar::handle:vertical{background:#5F7767;border-radius:3px;min-height:30px}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}
QToolTip{background:#1C3226;color:white;border:1px solid #3B4A45;padding:6px}
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
        self.card.setMinimumWidth(min(330, max(230, owner.centralWidget().width() - 36)))
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
            if len(str(message)) > 240 or str(message).count('\n') > 4:
                scroller = QScrollArea()
                scroller.setWidgetResizable(True)
                scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                scroller.setMaximumHeight(max(125, owner.height()-210))
                scroller.setWidget(body)
                col.addWidget(scroller)
            else:
                col.addWidget(body)
            actions = QHBoxLayout()
            actions.setSpacing(9)
            if question:
                cancel = QPushButton('Отмена')
                cancel.setMinimumWidth(112)
                cancel.setMinimumHeight(36)
                cancel.setObjectName('dialogSecondary')
                cancel.clicked.connect(lambda: self.finish(False))
                actions.addWidget(cancel)
            else:
                actions.addStretch()
            go = QPushButton('Подтвердить' if question else 'Понятно')
            go.setMinimumWidth(112)
            go.setMinimumHeight(36)
            go.setObjectName('primary')
            go.clicked.connect(lambda: self.finish(True))
            actions.addWidget(go)
            col.addLayout(actions)
        # Center the modal horizontally. A capped maximum width alone makes
        # QVBoxLayout pin a narrow card to the left edge of a wide settings view.
        layout.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter)
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
            owner.note(str(message), error=True, title=str(title))
        return NativeMessageBox.StandardButton.Ok

    @staticmethod
    def information(parent, title, message):
        owner = QMessageBox._owner(parent)
        if owner:
            if title in {'Службы Windows', 'Диагностика Windows'}:
                # Detailed reports requested by the user stay readable in a
                # scrollable modal, unlike short unsolicited notifications.
                owner.inline_notice(title, message)
            else:
                owner.note(str(message), error=False, title=str(title))
        return NativeMessageBox.StandardButton.Ok

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


class StrategyPicker(QFrame):
    """Inline, user-controlled strategy selection. Selecting never silently starts WinDivert."""
    def __init__(self, owner, choices, selected):
        super().__init__(owner)
        self.selected=None
        self._options=[]
        self.setFixedWidth(min(292,owner.width()-48))
        outer=QVBoxLayout(self);outer.setContentsMargins(9,9,9,9);outer.setSpacing(9)
        header=QLabel('Выбор стратегии Zapret');header.setObjectName('heading');outer.addWidget(header)
        hint=QLabel('Выберите стратегию. После выбора подтвердите запуск службы Flowseal вместе с Windows.')
        hint.setWordWrap(True);hint.setObjectName('muted');outer.addWidget(hint)
        area=QScrollArea();area.setWidgetResizable(True);area.setMaximumHeight(210)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane=QWidget();rows=QVBoxLayout(pane);rows.setSpacing(4);rows.setContentsMargins(2,2,8,2)
        for name in choices:
            radio=QRadioButton(name)
            radio.setChecked(name==selected)
            radio.setToolTip(name)
            rows.addWidget(radio);self._options.append((name,radio))
        area.setWidget(pane);outer.addWidget(area)
        buttons=QHBoxLayout();buttons.setSpacing(6)
        cancel=QPushButton('Отмена');cancel.clicked.connect(lambda:self._active_modal.finish(False))
        apply=QPushButton('Выбрать');apply.setObjectName('primary');apply.clicked.connect(self.apply)
        buttons.addWidget(cancel);buttons.addWidget(apply);outer.addLayout(buttons)

    def apply(self):
        self.selected=next((name for name,radio in self._options if radio.isChecked()),None)
        self._active_modal.finish(self.selected is not None)



class HeaderAction(PremiumIconButton):
    """Consistent vector navigation icons, independent of platform glyph fonts."""
    def __init__(self, kind, parent=None):
        icon={'back':IconKind.BACK,'settings':IconKind.GEAR,
              'minimize':IconKind.MINIMIZE}[kind]
        label={'back':'Назад','settings':'Настройки','minimize':'Свернуть в трей'}[kind]
        super().__init__(icon,label,34,parent,visual_size=30,icon_size=16,
                         corner_radius=9,idle_glow=False)
        self.action_kind=kind

    def set_kind(self, kind):
        self.action_kind = kind
        self.setToolTip({'back':'Назад', 'settings':'Настройки', 'minimize':'Свернуть в трей'}[kind])
        self.setAccessibleName(self.toolTip())
        self.kind={'back':IconKind.BACK,'settings':IconKind.GEAR,
                   'minimize':IconKind.MINIMIZE}[kind]
        self.icon_view.kind=self.kind
        self.icon_view.update();self.update()


class StatusOrb(QWidget):
    """Large, quiet health signal from the approved overview design."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_count = 0
        self.busy = False
        self.error = False
        self.setFixedSize(86, 86)
        self.icon_view=AnimatedIcon(IconKind.ACTIVITY,52,self)
        self.icon_view.setGeometry(17,17,52,52)
        self.icon_view.set_state(IconState.DISABLED,animate=False)

    def set_state(self, active_count, busy=False, error=False):
        self.active_count = int(active_count)
        self.busy = bool(busy)
        self.error = bool(error)
        if self.error:
            self.icon_view.kind=IconKind.WARNING;state=IconState.ERROR
        elif self.busy:
            self.icon_view.kind=IconKind.REFRESH;state=IconState.LOADING
        elif self.active_count:
            self.icon_view.kind=IconKind.SUCCESS;state=IconState.SUCCESS
        else:
            self.icon_view.kind=IconKind.ACTIVITY;state=IconState.DISABLED
        self.icon_view.set_state(state)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(ERROR if self.error else WARNING if self.busy else ACCENT if self.active_count else '#819684')
        glow=QColor(color);glow.setAlpha(28)
        painter.setPen(QPen(glow,7));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawEllipse(9,9,68,68)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(5,5,76,76);inner=QColor(color);inner.setAlpha(90)
        painter.setPen(QPen(inner,1));painter.drawEllipse(13,13,60,60)
        painter.end()


class SummaryItem(QWidget):
    def __init__(self, name, kind, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.dot = AnimatedIcon({'zapret':IconKind.SHIELD,'telegram':IconKind.TELEGRAM,
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
    """Branded network route glyph (same geometry as the header)."""
    pm=QPixmap(64,64)
    pm.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(64/24,64/24)
    painter.setBrush(QColor('#10251C'))
    painter.setPen(QPen(QColor('#7DE7A1'),1.6))
    paint_brand_symbol(painter)
    status=ERROR if error else WARNING if busy else ACCENT if active_count else '#96AC99'
    painter.setPen(QPen(QColor('#10251C'),1.0))
    painter.setBrush(QColor(status))
    painter.drawEllipse(QPointF(19.4,19.4),2.1,2.1)
    painter.end()
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
        self.setFixedWidth(min(300, parent.width()-40))
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
        self.setFixedSize(278, min(308, owner.height()-48))
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
        hint = QLabel('Изменения настроек Flowseal требуют повторного запуска стратегии.\nДополнительные upstream-тесты и замена fake-файлов доступны в оригинальном service.bat. Обычный запуск службы выполняется через NetPilot.')
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
        dialog.setFixedSize(min(300,self.owner.width()-52),min(375,self.owner.height()-80))
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
        if not self.smoke_mode:
            # An elevated helper can finish after the UI exits. The upstream
            # registry marker is the authoritative installed strategy, not an
            # unconfirmed local picker value.
            upstream = m.zapret_service_strategy()
            folder = m.sub_dir(self.cfg, 'zapret')
            installed = upstream + '.bat' if upstream else ''
            if installed and installed in m.list_strategies(folder):
                self.cfg['strategy'] = installed
        self.event_journal = EventJournal(None if self.smoke_mode else m.ROOT/'event-history.json')
        self._last_system_notice = None
        self._last_system_notice_at = 0.0
        self.bus = Bus(); self.bus.done.connect(self.completed); self.bus.progress.connect(self.progress_update)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='NetPilotWorker')
        self.running = set(); self.test_running = False; self.cancel = threading.Event()
        self.managed_pids = []; self.telegram_proc = None; self.warp_connected = False
        self._component_states = {}
        self._overflow_rect = None
        self._panel_positioned = False
        self.warp_pending = False; self.latest = {}; self.latest_test = None
        self.telegram_pending = False
        self.telegram_ready = False
        self._warp_verify_pending = False
        self.traffic_sampler = TrafficSampler()
        self._health_failures = 0; self.latest_auto_health = None; self._automatic_test = False
        self.suppress_toggle = False
        self.zapret_snapshot = ZapretSnapshot()
        self.zapret_semantic_state = 'disabled'
        self._zapret_inventory_ready = False
        self._zapret_generation = 0
        self._zapret_operation_generation = 0
        self._zapret_operation = None
        self._prompted_strategies = set()
        self._pending_stop_verification=None
        # Resume a consented manual benchmark only after privileged external-stop
        # completion has been verified against a fresh Windows inventory.
        self._resume_test_after_external_stop=False
        # A requested elevation resumes with a *fresh* administrator inventory
        # and still requires an explicit confirmation before transfer.
        self._resume_transfer_after_elevation = '--retry-zapret-transfer' in sys.argv
        self.setWindowTitle('NetPilot'); self.setWindowIcon(app_icon())
        # Small frameless flyout like Volty. The shell notification overflow is
        # measured for centering, not guessed from the cursor or screen edge.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Only two fixed window sizes: 0/1 overview and details share one
        # footprint; settings are wider, scrollable and never resize on events.
        # Stable size across all three component tabs, without 70px of empty card.
        self._base_size = (400, 354)
        self._settings_size = (485, 526)
        self.setFixedSize(*self._base_size)
        self._build()
        # The approved primary surface is the compact component detail view.
        # The overview remains reachable through Back.
        self._return_page=1
        self.show_page(1)
        self.window_transition=WindowTransition(self)
        self._update_compact_height()
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
        self.tray.messageClicked.connect(self.open_notification_details)
        self.tray.show()
        # Passive OS network counters: no speedtest, no extra internet traffic.
        # Poll only when the details window is open on Telegram or WARP.
        self.traffic_timer=QTimer(self)
        self.traffic_timer.setInterval(1250)
        self.traffic_timer.timeout.connect(self._update_traffic)
        self.traffic_timer.start()
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
        box = QVBoxLayout(root); box.setContentsMargins(13, 11, 13, 11); box.setSpacing(8)
        header = QHBoxLayout();header.setSpacing(7);self.header_layout=header;box.addLayout(header)
        self.back_btn = HeaderAction('back');self.back_btn.clicked.connect(self._navigate_back)
        self.back_btn.setVisible(False); header.addWidget(self.back_btn)
        self.brand_icon=AnimatedIcon(IconKind.BRAND,28);self.brand_icon.set_state(IconState.ENABLED,animate=False)
        header.addWidget(self.brand_icon)
        title = QLabel('NetPilot'); title.setObjectName('brand'); header.addWidget(title)
        header.addStretch()
        self.settings_btn = HeaderAction('settings')
        self.settings_btn.clicked.connect(self.toggle_page); header.addWidget(self.settings_btn)
        self.hide_btn = HeaderAction('minimize')
        self.hide_btn.clicked.connect(self.hide_to_tray); header.addWidget(self.hide_btn)
        self.pages = QStackedWidget()
        # A QStackedWidget normally reports the largest size hint of *all*
        # pages, including the hidden settings screen.  The outer window is a
        # small flyout: let the current page dictate its height instead.
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.pages.setMinimumHeight(0)
        box.addWidget(self.pages, 0)
        self._overview(); self._details(); self._settings()
        # Notifications are OS-level; no banner is inserted into this layout.
        self.message_timer=QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self._hide_message)
        # Any unavoidable spare space goes after the visible content. The
        # notification is a dismissible row *below* the page, never an overlay.
        box.addStretch(1)

    def _bottom_row(self, parent):
        footer=QWidget();bottom=QHBoxLayout(footer);bottom.setContentsMargins(0,0,0,0);parent.addWidget(footer)
        button=IconTextButton(IconKind.COMPONENTS,'Компоненты')
        # The painted icon consumes 22 px plus padding; reserve enough width
        # for the *full* Russian caption even at non-default Windows DPI.
        button.setMinimumWidth(max(button.minimumWidth(), 138))
        button.setToolTip('Открыть компоненты и обслуживание')
        button.clicked.connect(self.show_components);bottom.addWidget(button)
        bottom.addStretch()
        about=QLabel('v'+VERSION.upper().replace('-RC',' RC'));about.setObjectName('small');bottom.addWidget(about)
        return button,footer

    def _overview(self):
        page = QWidget(); self.pages.addWidget(page)
        content = QVBoxLayout(page); content.setContentsMargins(0, 6, 0, 0); content.setSpacing(5)
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
        content.addSpacing(16)  # Distinct action, not glued to service statuses.
        self.details_button=IconTextButton(IconKind.CHEVRON,'Подробности',accented=True,trailing=True)
        self.details_button.setFixedSize(160,38)
        self.details_button.clicked.connect(self.open_details);content.addWidget(self.details_button,0,Qt.AlignmentFlag.AlignHCenter)
        # Keep the icon/title/status group a little higher and give the
        # Details action its own breathing space, without changing geometry.
        # Extra height must remain below the entire group, not between
        # status labels and the action button.
        content.addStretch(1)

    def _fact_row(self, parent, title, value):
        row=QHBoxLayout();row.setSpacing(8);parent.addLayout(row)
        label=QLabel(title);label.setObjectName('muted');row.addWidget(label,1)
        result=QLabel(value);result.setObjectName('factValue');result.setAlignment(Qt.AlignmentFlag.AlignRight);row.addWidget(result)
        return result

    def _details(self):
        page=QWidget();self.pages.addWidget(page)
        content=QVBoxLayout(page);content.setContentsMargins(0,6,0,0);content.setSpacing(8)
        heading=QLabel('Подробности');heading.setObjectName('heading');content.addWidget(heading)
        tabs=QHBoxLayout();tabs.setSpacing(4);content.addLayout(tabs)
        self.detail_tabs={}
        for index,(key,title) in enumerate((('zapret','Zapret'),('telegram','Telegram'),('warp','WARP'))):
            button=ComponentTabButton({'zapret':IconKind.SHIELD,'telegram':IconKind.TELEGRAM,
                                       'warp':IconKind.CLOUD}[key],title,'Готов')
            button.clicked.connect(lambda checked=False,i=index:self.select_detail(i));tabs.addWidget(button,1)
            self.detail_tabs[key]=button
            if index<2:
                divider=QFrame(objectName='separator');divider.setFixedHeight(38);tabs.addWidget(divider)
        self.detail_stack=QStackedWidget();self.detail_stack.setFixedHeight(184);content.addWidget(self.detail_stack)
        self.controls={};self.detail_status_labels={};self.detail_facts={}
        specs={
            'zapret':(('Стратегия','Не выбрана'),('Результат','Нет проверки')),
            'telegram':(('Прокси','127.0.0.1:1443'),('Подключение','Автоматически')),
            'warp':(('Клиент','Cloudflare WARP'),('Подключение','Отключено')),
        }
        handlers={'telegram':self.toggle_telegram,'warp':self.toggle_warp}
        for key,title in (('zapret','Zapret'),('telegram','Telegram'),('warp','WARP')):
            panel=QFrame(objectName='detailPanel');self.detail_stack.addWidget(panel)
            wrap=QVBoxLayout(panel);wrap.setContentsMargins(13,10,13,10);wrap.setSpacing(4)
            head=QHBoxLayout()
            head.setSpacing(7)
            wrap.addLayout(head)
            dot=AnimatedIcon({'zapret':IconKind.SHIELD,'telegram':IconKind.TELEGRAM,
                              'warp':IconKind.CLOUD}[key],25)
            head.addWidget(dot)
            names=QVBoxLayout();names.setSpacing(0)
            head.addLayout(names,1)
            name=QLabel(title);name.setObjectName('detailName');name.setMinimumWidth(0);names.addWidget(name)
            state=QLabel('Готов');state.setObjectName('muted');state.setWordWrap(True)
            state.setMinimumWidth(0);names.addWidget(state)
            self.controls[key]=(None,state);self.detail_status_labels[key]=(dot,state)
            facts={}
            if key=='zapret':
                # Reference layout: status on the left; strategy and test on the
                # right. No redundant "Запуск сервиса" occupying a whole row.
                divider=QFrame(objectName='separator');divider.setFixedHeight(56)
                head.addWidget(divider)
                side=QVBoxLayout();side.setSpacing(5)
                head.addLayout(side,2)
                for title,value in specs[key]:
                    line=QVBoxLayout();line.setSpacing(0)
                    label=QLabel(title);label.setObjectName('small');line.addWidget(label)
                    result=QLabel(value);result.setObjectName('factValue')
                    result.setMinimumWidth(0)
                    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    result.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
                    result.setToolTip(value)
                    line.addWidget(result)
                    side.addLayout(line)
                    facts[title]=result
                strategy_arrow=QToolButton()
                strategy_arrow.setText('›')
                strategy_arrow.setFixedSize(23,33)
                strategy_arrow.setStyleSheet('QToolButton{border:none;background:transparent;color:#A9E5AF;font-size:19px;} QToolButton:hover{color:#E0FFE6;}')
                strategy_arrow.setToolTip('Выбрать стратегию вручную')
                strategy_arrow.clicked.connect(self.choose_strategy)
                head.addWidget(strategy_arrow)
                # Kept as a nonvisual state holder for service logic and
                # backwards compatibility with existing integration tests.
                facts['Запуск сервиса']=QLabel('Выключен')
            else:
                for title,value in specs[key]:
                    facts[title]=self._fact_row(wrap,title,value)
            self.detail_facts[key]=facts
            # Controls immediately follow facts/traffic; empty space must NOT
            # separate titles from their action switches.
            if key=='zapret':
                # One action = one outcome: enabling a Flowseal strategy also
                # configures its Windows service. No separate autostart toggle.
                wrap.addStretch(1)
                self.detail_service_toggle=Toggle(False)  # legacy test compatibility; not displayed
                self.flowseal_state_label=QLabel('При включении стратегия запускается вместе с Windows')
                self.flowseal_state_label.setObjectName('small')
                self.flowseal_state_label.setWordWrap(True)
                wrap.addWidget(self.flowseal_state_label)
                wrap.addSpacing(5)
                action=IconTextButton(IconKind.STRATEGY,'Подобрать лучшую стратегию',accented=True)
                action.setFixedHeight(33);action.clicked.connect(self.start_test);wrap.addWidget(action)
                self.test_button=action
                self.stop_flowseal_button=QPushButton('Остановить Zapret', panel)
                self.stop_flowseal_button.setObjectName('danger')
                self.stop_flowseal_button.setFixedHeight(30)
                self.stop_flowseal_button.clicked.connect(self.stop_selected_flowseal)
                wrap.addWidget(self.stop_flowseal_button)
                self.stop_flowseal_button.hide()
                self.stop_all_zapret_button=QPushButton('Остановить весь Zapret', panel)
                self.stop_all_zapret_button.setObjectName('danger')
                self.stop_all_zapret_button.setMinimumHeight(36)
                self.stop_all_zapret_button.setToolTip('Остановить свои и подтверждённые внешние экземпляры Zapret после подтверждения')
                self.stop_all_zapret_button.clicked.connect(self.show_stop_zapret_options)
                # 31 px with 9 px top/bottom global CSS padding clipped the
                # descender of the letter p in Zapret on Segoe UI / 125% DPI.
                # Keep full shutdown in Settings > Components. A large danger
                # button here made the small card jump between heights.
                self.stop_all_zapret_button.setVisible(False)
                zapret_card_layout = wrap
            elif key=='telegram':
                control=QHBoxLayout();control.setSpacing(9);wrap.addLayout(control)
                runtime=Toggle();runtime.clicked.connect(handlers[key]);control.addWidget(runtime)
                proxy_label=QLabel('Включить Telegram Proxy')
                proxy_label.setMinimumWidth(0)
                proxy_label.setWordWrap(True)
                # Match the normal 12 px text of WARP and Zapret controls:
                # the old #small (10 px) made this switch look inconsistent.
                control.addWidget(proxy_label,1)
                runtime.setToolTip('Управляет локальным прокси. Telegram Desktop может запросить подтверждение отдельно.')
                self.controls[key]=(runtime,state)
            else:
                control=QHBoxLayout();control.setSpacing(9);wrap.addLayout(control)
                runtime=Toggle();runtime.clicked.connect(handlers[key]);control.addWidget(runtime)
                warp_label=QLabel('Подключить WARP');warp_label.setMinimumWidth(0)
                warp_label.setWordWrap(True)
                control.addWidget(warp_label,1);self.controls[key]=(runtime,state)
            if key in {'telegram','warp'}:
                traffic=TrafficMiniPanel()
                wrap.addWidget(traffic)
                if not hasattr(self,'traffic_cards'):self.traffic_cards={}
                self.traffic_cards[key]=traffic
        self.strategy_name=self.detail_facts['zapret']['Стратегия']
        self.progress_box=QFrame(objectName='progressCard')
        track=QVBoxLayout(self.progress_box)
        track.setContentsMargins(8,5,8,5);track.setSpacing(3)
        progress_header=QHBoxLayout();progress_header.setSpacing(6)
        self.progress_title=QLabel('Подбор оптимальной стратегии')
        self.progress_title.setObjectName('small')
        progress_header.addWidget(self.progress_title,1)
        self.test_percent=QLabel('0%');self.test_percent.setObjectName('small')
        self.test_percent.setFixedWidth(36)
        self.test_percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_header.addWidget(self.test_percent)
        track.addLayout(progress_header)
        self.test_progress=QProgressBar()
        self.test_progress.setRange(0,100);self.test_progress.setValue(0)
        self.test_progress.setTextVisible(False);track.addWidget(self.test_progress)
        self.test_details=QLabel('Подготовка проверки…')
        self.test_details.setObjectName('sectionHint');self.test_details.setWordWrap(True)
        # Full details remain in the tooltip; the compact card shows progress
        # and percent without forcing the whole window to grow.
        self.test_details.setVisible(False)
        track.addWidget(self.test_details)
        self.progress_box.hide()
        # Progress replaces the strategy action during a running test; it
        # never adds to the card's fixed height or expands the entire window.
        zapret_card_layout.addWidget(self.progress_box)
        # No trailing stretch here. It previously left a large blank strip
        # below the strategy button and made its bottom margin look wrong.
        content.addSpacing(5)
        # Keep all extra height below the detail card, never between heading/tabs/card.
        # Components are accessible through Settings > Components only.
        content.addStretch(1)
        self.select_detail(0)
        self.load_last_test()

    def _setting_row(self, parent, name, value, handler, subtitle=''):
        frame=QFrame(objectName='row'); parent.addWidget(frame)
        line=QHBoxLayout(frame); line.setContentsMargins(11,9,9,9);line.setSpacing(8)
        text=QVBoxLayout();text.setSpacing(2);line.addLayout(text,1)
        label=QLabel(name)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setStyleSheet('font-weight:650')
        text.addWidget(label)
        if subtitle:
            detail=QLabel(subtitle);detail.setObjectName('muted');detail.setWordWrap(True);text.addWidget(detail)
        toggle=Toggle(value);toggle.toggled.connect(handler); line.addWidget(toggle)
        return toggle,frame

    def _settings(self):
        """Readable settings with the sidebar layout from the approved concept."""
        page=QWidget();self.pages.addWidget(page)
        whole=QVBoxLayout(page);whole.setContentsMargins(0,6,0,0);whole.setSpacing(9)
        label=QLabel('Настройки');label.setObjectName('heading');whole.addWidget(label)
        body_row=QHBoxLayout();body_row.setSpacing(9);whole.addLayout(body_row,1)
        sidebar=QFrame(objectName='settingsSidebar');sidebar.setFixedWidth(128)
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        sidebar_box=QVBoxLayout(sidebar)
        sidebar_box.setContentsMargins(6,8,6,8);sidebar_box.setSpacing(5)
        nav=sidebar_box
        self.settings_nav=[]
        for i, (title,kind) in enumerate((('Общие',IconKind.GEAR),
                                           ('Компоненты',IconKind.COMPONENTS),
                                           ('Диагностика',IconKind.ACTIVITY))):
            button=QPushButton(title)
            button.setIcon(QIcon(AnimatedIcon(kind,20).render_pixmap(20,1)))
            button.setObjectName('settingsTab');button.setCheckable(True)
            button.setMinimumWidth(0);button.setFixedHeight(36)
            button.clicked.connect(lambda checked=False,n=i:self.select_settings_section(n))
            nav.addWidget(button);self.settings_nav.append(button)
        # The navigation itself is only three items; do not draw a huge empty
        # panel down the side of the settings window.
        body_row.addWidget(sidebar, 0, Qt.AlignmentFlag.AlignTop)
        self.settings_sections=QStackedWidget();body_row.addWidget(self.settings_sections,1)

        def section():
            scroll=QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            body=QWidget();scroll.setWidget(body)
            layout=QVBoxLayout(body)
            layout.setContentsMargins(0,3,10,3)
            layout.setSpacing(7)
            self.settings_sections.addWidget(scroll)
            return layout

        self.prefs=section()
        launch=QHBoxLayout();self.prefs.addLayout(launch)
        divider=QLabel('ЗАПУСК И АВТОМАТИЗАЦИЯ');divider.setObjectName('eyebrow');launch.addWidget(divider)
        launch.addStretch()
        self.service_badge=QLabel('');self.service_badge.setMaximumWidth(88)
        self.service_badge.setToolTip('Состояние службы Zapret');launch.addWidget(self.service_badge)
        self.autostart_toggle,_=self._setting_row(
            self.prefs,'NetPilot вместе с Windows',self.autostart_enabled(),self.change_autostart,
            'Запуск приложения после входа в Windows')
        # Keep the old control as a nonvisual compatibility object only.
        # Zapret is installed as an AUTO_START Flowseal service on confirmation.
        self.zapret_service_toggle,self.zapret_service_row=self._setting_row(
            self.prefs,'Автозапуск Zapret управляется выбранной стратегией',False,
            self.change_zapret_autostart,'Используется штатная служба Flowseal')
        self.zapret_service_row.hide()
        self.start_hidden_toggle,_=self._setting_row(
            self.prefs,'Запускать свёрнутым',self.cfg.get('start_hidden',False),
            lambda v:self.preference('start_hidden',v),'Не показывать окно при автозапуске')
        self.auto_strategy_toggle,_=self._setting_row(
            self.prefs,'Автоматический контроль Zapret',self.cfg.get('auto_strategy',True),
            self.change_auto_strategy,'Контроль связи и подбор после подтверждённых сбоев')
        updates=QLabel('ОБНОВЛЕНИЯ');updates.setObjectName('eyebrow');self.prefs.addWidget(updates)
        self._setting_row(self.prefs,'Проверять обновления',self.cfg.get('auto_check',True),
                          lambda v:self.preference('auto_check',v))
        self._setting_row(self.prefs,'Автоустановка обновлений',self.cfg.get('auto_upgrade',False),
                          lambda v:self.preference('auto_upgrade',v))
        notifications_label=QLabel('УВЕДОМЛЕНИЯ WINDOWS')
        notifications_label.setObjectName('eyebrow')
        self.prefs.addWidget(notifications_label)
        self._setting_row(self.prefs, 'Показывать системные уведомления',
                          self.cfg.get('notifications_enabled', True),
                          lambda v:self.preference('notifications_enabled',v),
                          'Состояния и ошибки отображаются средствами Windows')
        self._setting_row(self.prefs, 'Уведомлять об успешных действиях',
                          self.cfg.get('notify_success', True),
                          lambda v:self.preference('notify_success',v),
                          'Ошибки остаются доступны в диагностике')
        note=QLabel('Вкладки «Компоненты» и «Диагностика» содержат остальные инструменты.')
        note.setObjectName('sectionHint');note.setWordWrap(True);self.prefs.addWidget(note)
        self.prefs.addStretch()

        components=section()
        eyebrow=QLabel('КОМПОНЕНТЫ И ОБСЛУЖИВАНИЕ')
        eyebrow.setObjectName('eyebrow');components.addWidget(eyebrow)
        self.download_panel=QFrame(objectName='progressCard')
        downloading=QVBoxLayout(self.download_panel)
        downloading.setContentsMargins(10,8,10,8);downloading.setSpacing(5)
        heading=QHBoxLayout();heading.setSpacing(6)
        heading.addWidget(QLabel('Загрузка компонентов'),1)
        self.download_percent=QLabel('0%');self.download_percent.setObjectName('small')
        heading.addWidget(self.download_percent)
        downloading.addLayout(heading)
        self.download_bar=QProgressBar();self.download_bar.setRange(0,100)
        self.download_bar.setTextVisible(False);downloading.addWidget(self.download_bar)
        self.download_description=QLabel('');self.download_description.setObjectName('sectionHint')
        self.download_description.setWordWrap(True);downloading.addWidget(self.download_description)
        self.download_panel.hide();components.addWidget(self.download_panel)
        for kind,title,handler,subtitle in (
            (IconKind.DOWNLOAD,'Установить компоненты',self.install_dialog,'Zapret и официальный WARP'),
            (IconKind.REFRESH,'Проверить новые версии',lambda:self.check_updates(True),'Проверка доступных обновлений'),
            (IconKind.STRATEGY,'Подбор стратегий Zapret',lambda:(self.show_page(1),self.select_detail(0),self.start_test()),'Сравнение доступных стратегий'),
            (IconKind.SHIELD,'Остановить весь Zapret',self.show_stop_zapret_options,'Остановить свои и подтверждённые внешние экземпляры после подтверждения'),
            (IconKind.LAYERS,'Вернуть Zapret до обновления',lambda:self.rollback('zapret'),'Восстановление из резервной копии'),
            (IconKind.GEAR,'Параметры Zapret',self.expert,'Game Filter, IPSet, профиль доменов'),
            (IconKind.ACTIVITY,'Диагностика Windows',self.diagnostics,'Проверка служб и состояния компонентов'),
        ):
            frame=QFrame(objectName='row');components.addWidget(frame)
            row=QHBoxLayout(frame);row.setContentsMargins(7,5,6,5);row.setSpacing(6)
            icon=AnimatedIcon(kind,20);icon.set_state(IconState.ENABLED,animate=False)
            row.addWidget(icon)
            action=QPushButton(title);action.setObjectName('settingsAction');action.setMinimumWidth(0)
            action.setToolTip(subtitle);action.setFixedHeight(31)
            action.clicked.connect(handler);row.addWidget(action,1)
        self.restore_autostart_button=QPushButton('Восстановить внешний автозапуск Zapret')
        self.restore_autostart_button.setObjectName('quiet')
        self.restore_autostart_button.clicked.connect(self.restore_external_autostart)
        components.addWidget(self.restore_autostart_button)
        legal=QLabel('NetPilot — неофициальная утилита. Flowseal и Cloudflare — отдельные проекты.')
        legal.setObjectName('muted');legal.setWordWrap(True);components.addWidget(legal)
        components.addStretch()

        diag=section()
        eyebrow=QLabel('СОСТОЯНИЕ ПОДКЛЮЧЕНИЙ')
        eyebrow.setObjectName('eyebrow');diag.addWidget(eyebrow)
        info_card=QFrame(objectName='row');diag.addWidget(info_card)
        info_layout=QVBoxLayout(info_card);info_layout.setContentsMargins(12,12,12,12)
        self.diagnostics_info=QLabel('');self.diagnostics_info.setWordWrap(True)
        self.diagnostics_info.setObjectName('muted');info_layout.addWidget(self.diagnostics_info)
        button=QPushButton('Обновить состояния');button.setObjectName('quiet')
        button.clicked.connect(lambda:(self.refresh(),self.update_diagnostics_info()))
        diag.addWidget(button)
        button=QPushButton('Подробности служб Windows');button.setObjectName('quiet')
        button.clicked.connect(self.diagnostics);diag.addWidget(button)
        history_label=QLabel('ПОСЛЕДНИЕ СОБЫТИЯ')
        history_label.setObjectName('eyebrow')
        diag.addWidget(history_label)
        self.events_info=QLabel('Событий пока нет.')
        self.events_info.setObjectName('muted')
        self.events_info.setWordWrap(True)
        self.events_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diag.addWidget(self.events_info)
        history_button=QPushButton('Журнал уведомлений')
        history_button.setObjectName('quiet')
        history_button.clicked.connect(self.show_notification_history)
        diag.addWidget(history_button)
        diag.addStretch()
        self.select_settings_section(0)
        self.refresh_service_setting()
        self.refresh_restore_autostart_action()

    def _navigate_back(self):
        if self.pages.currentIndex()==2:
            self.show_page(getattr(self,'_return_page',1))
        else:
            self.show_page(0)

    def toggle_page(self):
        if self.pages.currentIndex()==2:
            self._navigate_back()
        else:
            self._return_page=self.pages.currentIndex()
            self.select_settings_section(0)
            self.show_page(2)

    def show_components(self):
        if self.pages.currentIndex()!=2:
            self._return_page=self.pages.currentIndex()
        self.show_page(2)
        self.select_settings_section(1)

    def select_settings_section(self, index):
        if not hasattr(self, 'settings_sections'):
            return
        self.settings_sections.setCurrentIndex(index)
        for i, button in enumerate(self.settings_nav):
            button.setChecked(i==index)
        if index==2:
            self.update_diagnostics_info()

    def show_notification_history(self):
        self.inline_notice('Журнал NetPilot', self.event_journal.recent_text(20))

    def update_diagnostics_info(self):
        if not hasattr(self,'diagnostics_info'):
            return
        self.events_info.setText(self.event_journal.recent_text(4))
        if self.smoke_mode:
            self.diagnostics_info.setText('Режим проверки интерфейса · системные операции отключены')
            return
        snapshot=self.zapret_snapshot
        ready=self._zapret_inventory_ready
        self.diagnostics_info.setText(
            ('Экземпляров Zapret: '+str(snapshot.active_count) if ready else 'Проверка Zapret ещё выполняется')+
            '\nTelegram: '+('локальный прокси запущен' if self.telegram_ready else 'не запущен')+
            '\nWARP: '+('подключён' if self.warp_connected else 'отключён')+
            '\nСлужба zapret: '+m.service_state('zapret')+
            ' (автозапуск: '+m.service_start_mode('zapret')+')'+
            ('\nОграничения проверки: '+ '; '.join(snapshot.warnings[:2])[:250] if snapshot.warnings else '')+
            '\n\nСеть ПК: измерение трафика не выполняет Speedtest.\n'+
            'Подробности системных служб доступны в отдельной диагностике.')


    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        self.back_btn.setVisible(index!=0)
        self.settings_btn.setVisible(index!=2)
        self.traffic_sampler.reset()
        self._sync_page_footer()
        if hasattr(self,'window_transition'):
            self._update_compact_height()

    def open_details(self):
        self.show_page(1)

    def select_detail(self, index):
        if not hasattr(self,'detail_stack'):return
        self.detail_stack.setCurrentIndex(index)
        self._sync_detail_card_height()
        self.traffic_sampler.reset()
        keys=list(self.detail_tabs)
        for current,button in enumerate(self.detail_tabs.values()):
            button.setChecked(current==index)
            key=keys[current]
            title={'zapret':'Zapret','telegram':'Telegram','warp':'WARP'}[key]
            label=self._component_states.get(key,(False,'Готов'))[1]
            button.setText(title+'\n'+('Выбран' if current==index else label))
        if hasattr(self,'window_transition'):
            self._update_compact_height()

    def _set_test_progress_visible(self, visible):
        visible = bool(visible)
        self.progress_box.setVisible(visible)
        self.stop_all_zapret_button.hide()
        self.test_button.setVisible(not visible)

    def _sync_detail_card_height(self):
        """Do not resize a card when switching components or status changes."""
        if hasattr(self, 'detail_stack') and self.detail_stack.height() != 184:
            self.detail_stack.setFixedHeight(184)

    def _update_traffic(self):
        # Reading OS byte counters never sends traffic over the network.
        if (not self.isVisible() or self.pages.currentIndex()!=1 or
                self.detail_stack.currentIndex() not in (1,2)):
            self.traffic_sampler.reset()
            return
        try: reading=self.traffic_sampler.sample()
        except (OSError,ValueError):
            self.traffic_sampler.reset()
            return
        if reading is not None:
            key='telegram' if self.detail_stack.currentIndex()==1 else 'warp'
            self.traffic_cards[key].update_reading(reading)

    def _update_compact_height(self):
        """Fixed, predictable geometry. Only explicit page changes may resize.

        All component tabs, errors, progress updates and automatic operations
        retain one size; settings have a second fixed size with scroll areas.
        """
        page_index = self.pages.currentIndex()
        target = self._settings_size if page_index == 2 else self._base_size
        self._sync_detail_card_height()
        # Root chrome: 11 px top/bottom, 34 px header and 8 px spacing.
        # Fixed page heights prevent hidden pages and notifications from
        # creating blank gaps above the active component tab.
        page_height = target[1] - 22 - 34 - 8
        if self.pages.height() != page_height:
            self.pages.setFixedHeight(page_height)
        if (self.width(), self.height()) == target:
            return
        old_right = self.x() + self.width()
        old_bottom = self.y() + self.height()
        self.setFixedSize(*target)
        # Keep the lower-right corner stable when moving to/from Settings.
        if self.isVisible() and self._panel_positioned:
            screen = QApplication.screenAt(QPoint(old_right-1, old_bottom-1)) or QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                x = max(area.left(), min(old_right-target[0], area.right()-target[0]+1))
                y = max(area.top(), min(old_bottom-target[1], area.bottom()-target[1]+1))
                self.move(x, y)

    def note(self, message, error=False, title=None):
        """Journal the event and deliver a best-effort OS tray notification.

        A status notification must never change the widget layout or pretend
        the system action worked. Important confirmations stay in InlineModal.
        """
        full = str(message)
        self._decline_strategy_offer()
        lower = full.casefold()
        inventory_changed = ('inventory is incomplete or changed' in lower or
                             ('accessdenied' in lower and 'zapret' in lower))
        if title is None:
            title = ('Не удалось изменить Zapret' if error and inventory_changed
                     else 'Требуется внимание' if error else 'NetPilot')
        if error and inventory_changed:
            visible = ('Не удалось проверить все процессы Zapret. Проверьте права доступа, '
                       'обновите состояние и повторите действие.')
        else:
            visible = humanize_zapret_error(full) if error else full
        entry = self.event_journal.add(title, full, error)
        self._deliver_system_notification(entry['title'], safe_text(visible), error)
        if error:
            if inventory_changed:
                last_active = self._component_states.get('zapret', (False, ''))[0]
                self.home_summaries['zapret'].set_state(last_active, 'Проверка недоступна')
                self.home_summaries['zapret'].set_icon_state(IconState.ERROR)
                self.detail_tabs['zapret'].set_icon_state(IconState.ERROR)
            self._render_home_hero(error=True)
            self.message_timer.start(8000)
            if hasattr(self, 'tray'):
                self._tray_error_until = time.monotonic() + 8
                self._update_tray()
                QTimer.singleShot(8100, self._update_tray)
        if hasattr(self, 'diagnostics_info') and self.pages.currentIndex() == 2:
            if self.settings_sections.currentIndex() == 2:
                self.update_diagnostics_info()

    def _deliver_system_notification(self, title, message, error):
        if self.smoke_mode or not self.cfg.get('notifications_enabled', True):
            return False
        if not error and not self.cfg.get('notify_success', True):
            return False
        tray = getattr(self, 'tray', None)
        if tray is None or not tray.isVisible() or not QSystemTrayIcon.supportsMessages():
            return False
        signature = (title, message, bool(error))
        now = time.monotonic()
        if signature == self._last_system_notice and now - self._last_system_notice_at < 15:
            return False
        self._last_system_notice, self._last_system_notice_at = signature, now
        # Qt's QIcon overload requests our logo instead of the generic blue
        # information symbol. The system can still override appearance.
        tray.showMessage(title, message[:200], status_icon(error=error),
                         7000 if error else 4000)
        return True

    def open_notification_details(self):
        self.show_panel()
        self._return_page = 1
        self.show_page(2)
        self.select_settings_section(2)

    def _sync_page_footer(self):
        return

    def dismiss_message(self):
        self.message_timer.stop()
        self._hide_message()

    def _hide_message(self):
        self._restore_component_visual_states()
        self._render_home_hero()

    def _component_icon_state(self,name,active):
        if name!='zapret':
            return IconState.ENABLED if active else IconState.DISABLED
        return {'disabled':IconState.DISABLED,'managed':IconState.ENABLED,
                'external':IconState.EXTERNAL,'multiple':IconState.PARTIAL,
                'partial':IconState.PARTIAL,'error':IconState.ERROR,
                'starting':IconState.STARTING,'stopping':IconState.STOPPING}.get(
                    getattr(self,'zapret_semantic_state','disabled'),IconState.DISABLED)

    def _restore_component_visual_states(self):
        for name,(active,label) in self._component_states.items():
            icon_state=self._component_icon_state(name,active)
            self.home_summaries[name].set_state(active,label)
            self.home_summaries[name].set_icon_state(icon_state)
            self.detail_tabs[name].set_icon_state(icon_state,animate=False)

    def _render_home_hero(self,error=False):
        active_count=sum(bool(value[0]) for value in self._component_states.values())
        busy=bool(has_foreground_work(self.running) or self.test_running or self.warp_pending)
        self.status_orb.set_state(active_count,busy,error)
        if error:
            self.home_title.setText('Требуется внимание')
            self.home_subtitle.setText('Откройте подробности и проверьте состояние')
        elif busy:
            self.home_title.setText('Проверяем соединение')
            self.home_subtitle.setText('NetPilot выполняет операцию автоматически')
        elif not self._zapret_inventory_ready or self.zapret_semantic_state in {'error','partial'}:
            self.home_title.setText('Состояние не подтверждено')
            self.home_subtitle.setText('Проверьте Zapret в подробностях')
        elif active_count:
            self.home_title.setText('Подключения активны')
            self.home_subtitle.setText('Показаны подтверждённые состояния компонентов')
        else:
            self.home_title.setText('Нет активных подключений')
            self.home_subtitle.setText('Выберите компонент, чтобы начать работу')

    def apply_zapret_snapshot(self, snapshot, generation=None):
        """Render only a current immutable inventory snapshot."""
        if not isinstance(snapshot, ZapretSnapshot):
            return self.apply_zapret_inventory_error('Некорректный результат проверки')
        if generation is not None and generation != self._zapret_generation:
            return
        self.zapret_snapshot=snapshot;self._zapret_inventory_ready=True
        pending_stop=getattr(self,'_pending_stop_verification',None)
        if pending_stop:
            self._pending_stop_verification=None
            if snapshot.warnings:
                self.note('Остановка выполнена, но проверка неполная: доступ к части компонентов ограничен.',True)
            elif snapshot.active_count:
                self.note(f'После остановки ещё обнаружено экземпляров Zapret: {snapshot.active_count}.',True)
            else:
                self.note('Проверено: все обнаруженные экземпляры Zapret остановлены.')
        if getattr(self, '_resume_test_after_external_stop', False):
            self._resume_test_after_external_stop=False
            if not snapshot.active and not snapshot.ambiguous and not snapshot.warnings:
                QTimer.singleShot(0, lambda: self.start_test(False))
            else:
                self.note('Подбор не начат: остановка внешнего Zapret не подтверждена. '
                          'Проверьте процессы в диагностике.', True)
        own=tuple(item for item in snapshot.active if item.origin=='netpilot')
        external=tuple(item for item in snapshot.active if item.origin!='netpilot')
        if snapshot.active_count > 1:
            semantic,label='multiple','Работают несколько Zapret'
        elif own:
            semantic,label='managed','Работает'
        elif external:
            semantic,label=('external','Flowseal работает') if m.service_state('zapret')=='RUNNING' \
                           else ('external','Внешний Zapret работает')
        elif snapshot.warnings or snapshot.ambiguous:
            semantic,label='partial','Проверка неполная'
        else:
            semantic,label='disabled','Готов'
        self._render_zapret_state(semantic,label,bool(own),bool(snapshot.active))
        self.refresh_restore_autostart_action()

    def _render_zapret_state(self, semantic, label, own_active, any_active):
        self.zapret_semantic_state=semantic
        self._zapret_own_active=bool(own_active)
        self._component_states['zapret']=(bool(any_active),label)
        icon_state=self._component_icon_state('zapret',bool(any_active))
        dot,status=self.detail_status_labels['zapret'];dot.set_state(icon_state);status.setText(label)
        self.home_summaries['zapret'].set_state(bool(any_active),label)
        self.home_summaries['zapret'].set_icon_state(icon_state)
        self.detail_tabs['zapret'].set_icon_state(icon_state)
        selected=self.detail_tabs['zapret'].isChecked()
        self.detail_tabs['zapret'].setText('Zapret\n'+('Выбран' if selected else label))
        # System-wide stop remains accessible in Settings > Components, not
        # inside the component card. It must never reappear as a floating
        # top-level Qt button during an inventory refresh.
        self._stop_button_allowed=bool(any_active)
        self.stop_all_zapret_button.hide()
        # Autopick remains discoverable, but never starts over an external instance.
        self.test_button.setVisible(not self.progress_box.isVisible())
        self.test_button.setToolTip('Для подбора внешний Zapret можно остановить после подтверждения.' if any_active else '')
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
        self._pending_stop_verification=None
        dot,status=self.detail_status_labels['zapret'];dot.set_state(IconState.ERROR)
        status.setText('Не удалось проверить Zapret');status.setToolTip(str(error))
        self.home_summaries['zapret'].set_state(False,'Проверка недоступна')
        self.home_summaries['zapret'].set_icon_state(IconState.ERROR)
        self.detail_tabs['zapret'].set_icon_state(IconState.ERROR)
        self._component_states['zapret']=(self._component_states.get('zapret',(False,''))[0],'Проверка недоступна')
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
        for name in ('zapret_service_toggle','detail_service_toggle'):
            widget=getattr(self,name,None)
            if widget is not None:widget.setEnabled(bool(enabled))

    def begin_privileged_operation(self, operation, strategy=''):
        if self._zapret_operation is not None:
            self.note('Дождитесь завершения операции Zapret.',True);return False
        if operation not in {'remove_managed_service','flowseal_apply','flowseal_stop'} and not self._zapret_inventory_ready:
            self.note('Сначала дождитесь проверки Zapret.',True);self.refresh_zapret_inventory();return False
        if operation not in {'remove_managed_service','flowseal_apply','flowseal_stop'} and self.zapret_snapshot.warnings:
            # A partial inventory is not a failed connection and should not
            # leave a permanent red error while the user is deciding on UAC.
            # Never bypass the privileged rescan: the administrator process
            # must re-identify every target before touching external services.
            if not b.is_admin():
                approved=self.confirm(
                    'Нужны права администратора',
                    'Windows ограничила проверку некоторых экземпляров Zapret. '
                    'Для безопасного изменения служб требуется UAC. '
                    'Перезапустить NetPilot от администратора? '
                    'После повторного сканирования подтвердите операцию ещё раз.')
                if approved:
                    self.elevate('--elevated')
                else:
                    self.note('Операция отменена. Zapret не изменён; для изменения служб нужны права администратора.')
            else:
                self.note('Windows ограничила чтение данных одного из процессов Zapret даже после UAC. '
                          'Автозапуск не изменён: откройте Диагностику, чтобы увидеть PID и причину.',True)
            return False
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
        if operation not in {'remove_managed_service'}:
            self._render_zapret_state('starting' if operation == 'flowseal_apply' else 'stopping',
                                      'Настраиваем Flowseal…' if operation == 'flowseal_apply' else 'Останавливаем…',
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
            self._resume_test_after_external_stop=False
            self._zapret_operation=None;self.note('Операция Zapret: '+str(exc),True)
            self.refresh_zapret_inventory();return
        operation=self._zapret_operation[1];chosen_strategy=self._zapret_operation[2];self._zapret_operation=None
        if response is None:
            self._resume_test_after_external_stop=False
            self.note('Операция Zapret не завершилась вовремя.',True)
        elif response.status=='success':
            if operation in ('stop_all','disable_restart'):
                self._pending_stop_verification=operation
                self.note('Команда отправлена. Проверяем фактическое состояние Zapret…')
                self.refresh_zapret_inventory()
                return
            if operation == 'flowseal_apply':
                self.cfg['strategy']=chosen_strategy
                m.write_cfg(self.cfg)
                self.strategy_name.setText(chosen_strategy.replace('general','').replace('.bat','').strip(' ()') or 'general')
                self.strategy_name.setToolTip(chosen_strategy)
            copy={'flowseal_apply':'Стратегия Flowseal включена и запускается вместе с Windows',
                  'flowseal_stop':'Zapret остановлен; автозапуск отключён',
                  'stop_all':'Весь Zapret остановлен',
                  'disable_restart':'Zapret остановлен, автозапуск отключён',
                  'restore_autostart':'Внешний автозапуск восстановлен',
                  'remove_managed_service':'Служба NetPilot Zapret удалена'}[operation]
            self.note(copy)
        else:
            self._resume_test_after_external_stop=False
            self.note('Операция Zapret: '+('; '.join(response.errors) or response.status),True)
        self.refresh_service_setting()
        self.refresh_zapret_inventory()
        if operation in ('remove_managed_service', 'flowseal_apply', 'flowseal_stop'):
            QTimer.singleShot(1500, self.refresh_service_setting)

    def request_zapret_transfer(self, strategy):
        """Compatibility entrypoint; no legacy NetPilot-owned service transfer."""
        self.offer_strategy_autostart(strategy)

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
        """Single confirmation after manual selection or completed benchmark."""
        if not strategy or self._zapret_operation is not None:
            return
        folder=m.sub_dir(self.cfg,'zapret')
        if strategy not in m.list_strategies(folder):
            self.note('Выбранная стратегия Flowseal не найдена.',True);return
        message=(f'Включить стратегию «{strategy}»? '
                 'NetPilot установит или обновит штатную службу Flowseal '
                 'и настроит её запуск вместе с Windows. '                 'Если обнаружена неподтверждённая чужая служба, действие будет отменено.')
        if self.inline_confirm('Включить стратегию Flowseal',message):
            self.begin_privileged_operation('flowseal_apply',strategy)

    def _prompt_strategy_autostart(self):
        strategy=getattr(self,'_offered_strategy',None)
        self._offered_strategy=None
        if strategy:self.offer_strategy_autostart(strategy)

    def _accept_strategy_offer(self):
        self._prompt_strategy_autostart()

    def _decline_strategy_offer(self):
        self._offered_strategy=None

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
            control=self.autostart_toggle
            control.blockSignals(True);control.setChecked(not value);control.blockSignals(False)

    def refresh_service_setting(self):
        """Display confirmed Flowseal SCM state, never a separate auto toggle."""
        if not hasattr(self, 'service_badge'):
            return
        state = 'not_installed' if self.smoke_mode else m.service_state('zapret')
        mode = 'not_installed' if self.smoke_mode else m.service_start_mode('zapret')
        automatic = mode == 'AUTO_START'
        if state == 'RUNNING' and automatic:
            text, hint = 'АВТО · FLOWSEAL', 'Служба Zapret запущена и стартует вместе с Windows'
        elif state == 'not_installed':
            text, hint = 'НЕТ СЛУЖБЫ', 'Выберите стратегию, чтобы установить службу Flowseal'
        elif state in {'unknown','access_denied','not_windows'}:
            text, hint = 'НЕИЗВЕСТНО', 'Состояние службы Windows не удалось проверить'
        else:
            text, hint = 'ZAPRET ОСТАНОВЛЕН', 'Служба существует, но не запущена или автозапуск отключён'
        self.service_badge.setText(text)
        self.service_badge.setToolTip(hint)
        self.service_badge.setObjectName('success' if state == 'RUNNING' and automatic else 'muted')
        self.service_badge.style().unpolish(self.service_badge)
        self.service_badge.style().polish(self.service_badge)
        if hasattr(self, 'flowseal_state_label'):
            self.flowseal_state_label.setText('Автозапуск настроен · Flowseal' if state == 'RUNNING' and automatic
                                             else 'При включении стратегия запускается вместе с Windows')
        if hasattr(self, 'stop_flowseal_button'):
            self.stop_flowseal_button.setVisible(state == 'RUNNING')
        if hasattr(self, 'detail_facts'):
            self.detail_facts['zapret']['Запуск сервиса'].setText(hint)

    def change_zapret_autostart(self, value):
        """Legacy callback: use the unified strategy action, not a second toggle."""
        if value:
            self.enable_selected_flowseal()
        else:
            self.stop_selected_flowseal()

    def enable_selected_flowseal(self):
        folder=m.sub_dir(self.cfg,'zapret')
        strategy=self.cfg.get('strategy','general.bat')
        if not folder.is_dir():
            self.install_dialog();return
        choices=m.list_strategies(folder)
        if strategy not in choices:
            if not choices:
                self.note('Не установлены стратегии Flowseal.',True);return
            strategy=choices[0]
        self.offer_strategy_autostart(strategy)

    def stop_selected_flowseal(self):
        if self.inline_confirm('Остановить Zapret',
             'Остановить службу Flowseal и отключить её автозапуск Windows? '
             'Стратегии и файлы сохранятся.'):
            self.begin_privileged_operation('flowseal_stop')

    def apply_zapret_service(self, enabled):
        """Deprecated entry point; delegate to the unified Flowseal action."""
        if enabled:
            self.enable_selected_flowseal()
        else:
            self.stop_selected_flowseal()

    def _update_tray(self):
        """Keep the small Windows surface useful without opening the window."""
        if not hasattr(self, 'tray'):
            return
        states = self._component_states
        active_count = sum(bool(value[0]) for value in states.values())
        busy = bool(has_foreground_work(self.running) or self.test_running or self.warp_pending)
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
        self._update_compact_height()
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
        self._panel_positioned = True

    def closeEvent(self,event):
        self.hide_to_tray();event.ignore()

    def on_first_run(self):
        if self.cfg.get('start_hidden',False) and ('--tray' in sys.argv):self.hide()
        if not self.cfg.get('installed') and not m.sub_dir(self.cfg,'zapret').is_dir():self.install_dialog()

    def install_dialog(self):
        if self.test_running or has_foreground_work(self.running) or self._zapret_operation is not None:
            self.note('Сначала завершите установку или другую изменяющую операцию.',True);return
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
        percent=max(0,min(100,int(percent)))
        if 'install' in self.running:
            self.download_panel.setVisible(True)
            self.download_bar.setValue(percent)
            self.download_percent.setText(f'{percent}%')
            self.download_description.setText(str(text)[:120])
            if self.pages.currentIndex()!=2 or self.settings_sections.currentIndex()!=1:
                self.show_page(2)
                self.select_settings_section(1)
        elif self.test_running:
            self._set_test_progress_visible(True)
            self.test_details.setVisible(False)
            self.test_progress.setValue(percent)
            self.test_percent.setText(f'{percent}%')
            self.test_details.setText(str(text)[:160])
            self.test_progress.setToolTip(text)
            self.test_button.setText(f'Остановить подбор · {percent}%')
        self._update_compact_height()
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
            if self._resume_transfer_after_elevation:
                self._resume_transfer_after_elevation=False
                if not error and not result.warnings and not result.ambiguous:
                    QTimer.singleShot(0,lambda:self.change_zapret_autostart(True))
                else:
                    self.note('После повышения прав проверка Zapret остаётся неполной. Служба не изменена.', True)
            return
        if error:
            if key=='autohealth':
                self.latest_auto_health={'error':error}
                self._update_tray();return
            if key=='warpstatus':
                self._warp_verify_pending=False
                self.warp_connected=False
                self.detail_facts['warp']['Подключение'].setText('Недоступно')
                self.controls['warp'][0].set_confirmed(False)
                self.detail_status_labels['warp'][0].set_state(IconState.ERROR)
                self.home_summaries['warp'].set_icon_state(IconState.ERROR)
                self._update_tray();return
            if key=='test':self.test_running=False;self.cancel.clear();self.test_button.setText('Подобрать лучшую стратегию  ›');self.test_button.setEnabled(True);self._automatic_test=False
            if key=='install':self.download_panel.hide()
            if key=='warp':self.warp_pending=False;self._warp_verify_pending=False
            self.note(('Служба Zapret: ' if key=='service' else f'{key}: ')+error,True)
            self._set_test_progress_visible(False)
            self.test_details.setVisible(False)
            self._update_compact_height()
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
                self.detail_facts['zapret']['Результат'].setText(f"{best['passed']} из {best['total']} адресов")
                if not was_automatic:
                    self.note('Найдена подходящая стратегия: '+best['strategy'])
                    # Do not silently apply or change the saved choice. The
                    # previous service was restored after the benchmark.
                    QTimer.singleShot(0,lambda name=best['strategy']: self.offer_strategy_autostart(name))
                else:
                    self.note('Проверка завершена, действующая стратегия не изменена.')
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
            self.managed_pids=list(result or [])
            if not self.managed_pids:
                self.note('Не удалось подтвердить запуск процесса Zapret.',True)
            else:
                self.note('Стратегия Zapret запущена; проверяем состояние процесса')
        elif key=='warp':
            self.warp_pending=False
            self._warp_verify_pending=True
            self.note('Проверяем фактическое состояние WARP…')
            self._last_warp=0
            if 'warpstatus' not in self.running:
                self.job('warpstatus',lambda:m.warp_action('status'))
        elif key=='warpstatus':
            s=str(result).lower()
            self._warp_verify_pending=False
            self.warp_connected='connected' in s and 'disconnected' not in s
            self.detail_facts['warp']['Подключение'].setText('Подключено' if self.warp_connected else 'Отключено')
        elif key=='service':
            if result is False:self.note('Служба Zapret уже отсутствует')
            elif isinstance(result,str):self.note('Автозапуск Zapret включён · '+result)
            else:self.note('Автозапуск Zapret выключен')
            self.refresh_service_setting()
        elif key=='install':
            self.download_panel.hide()
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
            self._set_test_progress_visible(False);self.test_details.setVisible(False)
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
        elif key=='stop':
            self.managed_pids=[]
            self._pending_stop_verification='local_stop'
            self.note('Команда остановки отправлена. Проверяем процессы Zapret…')
        self.refresh()

    def _hide_completed_progress(self):
        if not self.test_running and 'install' not in self.running:
            self._set_test_progress_visible(False)
            self.test_details.setVisible(False)
            self.test_details.setVisible(False)
            self._update_compact_height()

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
        telegram_label=('Запускается' if self.telegram_pending else
                        'Локальный прокси' if tele_running and self.telegram_ready else
                        'Ожидание порта' if tele_running else 'Готов')
        states={
            'zapret':(zap_running,zap_label),
            'telegram':(tele_running and self.telegram_ready,telegram_label),
            'warp':(self.warp_connected,'Подключён' if self.warp_connected else 'Готов' if m.warp_cli() else 'Не установлен'),
        }
        self._component_states = states
        for name,(active,label) in states.items():
            switch,detail=self.controls[name]
            if switch is not None:
                # Keep the user's requested state animated while backend work
                # is pending; only commit the actual state after verification.
                pending=(name=='telegram' and self.telegram_pending or
                         name=='warp' and (self.warp_pending or self._warp_verify_pending))
                if not pending:
                    switch.blockSignals(True)
                    switch.setChecked(active)
                    switch.blockSignals(False)
            detail.setText(label)
            dot,status=self.detail_status_labels[name];dot.set_active(active);status.setText(label)
            self.home_summaries[name].set_state(active,label)
            self.detail_tabs[name].set_icon_state(self._component_icon_state(name,active),animate=False)
            tab_title={'zapret':'Zapret','telegram':'Telegram','warp':'WARP'}[name]
            self.detail_tabs[name].setText(tab_title+'\n'+('Выбран' if self.detail_tabs[name].isChecked() else label))
        self._render_home_hero(error=time.monotonic()<getattr(self,'_tray_error_until',0))
        self.detail_facts['warp']['Подключение'].setText('Подключено' if self.warp_connected else 'Отключено')
        self._update_tray()
        self.refresh_service_setting()
        self.refresh_restore_autostart_action()
        self.refresh_zapret_inventory()
        if m.warp_cli() and not self.warp_pending and 'warpstatus' not in self.running:
            if not hasattr(self,'_last_warp') or time.monotonic()-self._last_warp>30:
                self._last_warp=time.monotonic();self.job('warpstatus',lambda:m.warp_action('status'))

    def toggle_zapret(self):
        """Tray action: selected strategy and Windows autostart are one action."""
        if self.test_running or self._zapret_operation is not None:
            self.note('Сначала завершите текущую операцию Zapret.',True);return
        if m.service_state('zapret') == 'RUNNING':
            self.stop_selected_flowseal()
        else:
            self.enable_selected_flowseal()

    def _sync_network_switches(self):
        # Restore only the observed backend state, never optimistic clicks.
        telegram=self.controls.get('telegram',(None,None))[0]
        warp=self.controls.get('warp',(None,None))[0]
        if telegram is not None and not self.telegram_pending:
            telegram.blockSignals(True)
            telegram.set_confirmed(bool(self.telegram_proc and self.telegram_proc.poll() is None and self.telegram_ready))
            telegram.blockSignals(False)
        if warp is not None and not (self.warp_pending or self._warp_verify_pending):
            warp.blockSignals(True);warp.set_confirmed(self.warp_connected);warp.blockSignals(False)

    def toggle_telegram(self):
        if self.telegram_pending:
            self.note('Telegram Proxy ещё запускается. Дождитесь проверки порта.')
            self._sync_network_switches();return
        if self.telegram_proc and self.telegram_proc.poll() is None:
            try:
                tg.stop(self.telegram_proc)
                self.telegram_proc = None
                self.telegram_ready = False
                self.telegram_pending = False
                self.note('Прокси остановлен. Если Telegram остался без сети, отключите прокси в Telegram Desktop.')
            except Exception as exc:
                self.note('Не удалось остановить Telegram Proxy: ' + str(exc), True)
            self.refresh()
            return
        if not self.confirm('Telegram Proxy',
                'Запустить локальный прокси? Telegram Desktop может отдельно запросить подтверждение подключения.'):
            self._sync_network_switches()
            self.refresh()
            return
        try:
            proc = tg.spawn(self.cfg)
            m.write_cfg(self.cfg)  # Persist the 32-character MTProto secret.
            self.telegram_proc = proc
            self.telegram_pending = True
            self.telegram_ready = False
            self.note('Запускаем локальный Telegram Proxy…')
            self._tg_checks=0
            QTimer.singleShot(900, self.check_telegram_started)
        except Exception as exc:
            self.telegram_proc = None
            self.telegram_pending = False
            self.telegram_ready = False
            self.note('Telegram Proxy: ' + str(exc), True)
        self._sync_network_switches()
        self.refresh()

    def check_telegram_started(self):
        proc=self.telegram_proc
        if not proc:
            return
        if proc.poll() is not None:
            self.telegram_pending=False
            self.telegram_ready=False
            self.note('Telegram Proxy завершился. Проверьте telegram-worker-error.log.', True)
            self.telegram_proc=None
            self.refresh()
            return
        if tg.port_in_use():
            self.telegram_pending=False
            self.telegram_ready=True
            self.note('Локальный прокси работает. Подключение отдельно подтверждается в Telegram Desktop.')
            if self.cfg.get('telegram_auto_connect',True):
                # Official upstream uses tg://proxy; Telegram may ask confirmation.
                self.connect_telegram()
            self.refresh()
            return
        self._tg_checks=getattr(self,'_tg_checks',0)+1
        if self._tg_checks<9:
            QTimer.singleShot(700,self.check_telegram_started)
        else:
            self.telegram_pending=False
            self.telegram_ready=False
            try: tg.stop(proc)
            except Exception: pass
            self.telegram_proc=None
            self.note('Прокси не открыл порт 1443. Проверьте журнал.',True)
            self.refresh()

    def connect_telegram(self):
        from PySide6.QtCore import QUrl
        secret = tg.get_secret(self.cfg)
        m.write_cfg(self.cfg)
        if not QDesktopServices.openUrl(QUrl(tg.connect_url(secret))):
            self.note('Не удалось открыть Telegram Desktop. Проверьте установку Telegram.',True)

    def toggle_warp(self):
        if self.warp_pending:
            self._sync_network_switches();return
        if not m.warp_cli():
            if self.confirm('Установить WARP','Установить официальный Cloudflare WARP через winget? Windows может запросить UAC.'):
                self.warp_pending=True
                self.job('warp',m.install_warp_silently)
            self._sync_network_switches();self.refresh();return
        action='disconnect' if self.warp_connected else 'connect'
        if action=='connect' and self.managed_pids and not self.confirm('Одновременные подключения',
            'WARP и Zapret могут конфликтовать. Продолжить подключение WARP?'):
            self._sync_network_switches();self.refresh();return
        self.warp_pending=True
        self.job('warp',lambda:m.warp_action(action));self.refresh()

    def choose_strategy(self):
        folder=m.sub_dir(self.cfg,'zapret')
        if not folder.is_dir():
            self.note('Сначала установите Zapret.',True)
            return
        choices=m.list_strategies(folder)
        if not choices:
            self.note('Не найдены установленные стратегии general*.bat.',True)
            return
        picker=StrategyPicker(self,choices,self.cfg.get('strategy','general.bat'))
        if not self.inline_custom(picker) or not picker.selected:
            return
        if self.test_running or self._zapret_operation is not None:
            self.note('Сначала завершите текущую операцию Zapret.',True)
            return
        self.offer_strategy_autostart(picker.selected)

    def start_test(self, automatic=False):
        automatic=bool(automatic)
        if self.test_running:
            if automatic:return
            self.cancel.set();self.test_button.setEnabled(False);self.test_details.setText('Останавливаем проверку…');return
        if has_foreground_work(self.running) or self._zapret_operation is not None or self.warp_pending:
            if not automatic:
                self.note('Сейчас выполняется установка, изменение Zapret или WARP. Дождитесь её завершения.',True)
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
            # Flowseal service is temporarily stopped for *manual* testing,
            # then restored with its unchanged autostart configuration.
            # Background health checks never interrupt an active service.
            if automatic and m.service_state('zapret')!='not_installed':
                return
            import psutil
            owned=set(self.managed_pids)
            external=[p.pid for p in psutil.process_iter(['name'])
                      if (p.info.get('name') or '').lower()=='winws.exe' and p.pid not in owned]
            if external and m.service_state('zapret')=='not_installed':
                if automatic:
                    return
                if not self._zapret_inventory_ready:
                    self.note('Сначала проверяем работающие экземпляры Zapret. Повторите подбор.',True)
                    self.refresh_zapret_inventory()
                    return
                confirmed_pids={item.pid for item in self.zapret_snapshot.active
                                if item.kind=='process' and item.pid is not None}
                if (self.zapret_snapshot.warnings or self.zapret_snapshot.ambiguous or
                        not set(external).issubset(confirmed_pids)):
                    raise RuntimeError('Найдены процессы winws.exe, принадлежность которых '
                                       'не подтверждена. Откройте диагностику Zapret.')
                if not self.inline_confirm('Остановить внешний Flowseal перед тестом',
                        f'Найдены {len(external)} запущенных вне NetPilot процесса(ов) winws.exe. '
                        'Для подбора они будут остановлены после подтверждения UAC. '
                        'Если процесс запускался вручную, автоматически восстанавливать '
                        'его NetPilot не будет. Продолжить?'):
                    return
                self._resume_test_after_external_stop=True
                if not self.begin_privileged_operation('stop_all'):
                    self._resume_test_after_external_stop=False
                return
        except Exception as exc:
            if automatic:self.note('Автоподбор пропущен: '+str(exc),True)
            else:QMessageBox.warning(self,'Тестирование',str(exc))
            return
        checks=len(files)*len(catalog.hosts)
        if not automatic and not self.confirm('Подбор стратегии',f'{len(files)} стратегий, {len(catalog.hosts)} доменов, всего {checks} HTTPS-запросов. На время теста работающая служба Flowseal будет остановлена, а после теста восстановлена. Новая стратегия применяется только после вашего отдельного подтверждения. Начать?'):
            return
        self.cancel.clear();self.test_running=True;self._automatic_test=automatic
        self._set_test_progress_visible(True);self.test_details.setVisible(False)
        self.test_progress.setValue(0);self.test_percent.setText('0%')
        self.test_details.setText('Подготавливаем тестирование стратегий…')
        self.test_button.setText('Остановить подбор · 0%');self.test_button.setEnabled(True)
        self.test_progress.setToolTip('Автоматически сравниваем стратегии…' if automatic else 'Сравниваем стратегии и выбираем лучшую…')
        # Benchmark only finds candidates; explicit consent is required to enable.
        apply=False
        previously_owned=list(self.managed_pids)
        previous_name=self.cfg.get('strategy','general.bat')
        from flowseal_service import benchmark_with_service_restored
        self.job('test',lambda:benchmark_with_service_restored(
            folder,lambda n,s:self.bus.progress.emit(n,s),self.cancel,
            owned_pids=previously_owned,previous_strategy=previous_name))

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
        service=m.service_state('zapret')
        mode=m.service_start_mode('zapret')
        named={'not_installed':'не установлена', 'access_denied':'доступ запрещён',
               'unknown':'статус неизвестен', 'RUNNING':'работает',
               'STOPPED':'остановлена'}.get(service, service)
        if self._zapret_inventory_ready:
            inventory=self.zapret_snapshot
            process_line=f'Подтверждённых активных экземпляров Zapret: {inventory.active_count}'
            if inventory.warnings:
                process_line+='\nПроверка неполная: '+ '; '.join(inventory.warnings[:2])[:180]
        else:
            process_line='Проверка процессов Zapret ещё не завершена'
        info=(f'Служба с именем zapret: {named}\n'
              f'Автозапуск службы: {mode}\n'
              f'{process_line}\n\n'
              f'WinDivert: {m.service_state("WinDivert")}\n'
              f'WinDivert14: {m.service_state("WinDivert14")}\n'
              f'BFE: {m.service_state("BFE")}\n\n'
              'Отсутствие службы zapret НЕ означает, что вручную запущенный winws.exe остановлен. '
              'Работающий WinDivert сам по себе НЕ подтверждает запуск Zapret.')
        self.inline_notice('Диагностика Windows', info)

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
    if window.width()!=window._base_size[0] or not window.tray.isVisible():
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
    if os.name == 'nt':
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('NetPilot.Desktop')
        except (AttributeError, OSError):
            pass  # A missing shell feature must not prevent application launch.
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
