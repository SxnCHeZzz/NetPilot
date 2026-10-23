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

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QBrush, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget, QFrame, QHBoxLayout,
    QVBoxLayout, QLabel, QPushButton, QProgressBar, QScrollArea, QComboBox,
    QCheckBox, QSystemTrayIcon, QMenu, QFileDialog, QMessageBox, QLineEdit, QPlainTextEdit,
    QSizePolicy, QStackedWidget, QToolButton, QGraphicsDropShadowEffect,
)
import managed as m
import benchmark as b
from catalog import load_catalog
import telegram_headless as tg
from tray_position import Rect, find_overflow_rect, physical_to_qt, centered

VERSION = '0.5.1-rc2'
PINK = '#FF4DA6'
CSS = '''
*{font-family:'Segoe UI';font-size:12px;color:#F4F2F8}
QMainWindow{background:transparent}
QDialog{background:#0D0E12}
QWidget#root{background:#0D0E12;border:1px solid #28212F;border-radius:17px}
QFrame#surface{background:#191A22;border:1px solid #2A2A34;border-radius:17px}
QFrame#row{background:#20212B;border:1px solid #30313E;border-radius:12px}
QFrame#row:hover{border-color:#494354;background:#262530}
QLabel#brand{font-size:19px;font-weight:800;letter-spacing:0.3px}
QLabel#heading{font-size:15px;font-weight:750}
QLabel#muted{font-size:11px;color:#9896A7}
QLabel#small{font-size:10px;color:#A9A2B3}
QLabel#eyebrow{font-size:10px;color:#E5A5CA;font-weight:750}
QLabel#status{color:#DFBED1;font-size:11px}
QLabel#error{color:#FF829B;font-size:11px}
QPushButton{border:1px solid #363342;background:#2B2935;border-radius:10px;padding:9px 13px;font-weight:600}
QPushButton:hover{background:#383341;border-color:#79546C}
QPushButton:pressed{background:#494052}
QPushButton:disabled{color:#777482;background:#23212A;border-color:#282632}
QPushButton#primary{background:#E63E95;border:1px solid #F45BAA;color:white;font-weight:750}
QPushButton#primary:hover{background:#FF54A6}
QPushButton#icon{background:#25232E;border:1px solid #302D3A;min-width:25px;min-height:24px;padding:5px;font-size:16px}
QPushButton#quiet{background:transparent;border:1px solid #292630;color:#BFB9C9}
QPushButton#quiet:hover{background:#282530}
QPushButton#danger{color:#FFADC6;border:1px solid #503346}
QProgressBar{background:#35313D;border:none;border-radius:4px;height:7px;max-height:7px}
QProgressBar::chunk{background:#F34DA3;border-radius:4px}
QComboBox,QLineEdit{background:#25232F;border:1px solid #403A4B;border-radius:8px;padding:7px;min-height:21px}
QComboBox QAbstractItemView{background:#221D2A;selection-background-color:#AE397A;outline:none}
QScrollArea{border:none;background:transparent}
QScrollBar:vertical{background:transparent;width:6px;margin:3px}
QScrollBar::handle:vertical{background:#504056;border-radius:3px;min-height:30px}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}
QToolTip{background:#272331;color:white;border:1px solid #564557;padding:6px}
'''


def app_icon():
    path = m.DOMAINS_FILE.parent / 'netpilot.ico'
    if path.exists():
        return QIcon(str(path))
    from PySide6.QtGui import QPixmap
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(PINK), 5)); p.drawRoundedRect(9, 9, 46, 46, 13, 13)
    p.setPen(QPen(QColor('#FFFFFF'), 5)); p.drawLine(36, 13, 25, 33)
    p.drawLine(25, 33, 38, 33); p.drawLine(38, 33, 28, 51)
    p.end()
    return QIcon(pm)


class Toggle(QPushButton):
    """Accessible compact switch with a consistent appearance on Windows 10/11."""
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(47, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda _state:self.update())
        self.setAccessibleName('Переключатель')

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(PINK if self.isChecked() else '#47434F'))
        p.drawRoundedRect(1, 1, 45, 24, 12, 12)
        p.setBrush(QColor('#FFFFFF' if self.isEnabled() else '#B0AEB5'))
        p.drawEllipse(25 if self.isChecked() else 4, 4, 19, 19)
        p.end()


class Bus(QObject):
    done = Signal(str, object, str)
    progress = Signal(int, str)


class SetupDialog(QDialog):
    """First-run component acquisition happens after the installer, with consent."""
    def __init__(self, parent, cfg):
        super().__init__(parent)
        self.setWindowTitle('NetPilot · Подготовка')
        self.setFixedWidth(440)
        box = QVBoxLayout(self); box.setContentsMargins(22, 22, 22, 22); box.setSpacing(13)
        title = QLabel('Подготовим NetPilot'); title.setObjectName('brand'); box.addWidget(title)
        text = QLabel('Загрузим компоненты из официальных релизов Flowseal.\nПуть можно изменить. WARP устанавливается отдельно с подтверждением Windows.')
        text.setObjectName('muted'); text.setWordWrap(True); box.addWidget(text)
        choose = QHBoxLayout(); box.addLayout(choose)
        self.path = QLineEdit(cfg['components_dir']); choose.addWidget(self.path, 1)
        browse = QPushButton('…'); browse.clicked.connect(self.browse); choose.addWidget(browse)
        self.z = QCheckBox('Zapret · Discord / YouTube'); self.z.setChecked(True); box.addWidget(self.z)
        self.t = QCheckBox('Telegram Proxy (встроен, отдельная загрузка не нужна)'); self.t.setChecked(False); self.t.setEnabled(False); box.addWidget(self.t)
        note = QLabel('Инсталлятор NetPilot не устанавливает сторонние сетевые драйверы без вашего разрешения.')
        note.setWordWrap(True); note.setObjectName('muted'); box.addWidget(note)
        actions = QHBoxLayout(); box.addLayout(actions)
        skip = QPushButton('Позже'); skip.clicked.connect(self.reject); actions.addWidget(skip)
        go = QPushButton('Загрузить'); go.setObjectName('primary'); go.clicked.connect(self.accept); actions.addWidget(go)

    def browse(self):
        directory = QFileDialog.getExistingDirectory(self, 'Папка компонентов', self.path.text())
        if directory: self.path.setText(directory)


class ExpertDialog(QDialog):
    """Advanced upstream controls stay hidden unless explicitly requested."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle('NetPilot · Дополнительно')
        self.resize(440, 530)
        outer = QVBoxLayout(self)
        title = QLabel('Расширенные параметры Zapret'); title.setObjectName('heading'); outer.addWidget(title)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); outer.addWidget(scroll, 1)
        content = QWidget(); scroll.setWidget(content)
        layout = QVBoxLayout(content); layout.setSpacing(12)
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
            ('Подключить дополнительный профиль доменов', self.add_domains),
            ('Посмотреть проверяемые домены', self.show_domains),
            ('Обновить IPSet', self.update_ipset),
            ('Диагностика служб', self.diagnostics),
            ('Оригинальный Service Manager…', self.upstream),
        ]:
            btn = QPushButton(title); btn.clicked.connect(callback); layout.addWidget(btn)
        hint = QLabel('Изменения настроек Flowseal требуют повторного запуска стратегии.\nПолные upstream-тесты, замена fake-файлов и установка службы доступны в оригинальном service.bat.')
        hint.setObjectName('muted'); hint.setWordWrap(True); layout.addWidget(hint)
        layout.addStretch()
        close = QPushButton('Закрыть'); close.clicked.connect(self.close); outer.addWidget(close)

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
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Проверяемые адреса · {len(catalog.hosts)}')
        dialog.resize(490, 550)
        lay = QVBoxLayout(dialog)
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
        close = QPushButton('Закрыть'); close.clicked.connect(dialog.accept); lay.addWidget(close)
        dialog.exec()

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
    def __init__(self):
        super().__init__()
        self.cfg = m.read_cfg()
        self.bus = Bus(); self.bus.done.connect(self.completed); self.bus.progress.connect(self.progress_update)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='NetPilotWorker')
        self.running = set(); self.test_running = False; self.cancel = threading.Event()
        self.managed_pids = []; self.telegram_proc = None; self.warp_connected = False
        self._overflow_rect = None
        self.warp_pending = False; self.latest = {}; self.latest_test = None
        self.suppress_toggle = False
        self.setWindowTitle('NetPilot'); self.setWindowIcon(app_icon())
        # Small frameless flyout like Volty. The shell notification overflow is
        # measured for centering, not guessed from the cursor or screen edge.
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(414); self.resize(414, 540)
        self._build()
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        action = QAction('Открыть NetPilot', self); action.triggered.connect(self.show_panel); menu.addAction(action)
        menu.addSeparator()
        for title, callback in [('Zapret', self.toggle_zapret), ('Telegram Proxy', self.toggle_telegram), ('WARP', self.toggle_warp)]:
            a = QAction('Переключить ' + title, self); a.triggered.connect(callback); menu.addAction(a)
        a = QAction('Подобрать стратегию', self); a.triggered.connect(self.start_test); menu.addAction(a)
        menu.addSeparator()
        a = QAction('Выйти', self); a.triggered.connect(self.quit); menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_clicked)
        self.tray.show()
        self.status_timer = QTimer(self); self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(15000)
        self.daily_timer=QTimer(self);self.daily_timer.timeout.connect(lambda:self.check_updates(False))
        self.daily_timer.start(6*60*60*1000)
        self.refresh()
        QTimer.singleShot(500, self.on_first_run)
        if self.cfg.get('auto_check',True): QTimer.singleShot(1500, lambda:self.check_updates(False))

    def _build(self):
        root = QWidget(objectName='root'); self.setCentralWidget(root)
        box = QVBoxLayout(root); box.setContentsMargins(18, 16, 18, 13); box.setSpacing(11)
        header = QHBoxLayout(); box.addLayout(header)
        title = QLabel('◆  NetPilot'); title.setObjectName('brand'); header.addWidget(title)
        header.addStretch()
        self.settings_btn = QPushButton('⚙'); self.settings_btn.setObjectName('icon'); self.settings_btn.setToolTip('Настройки')
        self.settings_btn.clicked.connect(self.toggle_page); header.addWidget(self.settings_btn)
        hide = QPushButton('—'); hide.setObjectName('icon'); hide.setToolTip('Свернуть в трей')
        hide.clicked.connect(self.hide); header.addWidget(hide)
        self.pages = QStackedWidget(); box.addWidget(self.pages, 1)
        self._home(); self._settings()
        self.message = QLabel('Готов к работе'); self.message.setObjectName('muted')
        self.message.setWordWrap(True); self.message.setMinimumHeight(22); box.addWidget(self.message)

    def _home(self):
        page = QWidget(); self.pages.addWidget(page)
        content = QVBoxLayout(page); content.setContentsMargins(0, 8, 0, 0); content.setSpacing(10)
        strap = QHBoxLayout(); content.addLayout(strap)
        intro = QLabel('Подключения'); intro.setObjectName('heading'); strap.addWidget(intro)
        strap.addStretch()
        self.connection_note = QLabel('● Готово'); self.connection_note.setObjectName('status'); strap.addWidget(self.connection_note)
        self.controls = {}
        for name, subtitle, icon, handler in [
            ('zapret', 'Discord и YouTube', '◈', self.toggle_zapret),
            ('telegram', 'Telegram Proxy · без отдельного трея', '↗', self.toggle_telegram),
            ('warp', 'Cloudflare WARP', '◎', self.toggle_warp),
        ]:
            card = QFrame(objectName='row'); content.addWidget(card)
            line = QHBoxLayout(card); line.setContentsMargins(12,12,12,12); line.setSpacing(10)
            glyph = QLabel(icon); glyph.setStyleSheet(f'font-size:19px;color:{PINK};'); glyph.setFixedWidth(23); line.addWidget(glyph)
            block = QVBoxLayout(); block.setSpacing(3); line.addLayout(block, 1)
            label = QLabel({'zapret':'Zapret','telegram':'Telegram','warp':'WARP'}[name]); label.setStyleSheet('font-weight:700;font-size:13px'); block.addWidget(label)
            detail = QLabel(subtitle); detail.setObjectName('muted'); block.addWidget(detail)
            switch = Toggle(); switch.setToolTip('Включить / выключить'); switch.clicked.connect(handler); line.addWidget(switch)
            self.controls[name]=(switch, detail)
        section = QFrame(objectName='surface'); content.addWidget(section)
        wrap=QVBoxLayout(section);wrap.setContentsMargins(13, 12, 13, 14);wrap.setSpacing(9)
        line=QHBoxLayout();wrap.addLayout(line)
        label=QLabel('Умный подбор'); label.setStyleSheet('font-weight:700;font-size:13px'); line.addWidget(label)
        line.addStretch()
        self.strategy_name=QLabel('Не выполнялся'); self.strategy_name.setObjectName('muted'); line.addWidget(self.strategy_name)
        self.test_button=QPushButton('Подобрать стратегию'); self.test_button.setObjectName('primary')
        self.test_button.clicked.connect(self.start_test); wrap.addWidget(self.test_button)
        self.test_progress=QProgressBar();self.test_progress.setRange(0,100);self.test_progress.setValue(0)
        self.test_progress.setVisible(False); wrap.addWidget(self.test_progress)
        self.test_details=QLabel('');self.test_details.setObjectName('small');self.test_details.setWordWrap(True);wrap.addWidget(self.test_details)
        content.addStretch()
        bottom=QHBoxLayout();content.addLayout(bottom)
        self.install_button=QPushButton('↓  Компоненты');self.install_button.setObjectName('quiet');self.install_button.clicked.connect(self.install_dialog);bottom.addWidget(self.install_button)
        bottom.addStretch()
        about=QLabel('v0.5.1 RC2');about.setObjectName('small');bottom.addWidget(about)
        self.load_last_test()

    def _setting_row(self, parent, name, value, handler):
        frame=QFrame(objectName='row'); parent.addWidget(frame)
        line=QHBoxLayout(frame); line.setContentsMargins(11,9,10,9)
        label=QLabel(name); line.addWidget(label,1)
        toggle=Toggle(value);toggle.toggled.connect(handler); line.addWidget(toggle)
        return toggle

    def _settings(self):
        page=QWidget();self.pages.addWidget(page)
        whole=QVBoxLayout(page);whole.setContentsMargins(0,8,0,0);whole.setSpacing(8)
        label=QLabel('Настройки');label.setObjectName('heading');whole.addWidget(label)
        scroll=QScrollArea();scroll.setWidgetResizable(True);whole.addWidget(scroll,1)
        content=QWidget();scroll.setWidget(content)
        self.prefs=QVBoxLayout(content);self.prefs.setContentsMargins(0,4,5,4);self.prefs.setSpacing(8)
        self._setting_row(self.prefs,'Запуск с Windows',self.autostart_enabled(),self.change_autostart)
        self._setting_row(self.prefs,'Запускать свёрнутым',self.cfg.get('start_hidden',False),lambda v:self.preference('start_hidden',v))
        self._setting_row(self.prefs,'Проверять обновления',self.cfg.get('auto_check',True),lambda v:self.preference('auto_check',v))
        self._setting_row(self.prefs,'Автоустановка обновлений',self.cfg.get('auto_upgrade',False),lambda v:self.preference('auto_upgrade',v))
        self._setting_row(self.prefs,'Автоматически применять лидера',self.cfg.get('auto_apply',False),lambda v:self.preference('auto_apply',v))
        self._setting_row(self.prefs,'Тишина во время игр',self.cfg.get('quiet_games',True),lambda v:self.preference('quiet_games',v))
        divider=QLabel('КОМПОНЕНТЫ И ОБСЛУЖИВАНИЕ');divider.setObjectName('eyebrow');self.prefs.addWidget(divider)
        for title, handler in [
            ('Установить / обновить Zapret',self.install_dialog),
            ('Подключить Telegram Desktop',self.connect_telegram),
            ('Проверить новые версии',lambda:self.check_updates(True)),
            ('Откатить Zapret',lambda:self.rollback('zapret')),
                        ('Расширенные настройки Zapret',self.expert),
            ('Диагностика',self.diagnostics),
        ]:
            button=QPushButton(title);button.setObjectName('quiet');button.clicked.connect(handler);self.prefs.addWidget(button)
        label=QLabel('NetPilot — неофициальное приложение. Flowseal и Cloudflare — самостоятельные проекты.');label.setWordWrap(True)
        label.setObjectName('muted');self.prefs.addWidget(label)
        self.prefs.addStretch()
        done=QPushButton('←  Вернуться');done.clicked.connect(self.toggle_page);whole.addWidget(done)

    def toggle_page(self):
        self.pages.setCurrentIndex(1-self.pages.currentIndex())
        self.settings_btn.setText('←' if self.pages.currentIndex() else '⚙')

    def note(self, message, error=False):
        self.message.setText(message[:280]); self.message.setObjectName('error' if error else 'muted')
        self.message.style().unpolish(self.message); self.message.style().polish(self.message)

    def confirm(self, title, text):
        return QMessageBox.question(self,title,text)==QMessageBox.StandardButton.Yes

    def preference(self, key, value):
        self.cfg[key]=value; m.write_cfg(self.cfg)

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

    def tray_clicked(self, reason):
        if reason==QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():self.hide()
            else:self.show_panel()

    def show_panel(self):
        # Windows can hide its hidden-icon flyout as soon as this window gets
        # focus. Preserve the last real HWND rectangle, exactly as in Volty.
        detected = find_overflow_rect()
        if detected is not None:
            self._overflow_rect = detected
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._position_panel()

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
        self.hide();event.ignore()

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
        selected=['zapret'] if dialog.z.isChecked() else []
        if not selected:return
        if self.managed_pids or (self.telegram_proc and self.telegram_proc.poll() is None) or self.test_running:
            QMessageBox.warning(self,'Обновление','Сначала отключите запущенные компоненты NetPilot.');return
        def task():
            out=[]
            for i,kind in enumerate(selected):
                self.bus.progress.emit(int(i*100/len(selected)),f'{kind}: поиск релиза…')
                meta=m.release(kind)
                m.install_component(self.cfg,meta,lambda p,t:self.bus.progress.emit(int((i*100+p)/len(selected)),t))
                out.append(f"{kind}: {meta['tag']}")
            return out
        self.job('install',task)

    def job(self,key,fn):
        if key in self.running:return
        self.running.add(key)
        def call():
            try:self.bus.done.emit(key,fn(),'')
            except Exception as e:self.bus.done.emit(key,None,str(e))
        self.pool.submit(call)

    def progress_update(self,percent,text):
        if not self.test_running:
            self.test_progress.setVisible(True);self.test_progress.setValue(percent)
        else:
            self.test_progress.setValue(percent)
        self.test_details.setText(text)

    def completed(self,key,result,error):
        self.running.discard(key)
        if error:
            if key=='test':self.test_running=False;self.cancel.clear();self.test_button.setText('Подобрать стратегию');self.test_button.setEnabled(True)
            if key=='warp':self.warp_pending=False
            self.note(f'{key}: {error}',True)
            self.test_progress.setVisible(False)
            return
        if key=='test':
            self.test_running=False;self.cancel.clear();self.test_button.setText('Подобрать стратегию');self.test_button.setEnabled(True)
            self.latest_test=result
            best=result.get('best')
            self.managed_pids=result.get('active_pids',[])
            if best and best.get('passed',0)>0:
                self.cfg['strategy']=best['strategy'];m.write_cfg(self.cfg)
                self.strategy_name.setText(best['strategy'].replace('general','').replace('.bat','').strip(' ()') or 'general')
                self.test_details.setText(f"{best['passed']}/{best['total']} HTTPS · "+('применена' if self.managed_pids else 'подобрана'))
                self.note('Подбор завершён: '+best['strategy'])
                if self.tray.isVisible():self.tray.showMessage('NetPilot',('Применена: ' if self.managed_pids else 'Подобрана: ')+best['strategy'],QSystemTrayIcon.MessageIcon.Information,5000)
            else:
                self.strategy_name.setText('Нет улучшения')
                self.test_details.setText('Все стратегии завершились ошибкой или не дали успешных HTTPS-ответов.')
                self.note('Не удалось определить подходящую стратегию')
            self.test_progress.setValue(100)
        elif key=='activate':
            self.managed_pids=result
            self.note('Zapret запущен до выхода из NetPilot')
        elif key=='warp':
            self.warp_pending=False;self.note(str(result)[:140]);self._last_warp=0
        elif key=='warpstatus':
            s=str(result).lower()
            self.warp_connected='connected' in s and 'disconnected' not in s
        elif key=='install':
            self.note('Установлено: '+', '.join(result));self.test_progress.setVisible(False)
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

    def refresh(self):
        z=m.sub_dir(self.cfg,'zapret');t=m.sub_dir(self.cfg,'telegram')/'TgWsProxy_windows.exe'
        # A preexisting Windows service is detected as running but is not owned by NetPilot.
        if self.managed_pids:
            try:
                import psutil
                self.managed_pids=[pid for pid in self.managed_pids if psutil.pid_exists(pid)]
            except Exception:pass
        zap_running=bool(self.managed_pids) or m.service_state('zapret')=='RUNNING'
        tele_running=bool(self.telegram_proc and self.telegram_proc.poll() is None)
        states={
            'zapret':(zap_running,'Работает' if zap_running else 'Готов' if z.is_dir() else 'Не установлен'),
            'telegram':(tele_running,'Работает' if tele_running else 'Готов · встроен'),
            'warp':(self.warp_connected,'Подключён' if self.warp_connected else 'Готов' if m.warp_cli() else 'Не установлен'),
        }
        for name,(active,label) in states.items():
            switch,detail=self.controls[name]
            switch.blockSignals(True);switch.setChecked(active);switch.blockSignals(False)
            detail.setText(label)
        self.connection_note.setText('● Активно '+str(sum(s[0] for s in states.values()))+'/3')
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
                self.note('Telegram Proxy остановлен')
            except Exception as exc:
                self.note('Не удалось остановить Telegram Proxy: ' + str(exc), True)
            self.refresh()
            return
        try:
            proc = tg.spawn(self.cfg)
            m.write_cfg(self.cfg)  # Persist the 32-character MTProto secret.
            self.telegram_proc = proc
            self.note('Telegram Proxy запускается без отдельного значка')
            QTimer.singleShot(900, self.check_telegram_started)
        except Exception as exc:
            self.telegram_proc = None
            self.note('Telegram Proxy: ' + str(exc), True)
        self.refresh()

    def check_telegram_started(self):
        if self.telegram_proc and self.telegram_proc.poll() is not None:
            self.note('Telegram Proxy завершился. Проверьте telegram-worker-error.log.', True)
            self.telegram_proc = None
            self.refresh()

    def connect_telegram(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        secret = tg.get_secret(self.cfg)
        m.write_cfg(self.cfg)
        QDesktopServices.openUrl(QUrl(tg.connect_url(secret)))

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

    def start_test(self):
        if self.test_running:
            self.cancel.set();self.test_button.setEnabled(False);self.test_details.setText('Останавливаем проверку…');return
        if self.running:
            self.note('Сначала дождитесь завершения другой операции.',True);return
        folder=m.sub_dir(self.cfg,'zapret')
        if not folder.is_dir():self.install_dialog();return
        if self.warp_connected:
            QMessageBox.warning(self,'WARP','Перед сравнением стратегий отключите WARP, иначе оценка будет некорректной.');return
        if not b.is_admin():
            if self.confirm('Тестирование','Для запуска WinDivert требуются права администратора. Перезапустить NetPilot с UAC?'):
                self.elevate('--test')
            return
        try:
            files=b.preflight(folder);catalog=load_catalog(folder)
        except Exception as exc:QMessageBox.warning(self,'Тестирование',str(exc));return
        checks=len(files)*len(catalog.hosts)
        if not self.confirm('Подбор стратегии',f'{len(files)} стратегий, {len(catalog.hosts)} доменов, всего {checks} HTTPS-запросов. Проверка может длиться долго и временно влиять на сеть. Начать?'):
            return
        self.cancel.clear();self.test_running=True
        self.test_progress.setVisible(True);self.test_progress.setValue(0)
        self.test_button.setText('Остановить проверку');self.test_button.setEnabled(True)
        self.test_details.setText('Запускаем тестирование…')
        apply=self.cfg.get('auto_apply',False)
        self.job('test',lambda:b.benchmark(folder,lambda n,s:self.bus.progress.emit(n,s),self.cancel,keep_best=apply))

    def elevate(self,flag='--elevated'):
        if os.name!='nt':return
        if getattr(sys,'frozen',False): exe=sys.executable;args=flag
        else:exe=sys.executable;args=f'"{Path(__file__).resolve()}" {flag}'
        ok=ctypes.windll.shell32.ShellExecuteW(None,'runas',exe,args,None,1)
        if ok<=32:QMessageBox.warning(self,'Windows','Не удалось запросить права администратора.')
        else:self.quit()

    def check_updates(self, manual=False):
        if not manual and not self.cfg.get('auto_check',True):return
        if 'updates' in self.running or self.test_running:return
        self.job('updates',lambda:{kind:m.release(kind) for kind in ('zapret',)})

    def rollback(self,kind):
        if self.test_running or self.managed_pids or (self.telegram_proc and self.telegram_proc.poll() is None):
            self.note('Сначала остановите компоненты.',True);return
        if self.confirm('Откат обновления',f'Вернуть предыдущую версию {kind}?'):
            try:m.rollback_component(self.cfg,kind);self.note('Предыдущая версия восстановлена')
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
                self.test_details.setText(f"Предыдущая проверка: {best['passed']}/{best['total']} HTTPS")
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
        self.tray.hide()
        self.pool.shutdown(wait=False,cancel_futures=True)
        QApplication.instance().quit()


def main():
    app=QApplication(sys.argv)
    app.setApplicationName('NetPilot');app.setQuitOnLastWindowClosed(False);app.setWindowIcon(app_icon());app.setStyleSheet(CSS)
    window=NetPilot()
    if '--tray' not in sys.argv and not ('--elevated' in sys.argv and window.cfg.get('start_hidden')):
        window.show_panel()
    if '--test' in sys.argv:QTimer.singleShot(1100,window.start_test)
    if '--zapret' in sys.argv:QTimer.singleShot(900,window.toggle_zapret)
    return app.exec()

if __name__=='__main__': sys.exit(main())
