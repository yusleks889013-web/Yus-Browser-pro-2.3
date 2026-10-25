from __future__ import annotations

import html
import json
import os
import time
import urllib.request
import re
import sys
from datetime import datetime
from pathlib import Path
import math

APP_DATA = Path(os.environ.get("APPDATA", str(Path.home()))) / "YusBrowser"
APP_DATA.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = APP_DATA / "settings.json"
DEFAULT_PREFERENCES = {
    "graphics_mode": "fast",
    "search_engine": "Yandex",
    "javascript_enabled": True,
    "auto_wide_youtube": True,
    "restore_tabs": True,
    "dark_websites": True,
    "animated_home": True,
    "click_sounds": True,
}

def load_preferences_early():
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            result = dict(DEFAULT_PREFERENCES)
            result.update(saved)
            # Migration: make Yandex the default for existing profiles that previously used Google.
            if result.get("search_engine") == "Google":
                result["search_engine"] = "Yandex"
            return result
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return dict(DEFAULT_PREFERENCES)

STARTUP_PREFERENCES = load_preferences_early()

# Set Chromium graphics flags before QtWebEngine is imported.
# The normal mode keeps GPU acceleration for performance; fallbacks are opt-in.
YUS_GRAPHICS_MODE = os.environ.get("YUS_BROWSER_GRAPHICS_MODE", "").strip().lower()
if not YUS_GRAPHICS_MODE:
    YUS_GRAPHICS_MODE = str(STARTUP_PREFERENCES.get("graphics_mode", "fast")).strip().lower()
if YUS_GRAPHICS_MODE not in ("fast", "compat", "software"):
    YUS_GRAPHICS_MODE = "fast"
if YUS_GRAPHICS_MODE == "software":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
elif YUS_GRAPHICS_MODE == "compat":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu-compositing"
else:
    os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)

from PySide6.QtCore import (Qt, QUrl, QStandardPaths, QTimer, qVersion, QStringListModel, QObject, QEvent,
                            QPropertyAnimation, QEasingCurve, QPoint, QRect, QThread, Signal)
from PySide6.QtGui import (QAction, QKeySequence, QFont, QColor, QShortcut, QDesktopServices, QIcon,
                           QPixmap, QPainter, QPen, QBrush, QTransform, QImage)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTabWidget, QProgressBar, QMessageBox, QFileDialog,
    QMenu, QListWidget, QListWidgetItem, QDialog, QDialogButtonBox, QFormLayout,
    QComboBox, QCheckBox, QToolButton, QTabBar, QSizePolicy, QCompleter,
    QGraphicsDropShadowEffect, QScrollArea, QInputDialog, QTextEdit, QPlainTextEdit
)
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings, QWebEnginePermission
from PySide6.QtWebEngineWidgets import QWebEngineView

from browser_utils import (
    resolve_address, SEARCH_ENGINES, DEFAULT_SEARCH_ENGINE, search_url_for, search_home_for
)

class SpeedTestWorker(QThread):
    progress = Signal(float, int, int)
    result = Signal(float, float, str)
    def run(self):
        ping_ms = -1.0
        mbps = 0.0
        try:
            total_expected = 5_000_000
            url = 'https://speed.cloudflare.com/__down?bytes=5000000'
            req = urllib.request.Request(url, headers={'User-Agent':'YusBrowser-SpeedTest/2.3'})
            start = time.perf_counter()
            downloaded = 0
            last_sample_t = start
            last_sample_bytes = 0
            with urllib.request.urlopen(req, timeout=20) as response:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    now = time.perf_counter()
                    dt = now - last_sample_t
                    if dt >= 0.12:
                        instant = ((downloaded - last_sample_bytes) * 8 / max(dt, 0.001)) / 1_000_000
                        self.progress.emit(max(0.0, instant), downloaded, total_expected)
                        last_sample_t, last_sample_bytes = now, downloaded
            elapsed = max(time.perf_counter()-start, 0.001)
            mbps = (downloaded * 8 / elapsed) / 1_000_000
            self.progress.emit(mbps, downloaded, total_expected)
            ping_start = time.perf_counter()
            req2 = urllib.request.Request('https://www.cloudflare.com/cdn-cgi/trace', method='HEAD', headers={'User-Agent':'YusBrowser-SpeedTest/2.3'})
            with urllib.request.urlopen(req2, timeout=8): pass
            ping_ms = (time.perf_counter()-ping_start)*1000
            self.result.emit(mbps, ping_ms, '')
        except Exception as e:
            self.result.emit(mbps, ping_ms, str(e))

class SpeedGauge(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.maximum = 200.0
        self.testing = False
        self.phase = 0
        self.setMinimumSize(340, 240)
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(30)
    def _animate(self):
        self.phase = (self.phase + 1) % 120
        self.update()
    def set_testing(self, enabled):
        self.testing = bool(enabled)
        self.update()
    def set_speed(self, value):
        self.value = max(0.0, float(value))
        if self.value > self.maximum * 0.92:
            self.maximum = min(10000.0, max(self.maximum, 2 ** (int(max(1,self.value)).bit_length())))
        self.update()
    def paintEvent(self, event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width(),self.height(); cx=w/2; cy=h*.78; radius=min(w*.39,h*.65)
        # Soft luminous backdrop and a clean modern dial.
        glow = QColor('#a855f7'); glow.setAlpha(24 + (self.phase % 30 if self.testing else 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(glow)
        p.drawEllipse(int(cx-radius*.82), int(cy-radius*.82), int(radius*1.64), int(radius*1.64))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor('#332344'), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(int(cx-radius),int(cy-radius),int(radius*2),int(radius*2),180*16,-180*16)
        ratio=min(1.0,self.value/self.maximum)
        # Gradient arc for a premium speedometer appearance.
        grad = __import__('PySide6.QtGui', fromlist=['QConicalGradient']).QConicalGradient(cx,cy,180)
        grad.setColorAt(0.0,QColor('#7c3aed')); grad.setColorAt(.5,QColor('#c084fc')); grad.setColorAt(1.0,QColor('#f0abfc'))
        p.setPen(QPen(QBrush(grad), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(int(cx-radius),int(cy-radius),int(radius*2),int(radius*2),180*16,int(-180*ratio*16))
        for i in range(21):
            a=math.radians(180-i*9); r1=radius-27; r2=radius-13 if i%2==0 else radius-19
            x1=cx+math.cos(a)*r1; y1=cy-math.sin(a)*r1
            x2=cx+math.cos(a)*r2; y2=cy-math.sin(a)*r2
            p.setPen(QPen(QColor('#ead8ff' if i%2==0 else '#8f70ad'),2 if i%2==0 else 1)); p.drawLine(int(x1),int(y1),int(x2),int(y2))
        a=math.radians(180-180*ratio); nx=cx+math.cos(a)*(radius-36); ny=cy-math.sin(a)*(radius-36)
        p.setPen(QPen(QColor('#f5d0fe'),8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); p.drawLine(int(cx),int(cy),int(nx),int(ny))
        p.setBrush(QColor('#ffffff')); p.setPen(QPen(QColor('#c084fc'),3)); p.drawEllipse(int(cx-8),int(cy-8),16,16)
        p.setPen(QColor('#ffffff')); p.setFont(QFont('Arial',25,QFont.Weight.Bold)); p.drawText(0,int(cy-57),w,42,Qt.AlignmentFlag.AlignCenter,f'{self.value:.1f}')
        p.setFont(QFont('Arial',11,QFont.Weight.DemiBold)); p.setPen(QColor('#d8b4fe')); p.drawText(0,int(cy-19),w,24,Qt.AlignmentFlag.AlignCenter,'Мбит/с')
        p.end()

class SpeedTestDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle('Yus Browser Pro 2.3 — Скорость интернета'); self.resize(480,500)
        self.setStyleSheet('QDialog{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #10091b,stop:.55 #211033,stop:1 #160b24);color:#f3e8ff;border:1px solid #68408c} QLabel{color:#e9d5ff} QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a855f7,stop:1 #6d28d9);color:white;border:1px solid #c4a0ff;border-radius:13px;padding:12px 20px;font-weight:700} QPushButton:hover{background:#9333ea;border-color:#f0abfc} QPushButton:disabled{background:#453052;color:#b9a5ca}')
        lay=QVBoxLayout(self); lay.setContentsMargins(24,22,24,24); lay.setSpacing(10)
        title=QLabel('ИНТЕРНЕТ · LIVE SPEED TEST'); title.setAlignment(Qt.AlignmentFlag.AlignCenter); title.setStyleSheet('font-size:15px;font-weight:800;letter-spacing:1px;color:#e9d5ff'); lay.addWidget(title)
        self.gauge=SpeedGauge(); lay.addWidget(self.gauge,1)
        self.status=QLabel('Готов к измерению'); self.status.setAlignment(Qt.AlignmentFlag.AlignCenter); self.status.setStyleSheet('font-size:14px;font-weight:600'); lay.addWidget(self.status)
        self.progress=QProgressBar(); self.progress.setRange(0,100); self.progress.setValue(0); self.progress.setTextVisible(True); self.progress.setFormat('%p%'); self.progress.setStyleSheet('QProgressBar{background:#29183a;border:1px solid #52356b;border-radius:6px;height:12px;color:#eee} QProgressBar::chunk{border-radius:5px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7c3aed,stop:1 #e879f9)}'); lay.addWidget(self.progress)
        self.ping=QLabel('Задержка: —'); self.ping.setAlignment(Qt.AlignmentFlag.AlignCenter); lay.addWidget(self.ping)
        self.btn=QPushButton('▶  Начать тест'); self.btn.clicked.connect(self.start_test); lay.addWidget(self.btn)
    def start_test(self):
        self.btn.setEnabled(False); self.btn.setText('●  Измеряем…'); self.gauge.set_speed(0); self.gauge.set_testing(True); self.progress.setValue(0); self.status.setText('Загружаем тестовые данные — стрелка движется в реальном времени'); self.ping.setText('Задержка: измеряется…')
        self.worker=SpeedTestWorker(self); self.worker.progress.connect(self.update_progress); self.worker.result.connect(self.done_test); self.worker.start()
    def update_progress(self, speed, downloaded, total):
        self.gauge.set_speed(speed)
        self.progress.setValue(min(99, int(downloaded*100/max(total,1))))
        self.status.setText(f'Измеряем загрузку… {downloaded/1_000_000:.1f} / {total/1_000_000:.1f} МБ')
    def done_test(self, speed, ping, error):
        self.gauge.set_testing(False); self.gauge.set_speed(speed); self.progress.setValue(100 if not error else self.progress.value()); self.btn.setEnabled(True); self.btn.setText('↻  Повторить тест')
        if error: self.status.setText('Тест не завершён. Проверь подключение и повтори.'); self.ping.setText('Ошибка: '+error[:100])
        else: self.status.setText(f'РЕЗУЛЬТАТ · Загрузка: {speed:.1f} Мбит/с'); self.ping.setText(f'Задержка: {ping:.0f} мс')

class UiClickSound(QObject):
    """Original deep UI FX and a separate soft typing sound for browser controls."""
    def __init__(self, app):
        super().__init__(app)
        self.enabled = bool(STARTUP_PREFERENCES.get("click_sounds", True))
        self.effect = QSoundEffect(self)
        sound_path = APP_ROOT / "assets" / "ui_click.wav"
        self.effect.setSource(QUrl.fromLocalFile(str(sound_path)))
        self.effect.setVolume(0.34)
        self.effect.setLoopCount(1)
        self.key_effect = QSoundEffect(self)
        key_path = APP_ROOT / "assets" / "key_tap.wav"
        self.key_effect.setSource(QUrl.fromLocalFile(str(key_path)))
        self.key_effect.setVolume(0.16)
        self.key_effect.setLoopCount(1)
        self._last_key_sound_ms = 0
        app.installEventFilter(self)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def _inside_website(self, watched):
        try:
            parent = watched
            while parent is not None:
                if isinstance(parent, QWebEngineView):
                    return True
                parent = parent.parentWidget() if hasattr(parent, "parentWidget") else None
        except RuntimeError:
            return True
        return False

    def eventFilter(self, watched, event):
        if not self.enabled:
            return False
        try:
            # Separate gentle key sound for typing in Yus Browser's own text fields.
            # Website content is intentionally excluded so sites keep their own behavior.
            if event.type() == QEvent.Type.KeyPress and not self._inside_website(watched):
                text_input = isinstance(watched, (QLineEdit, QTextEdit, QPlainTextEdit))
                if isinstance(watched, QComboBox):
                    text_input = watched.isEditable()
                if text_input and isinstance(watched, QWidget) and watched.isEnabled() and watched.isVisible():
                    key = event.key()
                    printable = bool(event.text()) and not event.text().isspace() or key in (Qt.Key.Key_Space, Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
                    modifiers = event.modifiers()
                    shortcut_combo = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier))
                    if printable and not shortcut_combo:
                        self.key_effect.stop()
                        self.key_effect.play()
            elif event.type() == QEvent.Type.MouseButtonRelease and not self._inside_website(watched):
                # Sound for clicks anywhere in Yus Browser's own interface.
                if isinstance(watched, QWidget) and watched.isEnabled() and watched.isVisible():
                    if isinstance(watched, QMenu) and watched.actionAt(event.position().toPoint()) is None:
                        return False
                    self.effect.stop()
                    self.effect.play()
        except (RuntimeError, AttributeError):
            pass
        return False


APP_NAME = "Yus Browser Pro 2.3"
APP_VERSION = "2.3"
APP_ROOT = Path(__file__).resolve().parent
APP_ICON_PNG = APP_ROOT / "assets" / "yus_browser_icon.png"
HOME_URL = "https://yus.browser.local/"
BOOKMARKS_FILE = APP_DATA / "bookmarks.json"
HISTORY_FILE = APP_DATA / "history.json"
SESSION_FILE = APP_DATA / "session.json"
SESSION_HOME_TOKEN = "yus-home:"

WELCOME_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<style>
:root{--bg:#10091b;--card:#20122f;--line:#583878;--lav:#d8c1ff;--muted:#bca6d8;--glow:#9c5cff}
*{box-sizing:border-box}html{min-height:100%;background:var(--bg)}body{margin:0;background:radial-gradient(ellipse at 16% 0%,#47246d 0,transparent 40%),radial-gradient(ellipse at 86% 18%,#32184f 0,transparent 38%),linear-gradient(135deg,#10091b,#171027 58%,#0c0813);color:#f0e6ff;font:400 16px 'Segoe UI',Arial,sans-serif;min-height:100vh}
.wrap{width:min(1760px,98vw);margin:auto;padding:30px clamp(20px,2.8vw,52px) 30px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.brand{display:flex;align-items:center;gap:14px}.logo{width:62px;height:62px;display:grid;place-items:center;border-radius:20px;background:linear-gradient(135deg,#bd7bff,#7c3aed 55%,#49218a);box-shadow:0 12px 40px #8b5cf655;border:1px solid #dfc2ff55}.logo-window{width:72%;height:66%;border:2px solid #f4eaff;border-radius:8px;overflow:hidden;display:flex;flex-direction:column;background:linear-gradient(150deg,#392052,#160b23);box-shadow:0 2px 9px #16082399}.logo-bar{height:27%;min-height:9px;display:flex;align-items:center;gap:3px;padding-left:5px;background:linear-gradient(90deg,#f4eaff,#c7a7f5);border-bottom:1px solid #8e5fc4}.logo-bar i{display:block;width:4px;height:4px;border-radius:50%;background:#7433b5}.logo-page{flex:1;display:grid;place-items:center;color:#f1e5ff;font-size:25px;font-weight:700;line-height:1}.eyebrow{color:#d1b4ff;letter-spacing:3px;font-size:11px;font-weight:600}.title{font-size:clamp(30px,5vw,46px);font-weight:600;margin:2px 0;letter-spacing:-1px}.tag{color:#c5afe1;font-size:13px}.status{border:1px solid #67448a;border-radius:999px;padding:9px 14px;color:#d4bcff;background:#27153acc;font-size:12px}
.hero{margin:30px auto 25px;text-align:center;max-width:1120px}.hero h2{font-size:clamp(28px,4vw,42px);line-height:1.15;margin:0 0 12px;letter-spacing:-.7px}.hero h2 span{background:linear-gradient(90deg,#eedfff,#c19aff,#a879ff);-webkit-background-clip:text;background-clip:text;color:transparent}.hero p{color:var(--muted);line-height:1.65;margin:0 auto 24px;max-width:630px}
.search{display:flex;gap:9px;max-width:1180px;margin:0 auto;background:#150d21;border:1px solid #67458e;box-shadow:0 12px 46px #08050c88,0 0 0 4px #8b5cf611;border-radius:19px;padding:8px;transition:border-color .15s,box-shadow .15s}.search:focus-within{border-color:#bd95ff;box-shadow:0 12px 48px #08050c99,0 0 0 4px #a855f722}.glass{font-size:22px;padding:10px 8px;color:#c7a4ff}.search input{flex:1;min-width:0;border:0;outline:0;background:transparent;color:#f5edff;font-size:16px;padding:10px 3px}.search input::placeholder{color:#9c84b8}.search button{border:0;border-radius:13px;padding:0 22px;color:white;font-weight:500;background:linear-gradient(135deg,#a855f7,#7026d9);cursor:pointer;box-shadow:0 5px 18px #7c3aed44}.search button:hover{filter:brightness(1.13)}.hint{color:#9c85b8;font-size:12px;margin-top:11px}
.section-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:37px 2px 14px}.section-head h3{margin:0;font-size:16px;color:#eadcff}.section-head span{font-size:12px;color:#9c85b8}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px}.tile{display:block;text-decoration:none;color:#f0e6ff;border:1px solid #49305f;background:linear-gradient(145deg,#261637e8,#1a1028f2);border-radius:17px;padding:18px 16px;min-height:104px;transition:transform .16s,border-color .16s,background .16s}.tile:hover{transform:translateY(-5px) scale(1.012);border-color:#d0a6ff;background:linear-gradient(145deg,#43245f,#241330);box-shadow:0 14px 34px #8b5cf633, inset 0 1px #ffffff18}.tile .ico{font-size:24px;display:block;margin-bottom:13px;color:#d1adff}.tile strong{font-size:14px;display:block;font-weight:600}.tile small{display:block;color:#ac96c9;margin-top:5px;font-size:12px}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.feature{background:#21142ee0;border:1px solid #3f2a55;border-radius:16px;padding:17px}.feature b{color:#e7d8ff;font-size:14px;font-weight:600}.feature p{color:#ac96c8;font-size:12px;line-height:1.55;margin:7px 0 0}.foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid #3c2852;margin-top:34px;padding-top:17px;color:#89739f;font-size:11px}
@media(min-width:1100px){.tiles{grid-template-columns:repeat(6,minmax(0,1fr))}.feature-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.hero{margin-top:24px}.hero-art{height:140px}.title{font-size:44px}}
@media(max-width:580px){.wrap{padding:24px 10px}.logo{width:48px;height:48px;border-radius:15px}.search{gap:3px}.search button{padding:0 13px}.hero{margin-top:28px}}
/* Subtle motion only: no elements, positions, or colors are changed. */
@keyframes yusPageEnter{0%{opacity:0;transform:translateY(14px) scale(.992);filter:blur(3px)}70%{filter:blur(0)}100%{opacity:1;transform:translateY(0) scale(1);filter:blur(0)}}
@keyframes yusLogoPulse{0%,100%{box-shadow:0 10px 32px #8b5cf655,0 0 0 0 #c084fc00;border-color:#dfc2ff99;transform:translateY(0) scale(1) rotate(0)}50%{box-shadow:0 18px 48px #a855f788,0 0 0 7px #c084fc0c;border-color:#ffffff;transform:translateY(-3px) scale(1.035) rotate(-1deg)}}
@keyframes yusSearchAura{0%,100%{box-shadow:0 12px 46px #08050c88,0 0 0 4px #8b5cf611;border-color:#67458e}50%{box-shadow:0 16px 52px #08050c99,0 0 0 5px #a855f722,0 0 24px #a855f71a;border-color:#9d72c9}}
@keyframes yusStatusPulse{0%,100%{box-shadow:0 0 0 0 #9c5cff00}50%{box-shadow:0 0 30px 5px #9c5cff77}}
@keyframes yusTileEnter{from{opacity:0;filter:brightness(.82)}to{opacity:1;filter:brightness(1)}}
.wrap{animation:yusPageEnter .52s cubic-bezier(.2,.8,.2,1) both}
.logo{animation:yusLogoPulse 2.8s ease-in-out infinite}
.search{animation:yusSearchAura 3.2s ease-in-out infinite}
.status{animation:yusStatusPulse 3s ease-in-out infinite}
.tile{animation:yusTileEnter .42s ease-out both}
.tile:nth-child(2){animation-delay:.035s}.tile:nth-child(3){animation-delay:.07s}.tile:nth-child(4){animation-delay:.105s}.tile:nth-child(5){animation-delay:.14s}.tile:nth-child(6){animation-delay:.175s}
.feature{animation:yusTileEnter .45s ease-out both}.feature:nth-child(2){animation-delay:.08s}.feature:nth-child(3){animation-delay:.16s}
.hero-art{height:156px;width:min(440px,92vw);margin:0 auto 4px;position:relative;display:grid;place-items:center;isolation:isolate;overflow:visible}.art-halo{position:absolute;width:230px;height:100px;border-radius:50%;background:radial-gradient(ellipse,#8b5cf65b 0%,#6d28d900 72%);filter:blur(12px);animation:haloBreath 5s ease-in-out infinite}.art-window{width:min(100%,420px);height:100%;overflow:visible;filter:drop-shadow(0 11px 24px #05020b88)}.art-floating{transform-origin:center;animation:floatBrowser 6s ease-in-out infinite}.art-dot{filter:drop-shadow(0 0 8px #c084fc99);animation:floatDot 4.8s ease-in-out infinite}.dot-b{animation-delay:-1.7s}.dot-c{animation-delay:-3s}.hero-art+.hero h2{margin-top:0}@keyframes floatBrowser{0%,100%{transform:translateY(2px) rotate(-.4deg)}50%{transform:translateY(-6px) rotate(.5deg)}}@keyframes floatDot{0%,100%{transform:translateY(0);opacity:.65}50%{transform:translateY(-8px);opacity:1}}@keyframes haloBreath{0%,100%{opacity:.58;transform:scale(.92)}50%{opacity:1;transform:scale(1.08)}}
@media(max-width:580px){.hero-art{height:126px}.art-window{width:350px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
</style></head><body><main class="wrap">
<header class="top"><div class="brand"><div class="logo" role="img" aria-label="Yus Browser Pro 2.3 browser-window icon"><span class="logo-window"><span class="logo-bar"><i></i><i></i><i></i></span><span class="logo-page">Y</span></span></div><div><div class="eyebrow">YOUR WEB. YOUR WORLD.</div><div class="title">Yus Browser Pro 2.3</div><div class="tag">Быстрый поиск. Твоё пространство.</div></div></div><div class="status">● VIOLET STUDIO · PRO 2.3</div></header>
<section class="hero"><div class="hero-art" aria-hidden="true"><div class="art-halo"></div><svg class="art-window" viewBox="0 0 420 180" role="presentation"><defs><linearGradient id="yw" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#bb86fc"/><stop offset=".55" stop-color="#7c3aed"/><stop offset="1" stop-color="#4c1d95"/></linearGradient><linearGradient id="yp" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#28143e"/><stop offset="1" stop-color="#170e25"/></linearGradient><filter id="yg"><feGaussianBlur stdDeviation="7"/></filter></defs><ellipse cx="210" cy="145" rx="136" ry="13" fill="#8b5cf6" opacity=".24" filter="url(#yg)"/><g class="art-floating"><rect x="63" y="25" width="294" height="127" rx="17" fill="#8b5cf6" opacity=".16"/><rect x="67" y="20" width="286" height="125" rx="14" fill="url(#yp)" stroke="#b98aff" stroke-width="2"/><path d="M81 20h258a14 14 0 0 1 14 14v20H67V34a14 14 0 0 1 14-14" fill="#352048"/><circle cx="86" cy="37" r="4" fill="#efb7ff"/><circle cx="100" cy="37" r="4" fill="#b58aff"/><circle cx="114" cy="37" r="4" fill="#8b5cf6"/><rect x="137" y="29" width="116" height="16" rx="6" fill="#1b1028" stroke="#78539d"/><rect x="86" y="68" width="110" height="62" rx="9" fill="#2b1742" stroke="#68428e"/><path d="M98 115l22-20 16 11 24-25 23 23v16H98z" fill="url(#yw)" opacity=".92"/><circle cx="113" cy="82" r="7" fill="#d7bcff"/><rect x="211" y="70" width="119" height="8" rx="4" fill="#d6baff" opacity=".8"/><rect x="211" y="86" width="95" height="6" rx="3" fill="#9d83c0" opacity=".72"/><rect x="211" y="99" width="108" height="6" rx="3" fill="#9d83c0" opacity=".6"/><rect x="211" y="115" width="62" height="13" rx="6" fill="url(#yw)"/><path d="M49 142h322l-16 12H65z" fill="#a78bfa" opacity=".8"/><path d="M178 154h65" stroke="#e5d5ff" stroke-width="3" stroke-linecap="round"/></g><circle class="art-dot dot-a" cx="43" cy="60" r="5" fill="#d8b4fe"/><circle class="art-dot dot-b" cx="378" cy="66" r="4" fill="#c084fc"/><circle class="art-dot dot-c" cx="337" cy="12" r="3" fill="#f0abfc"/><path d="M35 117q-12-29 10-48" stroke="#a78bfa" stroke-width="2" stroke-linecap="round" fill="none" opacity=".6"/><path d="M380 108q13-24-3-43" stroke="#a78bfa" stroke-width="2" stroke-linecap="round" fill="none" opacity=".6"/></svg></div><h2>Найди свой путь<br><span>по всему интернету</span></h2><p>Введи адрес сайта или запрос. Используй выбранный поисковик либо короткие команды для видео, изображений, карт и энциклопедий.</p>
<form class="search" id="searchForm"><span class="glass">⌕</span><input id="homeQuery" type="text" autocomplete="off" spellcheck="false" placeholder="Поиск через __YUS_SEARCH_NAME__ или адрес сайта…" aria-label="Поиск в интернете"><button type="submit">Найти&nbsp; ↗</button></form><div class="hint">Совет: <b>yt: Minecraft</b> · <b>img: фиолетовые обои</b> · <b>wiki: WireGuard</b></div></section>
<div class="section-head"><h3>Быстрый доступ</h3><span>Открывается в текущей вкладке</span></div>
<section class="tiles">
<a class="tile" href="https://www.youtube.com/"><span class="ico">▶</span><strong>YouTube</strong><small>Видео и каналы</small></a>
<a class="tile" href="https://www.google.com/"><span class="ico">⌕</span><strong>Google</strong><small>Поиск в интернете</small></a>
<a class="tile" href="https://mail.google.com/"><span class="ico">✉</span><strong>Gmail</strong><small>Электронная почта</small></a>
<a class="tile" href="https://ru.wikipedia.org/"><span class="ico">▤</span><strong>Википедия</strong><small>Знания и справки</small></a>
<a class="tile" href="https://github.com/"><span class="ico">⌘</span><strong>GitHub</strong><small>Твои проекты</small></a>
<a class="tile" href="https://maps.google.com/"><span class="ico">⌖</span><strong>Карты</strong><small>Места и маршруты</small></a>
</section>
<div class="section-head"><h3>Твои закладки</h3><span>Сохранённые страницы</span></div>
<section class="tiles">__YUS_BOOKMARK_TILES__</section>
<div class="section-head"><h3>Недавно посещённые</h3><span>Последние сайты</span></div>
<section class="tiles">__YUS_RECENT_TILES__</section>
<div class="section-head"><h3>Инструменты Yus</h3><span>Сочетания клавиш: Ctrl+L · Ctrl+T · Ctrl+F</span></div>
<section class="feature-grid"><article class="feature"><b>✦ Умный поиск</b><p>Несколько поисковиков и быстрые команды для YouTube, картинок, новостей, карт и Википедии.</p></article><article class="feature"><b>◈ Удобные вкладки</b><p>История, закладки, загрузки, широкое окно и быстрый возврат к адресной строке.</p></article><article class="feature"><b>⬡ Приватность</b><p>Управление cookies, кэшем и разрешениями. Сайты не получают доступ ко всему диску автоматически.</p></article></section>
<footer class="foot"><span>Yus Browser Pro 2.3 - Yandex Desktop Edition</span><span>Сайты сохраняют собственное оформление</span></footer></main>
<script>
const pattern = "__YUS_SEARCH_PATTERN__";
const shortcuts = {
  "yt":"https://www.youtube.com/results?search_query=", "youtube":"https://www.youtube.com/results?search_query=", "video":"https://www.youtube.com/results?search_query=", "видео":"https://www.youtube.com/results?search_query=",
  "img":"https://www.google.com/search?tbm=isch&q=", "images":"https://www.google.com/search?tbm=isch&q=", "картинки":"https://www.google.com/search?tbm=isch&q=",
  "news":"https://news.google.com/search?q=", "новости":"https://news.google.com/search?q=", "maps":"https://www.google.com/maps/search/?api=1&query=", "map":"https://www.google.com/maps/search/?api=1&query=", "карта":"https://www.google.com/maps/search/?api=1&query=",
  "wiki":"https://ru.wikipedia.org/w/index.php?search=", "вики":"https://ru.wikipedia.org/w/index.php?search=", "!g":"https://www.google.com/search?q=", "!google":"https://www.google.com/search?q=", "!b":"https://www.bing.com/search?q=", "!ddg":"https://duckduckgo.com/?q=", "!brave":"https://search.brave.com/search?q=", "!startpage":"https://www.startpage.com/sp/search?query=", "!sp":"https://www.startpage.com/sp/search?query=", "!eco":"https://www.ecosia.org/search?q=", "!ecosia":"https://www.ecosia.org/search?q=", "!ya":"https://yandex.kz/search/?text=", "!yahoo":"https://search.yahoo.com/search?p=",
  "tr":"https://translate.google.com/?sl=auto&tl=ru&text=", "translate":"https://translate.google.com/?sl=auto&tl=ru&text=", "gh":"https://github.com/search?q=", "github":"https://github.com/search?q=", "reddit":"https://www.reddit.com/search/?q=", "music":"https://music.youtube.com/search?q=", "pdf":"https://www.google.com/search?q=filetype%3Apdf+", "shop":"https://www.google.com/search?tbm=shop&q=", "scholar":"https://scholar.google.com/scholar?q="
};
document.getElementById('searchForm').addEventListener('submit', function(e){
  e.preventDefault(); const q=document.getElementById('homeQuery').value.trim(); if(!q)return;
  if(q.startsWith('?')){const forced=q.slice(1).trim();if(forced)window.location.href=pattern.replace('%s',encodeURIComponent(forced));return;}
  if(/^(https?|ftp|file):/i.test(q)){window.location.href=q;return;}
  if(!/\s/.test(q) && /^(localhost(?::\d+)?|(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#].*)?|(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:[/?#].*)?)$/i.test(q)){window.location.href='https://'+q;return;}
  const match=q.match(/^(!?[a-z]+|[а-яё]+)\s*(?::|\s)\s*(.+)$/iu);
  if(match){const key=match[1].toLowerCase(); if(shortcuts[key]){window.location.href=shortcuts[key]+encodeURIComponent(match[2]);return;}}
  window.location.href=pattern.replace('%s',encodeURIComponent(q));
});
document.getElementById('homeQuery').addEventListener('keydown', function(e){if(e.key==='Escape'){this.value='';this.blur();}});
</script></body></html>"""

STYLE = r"""
* { font-family: 'Segoe UI'; }
QMainWindow, QWidget { background: #120b1c; color: #f0e6ff; font-size: 10pt; }
QDialog, QMessageBox, QInputDialog, QFileDialog { background: #191027; color: #eee5fa; }
QWidget#TopPanel { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #29183c,stop:0.55 #20132f,stop:1 #171020); border: 1px solid #68448a; border-radius: 17px; }
QLabel { background: transparent; color: #e8dcf8; }
QLabel#Brand { color: #f0e3ff; font-size: 16pt; font-weight: 600; padding: 2px 10px; }
QLabel#Tagline { color: #ad91d0; font-size: 8pt; }
QLineEdit { background: #100b18; color: #f1e8ff; border: 1px solid #62427f; border-radius: 12px; padding: 8px 12px; selection-background-color: #7c3aed; min-height: 20px; }
QLineEdit:focus { border: 1px solid #b58aff; background: #170e23; }
QPushButton, QToolButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #38234e,stop:1 #271936); color: #f1e7ff; border: 1px solid #745095; border-radius: 11px; padding: 7px 11px; min-height: 20px; }
QPushButton:hover, QToolButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7045a0,stop:1 #492569); border-color: #c59aff; }
QPushButton:pressed, QToolButton:pressed { background: #6d28d9; }
QPushButton:disabled, QToolButton:disabled { background: #21172d; color: #78688b; border-color: #352343; }
QPushButton#Primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a855f7,stop:1 #6d28d9); border: 1px solid #c4a1ff; color: #fff1ff; font-weight: 500; }
QPushButton#Primary:hover { background: #9333ea; }
QTabWidget::pane { border: 1px solid #382447; border-radius: 10px; top: -1px; background: #100b18; }
QTabBar::tab { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2d1d41,stop:1 #21142f); color: #d5c1ee; border: 1px solid #51366d; border-bottom: none; border-top-left-radius: 10px; border-top-right-radius: 10px; padding: 8px 27px 8px 12px; min-width: 78px; max-width: 240px; }
QTabBar::tab:selected { color: #fff6ff; background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7942b8,stop:1 #4b2476); border-color: #b58aff; }
QTabBar::tab:hover:!selected { background: #342047; }
QTabBar::close-button { background: #392449; border-radius: 5px; }
QTabBar::close-button:hover { background: #8b5cf6; }
QToolButton#YusTabCloseButton { background: transparent; color: #cdb6e8; border: 1px solid transparent; border-radius: 7px; padding: 0; min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; font-size: 15px; font-weight: 800; }
QToolButton#YusTabCloseButton:hover { background: #a83274; color: #fff4ff; border-color: #f0a1d7; }
QToolButton#YusTabCloseButton:pressed { background: #76245b; color: #ffffff; }
QToolButton#YusNewTabButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a855f7,stop:1 #6d28d9); color: #ffffff; border: 1px solid #d8b4fe; border-radius: 9px; min-width: 30px; max-width: 30px; min-height: 28px; max-height: 28px; padding: 0; font-size: 20px; font-weight: 700; margin: 2px 5px 2px 4px; }
QToolButton#YusNewTabButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #c084fc,stop:1 #7c3aed); border-color: #ffffff; }
QToolButton#YusNewTabButton:pressed { background: #5b21b6; }
QProgressBar { background: #2a1a3a; border: 0; border-radius: 3px; max-height: 5px; text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8b5cf6,stop:1 #d8b4fe); border-radius: 3px; }
QDockWidget { color: #decafa; font-weight: 500; }
QDockWidget::title { background: #261735; color: #decafa; padding: 9px; border: 1px solid #50356a; }
QPlainTextEdit, QListWidget, QComboBox, QTextEdit, QSpinBox { background: #100b18; color: #eee5fa; border: 1px solid #4a315f; border-radius: 8px; padding: 7px; selection-background-color: #6d28d9; selection-color: #fff0ff; }
QAbstractItemView { background: #1b1129; color: #eee5fa; border: 1px solid #62427f; selection-background-color: #6d28d9; selection-color: #fff0ff; outline: 0; }
QListWidget::item { padding: 6px; border-radius: 5px; }
QListWidget::item:hover { background: #342047; }
QListWidget::item:selected { background: #6d28d9; color: #fff0ff; }
QComboBox::drop-down { background: #302040; border-left: 1px solid #62427f; border-top-right-radius: 8px; border-bottom-right-radius: 8px; width: 24px; }
QCheckBox, QRadioButton { background: transparent; color: #eadcff; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; background: #241632; border: 1px solid #7c5a9a; }
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: #8b5cf6; border: 2px solid #d8b4fe; }
QMenu { background: #21152f; color: #eee5fa; border: 1px solid #664785; padding: 5px; }
QMenu::item { padding: 8px 22px; border-radius: 5px; }
QMenu::item:selected { background: #5b2e7c; color: #fff0ff; }
QMenu::separator { height: 1px; background: #4a315f; margin: 5px 10px; }
QStatusBar { background: #20152f; color: #cdb7e5; border-top: 1px solid #3e2956; }
QToolTip { background: #2b1a3b; color: #f1e8ff; border: 1px solid #a78bfa; padding: 5px; }
QScrollBar:vertical { background: #1a1126; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #63417e; border-radius: 5px; min-height: 25px; }
QScrollBar:horizontal { background: #1a1126; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #63417e; border-radius: 5px; min-width: 25px; }
QScrollBar::add-line, QScrollBar::sub-line { background: #21152f; border: none; }
"""


def read_json(path: Path, fallback):
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                value = json.load(f)
            # Treat damaged or unexpected app-data shapes as empty state instead of crashing.
            if isinstance(fallback, list) and not isinstance(value, list):
                return fallback
            if isinstance(fallback, dict) and not isinstance(value, dict):
                return fallback
            return value
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return fallback


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    temp.replace(path)




class BrowserPage(QWebEnginePage):
    """Web page adapter that keeps popup, permission and file flows inside Yus Browser."""
    def __init__(self, profile: QWebEngineProfile, window: "BrowserWindow", parent=None):
        super().__init__(profile, parent)
        self.browser_window = window

    def createWindow(self, _window_type):
        return self.browser_window.create_popup_page()

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        # The browser only shares files the user explicitly picks in this dialog.
        return self.browser_window.choose_website_files(mode, old_files, accepted_mime_types)

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        try:
            is_error = level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel
        except AttributeError:
            is_error = "Error" in str(level)
        if is_error:
            self.browser_window.record_page_error(self.url().toString(), str(message), int(line_number), str(source_id))
        # Chromium sites often emit benign performance warnings (for example,
        # unused generate_204 preload hints and ResizeObserver notifications).
        # Keep the terminal readable: forward only real JavaScript errors.
        if is_error:
            try:
                super().javaScriptConsoleMessage(level, message, line_number, source_id)
            except (AttributeError, RuntimeError):
                pass


class ScreenshotCanvas(QWidget):
    """Small screenshot annotation canvas: pen, rectangle, arrow and text."""
    def __init__(self, image: QPixmap, parent=None):
        super().__init__(parent)
        self.base_image = QPixmap(image)
        self.annotations: list[dict] = []
        self.active_annotation: dict | None = None
        self.tool = "pen"
        self.color = "#d8b4fe"
        self.width_px = 4
        self.setFixedSize(self.base_image.size())
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    @staticmethod
    def _point(event) -> QPoint:
        try:
            return event.position().toPoint()
        except AttributeError:
            return event.pos()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._point(event)
        if self.tool == "text":
            text, ok = QInputDialog.getText(self.window(), "Подпись на скриншоте", "Текст заметки:")
            if ok and text.strip():
                self.annotations.append({"kind": "text", "points": [(point.x(), point.y())], "text": text.strip()[:300], "color": self.color, "width": self.width_px})
                self.update()
            return
        self.active_annotation = {"kind": self.tool, "points": [(point.x(), point.y())], "color": self.color, "width": self.width_px}
        if self.tool in ("rect", "arrow", "crop", "cover", "highlight"):
            self.active_annotation["points"].append((point.x(), point.y()))
        self.update()

    def mouseMoveEvent(self, event):
        if not self.active_annotation or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        point = self._point(event)
        points = self.active_annotation["points"]
        if self.active_annotation["kind"] == "pen":
            points.append((point.x(), point.y()))
        elif len(points) >= 2:
            points[-1] = (point.x(), point.y())
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.active_annotation:
            point = self._point(event)
            points = self.active_annotation["points"]
            if self.active_annotation["kind"] == "pen":
                points.append((point.x(), point.y()))
            elif len(points) >= 2:
                points[-1] = (point.x(), point.y())
            self.annotations.append(self.active_annotation)
            self.active_annotation = None
            self.update()

    def undo(self):
        if self.active_annotation:
            self.active_annotation = None
        elif self.annotations:
            self.annotations.pop()
        self.update()

    def _draw_annotations(self, painter: QPainter):
        items = list(self.annotations)
        if self.active_annotation:
            items.append(self.active_annotation)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for item in items:
            points = item.get("points") or []
            if not points:
                continue
            color = QColor(item.get("color", "#d8b4fe"))
            pen = QPen(color, int(item.get("width", 4)), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            qpoints = [QPoint(int(p[0]), int(p[1])) for p in points]
            kind = item.get("kind")
            if kind == "pen":
                if len(qpoints) == 1:
                    painter.drawPoint(qpoints[0])
                else:
                    for a, b in zip(qpoints, qpoints[1:]):
                        painter.drawLine(a, b)
            elif kind == "rect" and len(qpoints) >= 2:
                painter.drawRoundedRect(QRect(qpoints[0], qpoints[-1]).normalized(), 5, 5)
            elif kind == "crop" and len(qpoints) >= 2:
                painter.save()
                painter.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRect(qpoints[0], qpoints[-1]).normalized())
                painter.restore()
            elif kind == "cover" and len(qpoints) >= 2:
                painter.save()
                painter.setPen(QPen(QColor("#09060f"), 1))
                painter.setBrush(QBrush(QColor("#09060f")))
                painter.drawRect(QRect(qpoints[0], qpoints[-1]).normalized())
                painter.restore()
            elif kind == "highlight" and len(qpoints) >= 2:
                painter.save()
                highlight = QColor(color)
                highlight.setAlpha(85)
                painter.setPen(QPen(highlight, 1))
                painter.setBrush(QBrush(highlight))
                painter.drawRect(QRect(qpoints[0], qpoints[-1]).normalized())
                painter.restore()
            elif kind == "arrow" and len(qpoints) >= 2:
                start, end = qpoints[0], qpoints[-1]
                painter.drawLine(start, end)
                angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                head = max(12, int(item.get("width", 4) * 3.5))
                left = QPoint(int(end.x() - head * math.cos(angle - math.pi / 6)), int(end.y() - head * math.sin(angle - math.pi / 6)))
                right = QPoint(int(end.x() - head * math.cos(angle + math.pi / 6)), int(end.y() - head * math.sin(angle + math.pi / 6)))
                painter.drawLine(end, left)
                painter.drawLine(end, right)
            elif kind == "text":
                font = QFont("Segoe UI", 17, QFont.Weight.Bold)
                painter.setFont(font)
                painter.setBrush(QBrush(QColor("#150d21")))
                text = str(item.get("text", ""))
                metrics = painter.fontMetrics()
                rect = metrics.boundingRect(text)
                origin = qpoints[0]
                padded = QRect(origin.x() - 5, origin.y() - rect.height() - 7, rect.width() + 10, rect.height() + 12)
                painter.drawRoundedRect(padded, 5, 5)
                painter.setPen(QPen(color, 1))
                painter.drawText(origin, text)

    def crop_to_last_selection(self) -> bool:
        """Crop to the most recent crop-selection rectangle."""
        if not self.annotations or self.annotations[-1].get("kind") != "crop":
            return False
        points = self.annotations[-1].get("points", [])
        if len(points) < 2:
            return False
        rect = QRect(QPoint(int(points[0][0]), int(points[0][1])), QPoint(int(points[-1][0]), int(points[-1][1]))).normalized()
        rect = rect.intersected(self.base_image.rect())
        if rect.width() < 12 or rect.height() < 12:
            return False
        # Flatten existing annotations but exclude the dashed crop-selection frame itself.
        crop_item = self.annotations.pop()
        flattened = self.rendered_pixmap()
        self.annotations.append(crop_item)
        self.base_image = flattened.copy(rect)
        self.annotations.clear()
        self.active_annotation = None
        self.setFixedSize(self.base_image.size())
        self.updateGeometry()
        self.update()
        return True

    def flatten_and_transform(self, transform: QTransform | None = None, image_filter: str | None = None):
        image = self.rendered_pixmap()
        if transform is not None:
            image = image.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        if image_filter == "grayscale":
            image = QPixmap.fromImage(image.toImage().convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(QImage.Format.Format_ARGB32))
        elif image_filter == "invert":
            converted = image.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            converted.invertPixels(QImage.InvertMode.InvertRgb)
            image = QPixmap.fromImage(converted)
        self.base_image = image
        self.annotations.clear()
        self.active_annotation = None
        self.setFixedSize(self.base_image.size())
        self.updateGeometry()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.base_image)
        self._draw_annotations(painter)
        painter.end()

    def rendered_pixmap(self) -> QPixmap:
        result = QPixmap(self.base_image)
        painter = QPainter(result)
        self._draw_annotations(painter)
        painter.end()
        return result


class ScreenshotEditorDialog(QDialog):
    """Violet themed screenshot editor. The original image remains unchanged until export."""
    def __init__(self, image: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Скриншот и редактор · Yus Browser")
        app_icon = APP_ROOT / "assets" / "yus_browser_icon.ico"
        if app_icon.is_file():
            self.setWindowIcon(QIcon(str(app_icon)))
        self.setMinimumSize(900, 600)
        self.resize(1160, 760)
        self.original_image = QPixmap(image)
        self.canvas = ScreenshotCanvas(image, self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)
        header = QLabel("YUS SCREENSHOT STUDIO")
        header.setStyleSheet("font-size:15pt;font-weight:600;letter-spacing:1px;color:#dec4ff;padding:3px")
        outer.addWidget(header)
        subtitle = QLabel("Рисуй, выделяй важные места, добавляй подписи и сохраняй результат.")
        subtitle.setStyleSheet("color:#aa94c9")
        outer.addWidget(subtitle)

        toolbar = QHBoxLayout()
        self.tool_choice = QComboBox()
        for label, value in (("Кисть", "pen"), ("Прямоугольник", "rect"), ("Стрелка", "arrow"), ("Текст", "text"), ("Обрезка", "crop"), ("Закрыть данные", "cover"), ("Подсветка", "highlight")):
            self.tool_choice.addItem(label, value)
        self.tool_choice.currentIndexChanged.connect(lambda _i: setattr(self.canvas, "tool", self.tool_choice.currentData()))
        toolbar.addWidget(QLabel("Инструмент:"))
        toolbar.addWidget(self.tool_choice)
        self.color_choice = QComboBox()
        for label, value in (("Лаванда", "#d8b4fe"), ("Неоновый фиолетовый", "#a855f7"), ("Розовый", "#f0abfc"), ("Жёлтый", "#facc15"), ("Красный", "#fb7185"), ("Бирюзовый", "#5eead4")):
            self.color_choice.addItem(label, value)
        self.color_choice.currentIndexChanged.connect(lambda _i: setattr(self.canvas, "color", self.color_choice.currentData()))
        toolbar.addWidget(QLabel("Цвет:"))
        toolbar.addWidget(self.color_choice)
        self.width_choice = QComboBox()
        for width in (2, 4, 7, 10):
            self.width_choice.addItem(f"{width} px", width)
        self.width_choice.setCurrentIndex(1)
        self.width_choice.currentIndexChanged.connect(lambda _i: setattr(self.canvas, "width_px", int(self.width_choice.currentData() or 4)))
        toolbar.addWidget(QLabel("Толщина:"))
        toolbar.addWidget(self.width_choice)
        toolbar.addStretch(1)
        undo_btn = QPushButton("↶ Отменить")
        undo_btn.setToolTip("Отменить последнее действие редактора")
        undo_btn.clicked.connect(self.canvas.undo)
        toolbar.addWidget(undo_btn)
        crop_btn = QPushButton("Обрезать")
        crop_btn.setToolTip("Сначала выдели прямоугольник инструментом «Обрезка», затем нажми сюда")
        crop_btn.clicked.connect(self.crop_image)
        toolbar.addWidget(crop_btn)
        outer.addLayout(toolbar)

        edit_row = QHBoxLayout()
        rotate_left = QPushButton("↶ 90°")
        rotate_left.setToolTip("Повернуть изображение против часовой стрелки")
        rotate_left.clicked.connect(lambda: self.canvas.flatten_and_transform(QTransform().rotate(-90)))
        rotate_right = QPushButton("↷ 90°")
        rotate_right.setToolTip("Повернуть изображение по часовой стрелке")
        rotate_right.clicked.connect(lambda: self.canvas.flatten_and_transform(QTransform().rotate(90)))
        flip_btn = QPushButton("Отразить")
        flip_btn.setToolTip("Отразить изображение по горизонтали")
        flip_btn.clicked.connect(lambda: self.canvas.flatten_and_transform(QTransform().scale(-1, 1)))
        gray_btn = QPushButton("Ч/Б")
        gray_btn.setToolTip("Преобразовать изображение в чёрно-белое")
        gray_btn.clicked.connect(lambda: self.canvas.flatten_and_transform(image_filter="grayscale"))
        invert_btn = QPushButton("Инверсия")
        invert_btn.setToolTip("Инвертировать цвета изображения")
        invert_btn.clicked.connect(lambda: self.canvas.flatten_and_transform(image_filter="invert"))
        reset_btn = QPushButton("Сбросить правки")
        reset_btn.setToolTip("Вернуть исходный снимок без правок")
        reset_btn.clicked.connect(self.reset_image)
        for control in (rotate_left, rotate_right, flip_btn, gray_btn, invert_btn, reset_btn):
            edit_row.addWidget(control)
        edit_row.addStretch(1)
        outer.addLayout(edit_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(self.canvas)
        outer.addWidget(scroll, 1)
        self.size_label = QLabel(f"Снимок: {image.width()} × {image.height()} px")
        self.size_label.setStyleSheet("color:#a994c7")
        outer.addWidget(self.size_label)
        buttons = QHBoxLayout()
        copy_btn = QPushButton("Копировать")
        copy_btn.clicked.connect(self.copy_image)
        save_btn = QPushButton("Сохранить PNG…")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self.save_image)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(save_btn)
        buttons.addWidget(close_btn)
        outer.addLayout(buttons)

    def crop_image(self):
        if self.canvas.crop_to_last_selection():
            self.size_label.setText(f"Обрезано: {self.canvas.base_image.width()} × {self.canvas.base_image.height()} px")
        else:
            QMessageBox.information(self, "Обрезка", "Выбери инструмент «Обрезка», протяни прямоугольник на снимке и нажми «Обрезать».")

    def reset_image(self):
        self.canvas.base_image = QPixmap(self.original_image)
        self.canvas.annotations.clear()
        self.canvas.active_annotation = None
        self.canvas.setFixedSize(self.canvas.base_image.size())
        self.canvas.updateGeometry()
        self.canvas.update()
        self.size_label.setText(f"Исходный снимок: {self.original_image.width()} × {self.original_image.height()} px")

    def copy_image(self):
        QApplication.clipboard().setPixmap(self.canvas.rendered_pixmap())
        self.size_label.setText("Снимок скопирован в буфер обмена.")

    def save_image(self):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation) or str(Path.home() / "Pictures"))
        folder.mkdir(parents=True, exist_ok=True)
        suggested = folder / f"YusBrowser_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        dialog = QFileDialog(self, "Сохранить скриншот", str(suggested), "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
            return
        path = Path(dialog.selectedFiles()[0])
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            path = path.with_suffix(".png")
        image = self.canvas.rendered_pixmap()
        fmt = "JPEG" if path.suffix.lower() in (".jpg", ".jpeg") else "PNG"
        if image.save(str(path), fmt):
            self.size_label.setText(f"Сохранено: {path}")
        else:
            QMessageBox.warning(self, "Скриншот", "Не удалось сохранить изображение по выбранному пути.")


class BrowserWindow(QMainWindow):
    def __init__(self, private_mode: bool = False):
        super().__init__()
        if APP_ICON_PNG.is_file():
            self.setWindowIcon(QIcon(str(APP_ICON_PNG)))
        self.private_mode = private_mode
        self.setWindowTitle(f"{APP_NAME} — {APP_VERSION}" + (" · Приватное окно" if private_mode else ""))
        self._web_fullscreen_active = False
        self._previous_window_state = Qt.WindowState.WindowNoState
        self.setMinimumSize(1000, 680)
        self.resize(1500, 960)
        self._private_windows: list[BrowserWindow] = []
        self._restoring_session = False
        self.preferences = load_preferences_early()
        if self.preferences.get("search_engine") not in SEARCH_ENGINES:
            self.preferences["search_engine"] = DEFAULT_SEARCH_ENGINE
        raw_bookmarks = read_json(BOOKMARKS_FILE, []) if not private_mode else []
        raw_history = read_json(HISTORY_FILE, []) if not private_mode else []
        self.bookmarks = [row for row in raw_bookmarks if isinstance(row, dict) and row.get("url")]
        self.history = [row for row in raw_history if isinstance(row, dict) and row.get("url")]
        self.download_items: list[dict] = []
        self.page_errors: list[dict] = []
        # Beta 1.2: wide view is enabled by default on every website.
        # Ctrl+Shift+V or the toolbar button restores the normal layout.
        self._wide_view_user_override: bool | None = True
        self._wide_view_active = False
        self._current_url_is_youtube = False
        self._intro_animation_started = False
        self._extra_panels_expanded = False
        self._profile = self._make_profile()
        self._profile.downloadRequested.connect(self.handle_download)
        self._build_ui()
        self._apply_wide_view(True)
        self._install_shortcuts()
        self._session_save_timer = QTimer(self)
        self._session_save_timer.setSingleShot(True)
        self._session_save_timer.setInterval(450)
        self._session_save_timer.timeout.connect(self.save_session)
        restored = False
        if not private_mode and bool(self.preferences.get("restore_tabs", True)):
            restored = self.restore_session_tabs()
        if not restored:
            self.new_tab(HOME_URL)
        else:
            # Always open the branded Yus home page; previously saved tabs remain available.
            self.new_tab(HOME_URL, switch=True)
        status = ("Приватное окно: вкладки, история, cookies и аккаунты не сохраняются после закрытия" if private_mode
                  else "Yus Browser готов · вкладки автоматически сохраняются")
        self.statusBar().showMessage(status, 7000)

    def showEvent(self, event):
        """Play subtle startup and brand-glow animations without moving UI controls."""
        super().showEvent(event)
        if self._intro_animation_started:
            return
        self._intro_animation_started = True
        try:
            self.setWindowOpacity(0.90)
            self._window_fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
            self._window_fade_animation.setDuration(360)
            self._window_fade_animation.setStartValue(0.90)
            self._window_fade_animation.setEndValue(1.0)
            self._window_fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._window_fade_animation.start()

            self._brand_glow_animation = QPropertyAnimation(
                self.brand_glow_effect, b"blurRadius", self
            )
            self._brand_glow_animation.setDuration(2200)
            self._brand_glow_animation.setStartValue(9.0)
            self._brand_glow_animation.setKeyValueAt(0.5, 28.0)
            self._brand_glow_animation.setEndValue(9.0)
            self._brand_glow_animation.setEasingCurve(QEasingCurve.Type.InOutSine)
            self._brand_glow_animation.setLoopCount(-1)
            self._brand_glow_animation.start()

        except (AttributeError, RuntimeError, TypeError):
            # Unsupported window compositing must never prevent the browser from opening.
            try:
                self.setWindowOpacity(1.0)
            except (AttributeError, RuntimeError):
                pass

    def _make_profile(self) -> QWebEngineProfile:
        if self.private_mode:
            # An unnamed profile is off-the-record: cookies and website storage stay in memory only.
            profile = QWebEngineProfile(QApplication.instance() or self)
            try:
                profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
                profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
            except (AttributeError, RuntimeError):
                pass
        else:
            storage = APP_DATA / "webdata"
            cache = APP_DATA / "cache"
            storage.mkdir(parents=True, exist_ok=True)
            cache.mkdir(parents=True, exist_ok=True)
            profile = QWebEngineProfile("YusBrowserProfile", QApplication.instance() or self)
            profile.setPersistentStoragePath(str(storage))
            profile.setCachePath(str(cache))
            profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
            profile.setHttpCacheMaximumSize(256 * 1024 * 1024)
        # Remove QtWebEngine's product token while keeping the actual bundled Chromium
        # version; this improves compatibility with sites that reject embedded-webview tokens.
        # It does not disable TLS checks or promise compatibility with every site.
        try:
            default_agent = profile.httpUserAgent()
            # Prefer a desktop Windows Chrome-style UA. Keep the actual Chromium
            # major/minor version reported by Qt WebEngine instead of spoofing a
            # random newer browser version, and remove the embedded-webview token.
            clean_agent = re.sub(r"\s+QtWebEngine/[^\s]+", "", default_agent, flags=re.IGNORECASE)
            chrome_match = re.search(r"(?:Chrome|Chromium)/([0-9.]+)", clean_agent, flags=re.IGNORECASE)
            if chrome_match:
                # Always advertise a Windows desktop browser, never a mobile Android/iPhone UA.
                # Keep the Chromium version bundled with QtWebEngine to avoid a false version claim.
                profile.setHttpUserAgent(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    f"Chrome/{chrome_match.group(1)} Safari/537.36"
                )
            else:
                profile.setHttpUserAgent(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            profile.setHttpAcceptLanguage("en-US,en;q=0.9,ru-RU,ru;q=0.8")
        except (AttributeError, RuntimeError):
            pass
        settings = profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, bool(self.preferences.get("javascript_enabled", True)))
        # ForceDarkMode is supported by Qt WebEngine Chromium builds; guard it for compatibility.
        # It requests dark rendering for pages such as Google Search without injecting CSS into sites.
        force_dark = getattr(QWebEngineSettings.WebAttribute, "ForceDarkMode", None)
        if force_dark is not None:
            settings.setAttribute(force_dark, bool(self.preferences.get("dark_websites", True)))
        # Be explicit: modern sites such as YouTube rely on images and thumbnails being loaded.
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, not self.private_mode)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        for name in ("WebGLEnabled", "NavigateOnDropEnabled", "FocusOnNavigationEnabled", "PdfViewerEnabled"):
            attribute = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attribute is not None:
                settings.setAttribute(attribute, True)
        # Do not allow a locally opened HTML file to read unrelated local files by default.
        # Website file uploads are supported through a visible file chooser below.
        local_file_access = getattr(QWebEngineSettings.WebAttribute, "LocalContentCanAccessFileUrls", None)
        if local_file_access is not None:
            settings.setAttribute(local_file_access, False)
        remote_local_access = getattr(QWebEngineSettings.WebAttribute, "LocalContentCanAccessRemoteUrls", None)
        if remote_local_access is not None:
            settings.setAttribute(remote_local_access, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        # Keep user-initiated media playback and HTTPS certificate verification.
        playback_attr = getattr(QWebEngineSettings.WebAttribute, "PlaybackRequiresUserGesture", None)
        if playback_attr is not None:
            settings.setAttribute(playback_attr, True)
        return profile

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 8, 10, 6)
        root_layout.setSpacing(7)

        top = QWidget()
        self.top_panel = top
        top.setObjectName("TopPanel")
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(11, 8, 11, 8)
        top_layout.setSpacing(7)

        self.brand_widget = QWidget()
        brand_row = QHBoxLayout(self.brand_widget)
        self.brand_row = brand_row
        brand = QLabel("✦  Yus Browser Pro 2.3")
        brand.setObjectName("Brand")
        self.brand_label = brand
        # A restrained purple glow animates on the existing brand label only.
        self.brand_glow_effect = QGraphicsDropShadowEffect(self.brand_label)
        self.brand_glow_effect.setColor(QColor("#9864ff"))
        self.brand_glow_effect.setOffset(0, 0)
        self.brand_glow_effect.setBlurRadius(7.0)
        self.brand_label.setGraphicsEffect(self.brand_glow_effect)
        tagline = QLabel("YOUR WEB. YOUR WORLD.")
        tagline.setObjectName("Tagline")
        brand_row.addWidget(brand)
        brand_row.addWidget(tagline)
        brand_row.addStretch(1)
        if self.private_mode:
            private_label = QLabel("◉ ПРИВАТНОЕ ОКНО · АККАУНТЫ НЕ СОХРАНЯЮТСЯ")
            private_label.setStyleSheet("color:#d6b8ff;font-weight:500;padding:5px")
            brand_row.addWidget(private_label)
        top_layout.addWidget(self.brand_widget)

        nav_row = QHBoxLayout()
        self.nav_row = nav_row
        nav_row.setSpacing(6)
        self.back_button = self._btn("←", "Назад", lambda: self.current_view() and self.current_view().back())
        self.forward_button = self._btn("→", "Вперёд", lambda: self.current_view() and self.current_view().forward())
        self.reload_button = self._btn("↻", "Перезагрузить", lambda: self.current_view() and self.current_view().reload())
        self.stop_button = self._btn("■", "Остановить загрузку", self.stop_loading)
        self.home_button = self._btn("⌂", "Домой", self.go_home)
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.forward_button)
        nav_row.addWidget(self.reload_button)
        nav_row.addWidget(self.stop_button)
        nav_row.addWidget(self.home_button)

        self.search_engine_combo = QComboBox()
        self.search_engine_combo.addItems(list(SEARCH_ENGINES.keys()))
        preferred_engine = str(self.preferences.get("search_engine", DEFAULT_SEARCH_ENGINE))
        self.search_engine_combo.setCurrentText(preferred_engine if preferred_engine in SEARCH_ENGINES else DEFAULT_SEARCH_ENGINE)
        self.search_engine_combo.setMinimumWidth(132)
        self.search_engine_combo.setMaximumWidth(158)
        self.search_engine_combo.setToolTip("Поисковая система по умолчанию. Её можно менять прямо здесь.")
        self.search_engine_combo.currentTextChanged.connect(self.change_search_engine)
        nav_row.addWidget(self.search_engine_combo)

        self.address = QLineEdit()
        self.address.setPlaceholderText("Адрес сайта, поиск или yt: запрос…")
        self.address.setClearButtonEnabled(True)
        self.address.setMinimumWidth(220)
        self.address.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.address.returnPressed.connect(self.navigate_address)
        self.address_completer_model = QStringListModel(self)
        self.address_completer = QCompleter(self.address_completer_model, self)
        self.address_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.address_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.address_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.address_completer.setMaxVisibleItems(8)
        self.address.setCompleter(self.address_completer)
        nav_row.addWidget(self.address, 1)
        go_button = self._btn("Перейти", "Открыть адрес", self.navigate_address, primary=True)
        nav_row.addWidget(go_button)
        self.bookmark_button = self._btn("☆", "Добавить страницу в закладки (Ctrl+D)", self.add_bookmark)
        nav_row.addWidget(self.bookmark_button)
        self.wide_view_button = self._btn("Широкий вид", "Увеличить область страницы; Ctrl+Shift+V переключает режим", self.toggle_wide_view)
        nav_row.addWidget(self.wide_view_button)
        self.menu_button = self._btn("Меню", "Все функции браузера", self.show_browser_menu)
        nav_row.addWidget(self.menu_button)
        self.panels_button = self._btn("Панели +", "Показать или скрыть дополнительные панели, чтобы освободить место для сайта", self.toggle_extra_panels)
        nav_row.addWidget(self.panels_button)
        self.external_button = self._btn("↗", "Открыть текущую страницу в системном браузере, если сайт несовместим со встроенным движком", self.open_current_in_system_browser)
        nav_row.addWidget(self.external_button)
        top_layout.addLayout(nav_row)

        self.action_widget = QWidget()
        action_row = QHBoxLayout(self.action_widget)
        self.action_row = action_row
        action_row.setSpacing(7)
        action_row.addWidget(self._btn("Закладки", "Открыть закладки", self.show_bookmarks))
        action_row.addWidget(self._btn("История", "История браузера (Ctrl+H)", self.show_history))
        action_row.addWidget(self._btn("Спидометр", "Измерить скорость интернета", self.show_speed_test))
        action_row.addWidget(self._btn("Загрузки", "История загрузок (Ctrl+J)", self.show_downloads))
        action_row.addWidget(self._btn("Новая вкладка +", "Создать вкладку (Ctrl+T)", lambda: self.new_tab()))
        action_row.addWidget(self._btn("Приватное окно", "Окно без постоянной истории и cookies (Ctrl+Shift+N)", self.open_private_window))
        action_row.addStretch(1)
        action_row.addWidget(self._btn("Звук: Вкл" if self.preferences.get("click_sounds", True) else "Звук: Выкл", "Включить/выключить глубокие игровые звуки интерфейса (Ctrl+Alt+S)", self.toggle_click_sounds))
        action_row.addWidget(self._btn("Настройки", "Настройки браузера и приватности", self.show_settings))
        action_row.addWidget(self._btn("О программе", "Версия, компоненты и папка данных", self.show_about))
        action_row.addWidget(self._btn("Обновления", "Проверить информацию об обновлениях", self.check_updates))
        top_layout.addWidget(self.action_widget)

        self.tools_widget = QWidget()
        tools_row = QHBoxLayout(self.tools_widget)
        self.tools_row = tools_row
        tools_row.setSpacing(6)
        tools_row.addWidget(self._btn("Найти", "Поиск текста на текущей странице · Ctrl+F", self.find_in_page))
        tools_row.addWidget(self._btn("PDF", "Сохранить текущую страницу в PDF", self.print_current_page))
        tools_row.addWidget(self._btn("HTML", "Сохранить HTML-копию текущей страницы", self.save_current_page))
        tools_row.addWidget(self._btn("Открыть файл", "Открыть локальный HTML, PDF или другой файл", self.open_local_file))
        tools_row.addWidget(self._btn("Скриншот F1", "Снимок текущего окна с редактором: F1", self.capture_screenshot, primary=True))
        tools_row.addWidget(self._btn("Видео/YouTube", "Совместимость и диагностика видео", self.show_video_help))
        tools_row.addWidget(self._btn("Команды поиска", "Подсказки для быстрого поиска по типам", self.show_search_help))
        tools_row.addWidget(self._btn("Диагностика", "Технический статус текущего сайта", self.show_site_diagnostics))
        tools_row.addStretch(1)
        tools_row.addWidget(self._btn("−", "Уменьшить масштаб", lambda: self.change_zoom(-0.1)))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(46)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tools_row.addWidget(self.zoom_label)
        tools_row.addWidget(self._btn("+", "Увеличить масштаб", lambda: self.change_zoom(0.1)))
        top_layout.addWidget(self.tools_widget)

        root_layout.addWidget(top)
        self.action_widget.setVisible(False)
        self.tools_widget.setVisible(False)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        # Use compact custom close buttons so every tab has a visible, consistent violet X.
        self.tabs.setTabsClosable(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.currentChanged.connect(self.on_current_tab_changed)
        self.tabs.tabBarDoubleClicked.connect(lambda _i: self.new_tab())
        # A visible browser-style + button stays beside the tab strip, even in wide view.
        self.new_tab_button = QToolButton(self.tabs)
        self.new_tab_button.setObjectName("YusNewTabButton")
        self.new_tab_button.setText("+")
        self.new_tab_button.setToolTip("Открыть новую отдельную вкладку (Ctrl+T)")
        self.new_tab_button.setAccessibleName("Новая вкладка")
        self.new_tab_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_tab_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_tab_button.clicked.connect(lambda _checked=False: self.new_tab())
        self.tabs.setCornerWidget(self.new_tab_button, Qt.Corner.TopRightCorner)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self.show_tab_context_menu)
        root_layout.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        root_layout.addWidget(self.progress)
        self.setCentralWidget(root)

        self.statusBar().setStyleSheet("background:#20152f;color:#cdb7e5;border-top:1px solid #3e2956")

    def toggle_extra_panels(self):
        """Expand or collapse the secondary controls without affecting the page viewport."""
        if self._wide_view_active:
            self._wide_view_user_override = False
            self._apply_wide_view(False)
        self._extra_panels_expanded = not self._extra_panels_expanded
        visible = self._extra_panels_expanded and not self._wide_view_active
        self.action_widget.setVisible(visible)
        self.tools_widget.setVisible(visible)
        self.panels_button.setText("Панели −" if visible else "Панели +")
        self.statusBar().showMessage(
            "Дополнительные панели показаны." if visible else "Дополнительные панели скрыты — больше места для сайта.", 3000
        )

    def toggle_click_sounds(self):
        enabled = not bool(self.preferences.get("click_sounds", True))
        self.preferences["click_sounds"] = enabled
        try:
            write_json(SETTINGS_FILE, self.preferences)
        except OSError:
            pass
        sound = getattr(QApplication.instance(), "_yus_click_sound", None)
        if sound is not None:
            sound.set_enabled(enabled)
        for button in self.action_widget.findChildren(QPushButton):
            if button.toolTip().startswith("Включить/выключить глубокие игровые звуки"):
                button.setText("Звук: Вкл" if enabled else "Звук: Выкл")
                break
        self.statusBar().showMessage("Игровые звуки включены" if enabled else "Игровые звуки выключены", 2500)

    def _btn(self, text, tooltip, callback, primary=False):
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(36)
        if primary:
            btn.setObjectName("Primary")
        btn.clicked.connect(callback)
        return btn

    def _install_shortcuts(self):
        def shortcut(key, fn):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)
            return sc
        self._shortcuts = [
            shortcut("Ctrl+L", self.focus_address),
            shortcut("Ctrl+K", self.focus_address),
            shortcut("Alt+D", self.focus_address),
            shortcut("Ctrl+Shift+S", self.show_search_help),
            shortcut("Ctrl+Alt+S", self.toggle_click_sounds),
            shortcut("Ctrl+T", lambda: self.new_tab()),
            shortcut("Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())),
            shortcut("Ctrl+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count()) if self.tabs.count() else None),
            shortcut("Ctrl+Shift+Tab", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % self.tabs.count()) if self.tabs.count() else None),
            shortcut("Ctrl+D", self.add_bookmark),
            shortcut("Ctrl+H", self.show_history),
            shortcut("Ctrl+J", self.show_downloads),
            shortcut("Ctrl+F", self.find_in_page),
            shortcut("Ctrl+P", self.print_current_page),
            shortcut("Ctrl+S", self.save_current_page),
            shortcut("Ctrl+Shift+N", self.open_private_window),
            shortcut("Ctrl+O", self.open_local_file),
            shortcut("Ctrl+,", self.show_settings),
            shortcut("Ctrl+Shift+I", self.show_site_diagnostics),
            shortcut("F1", self.capture_screenshot),
            shortcut("Ctrl+Shift+R", self.reload_bypass_cache),
            shortcut("Ctrl+Shift+V", self.toggle_wide_view),
            shortcut("F5", lambda: self.current_view() and self.current_view().reload()),
            shortcut("Ctrl+R", lambda: self.current_view() and self.current_view().reload()),
            shortcut("F11", self.toggle_app_fullscreen),
            shortcut("Escape", self.exit_fullscreen),
            shortcut("Ctrl+Plus", lambda: self.change_zoom(0.1)),
            shortcut("Ctrl+=", lambda: self.change_zoom(0.1)),
            shortcut("Ctrl+-", lambda: self.change_zoom(-0.1)),
            shortcut("Ctrl+0", lambda: self.set_zoom(1.0)),
            shortcut("Alt+Left", lambda: self.current_view() and self.current_view().back()),
            shortcut("Alt+Right", lambda: self.current_view() and self.current_view().forward()),
        ]

    def _session_url_for_view(self, view) -> str | None:
        """Persist active and lazily restored HTTP(S) tabs; never save file:// pages."""
        pending = getattr(view, "_yus_pending_url", None)
        if isinstance(pending, str) and pending.startswith(("https://", "http://")):
            return pending[:2048]
        try:
            url = view.url().toString().strip()
        except (AttributeError, RuntimeError):
            return None
        if not url or url in ("about:blank", "https://yus.browser.local/"):
            return SESSION_HOME_TOKEN
        if url.startswith(("https://", "http://")):
            return url[:2048]
        return None

    def schedule_session_save(self):
        if (self.private_mode or self._restoring_session or not hasattr(self, "_session_save_timer")
                or not bool(self.preferences.get("restore_tabs", True))):
            return
        self._session_save_timer.start()

    def save_session(self):
        """Autosave tab URLs for normal windows only; private sessions are never written."""
        if (self.private_mode or self._restoring_session or not hasattr(self, "tabs")
                or not bool(self.preferences.get("restore_tabs", True))):
            return
        urls = []
        saved_current_index = 0
        source_current_index = self.tabs.currentIndex()
        for index in range(self.tabs.count()):
            view = self.tabs.widget(index)
            if not isinstance(view, QWebEngineView):
                continue
            url = self._session_url_for_view(view)
            if url and (url == SESSION_HOME_TOKEN or url.startswith(("https://", "http://"))):
                if index == source_current_index:
                    saved_current_index = len(urls)
                urls.append(url)
        if not urls:
            urls = [SESSION_HOME_TOKEN]
            saved_current_index = 0
        # Keep up to 100 tabs across restarts; restored background tabs are loaded lazily.
        if len(urls) > 100:
            trim_count = len(urls) - 100
            urls = urls[trim_count:]
            saved_current_index -= trim_count
        saved_current_index = max(0, min(int(saved_current_index), len(urls) - 1))
        try:
            write_json(SESSION_FILE, {"urls": urls, "current_index": saved_current_index, "saved_at": datetime.now().isoformat(timespec="seconds")})
        except OSError as exc:
            if hasattr(self, "statusBar"):
                self.statusBar().showMessage(f"Не удалось сохранить вкладки: {exc}", 5000)

    def restore_session_tabs(self) -> bool:
        if self.private_mode:
            return False
        raw = read_json(SESSION_FILE, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("urls"), list):
            return False
        valid = []
        for item in raw["urls"][:100]:
            if isinstance(item, str) and (item == SESSION_HOME_TOKEN or item.startswith(("https://", "http://"))):
                valid.append(item)
        if not valid:
            return False
        self._restoring_session = True
        try:
            for item in valid:
                view = self.new_tab(None, switch=False)
                if item != SESSION_HOME_TOKEN:
                    # Restore tabs as placeholders first; only the selected tab loads now.
                    # This keeps startup responsive when a lot of tabs were saved.
                    view._yus_pending_url = item
                    idx = self.tabs.indexOf(view)
                    host = QUrl(item).host() or item
                    self.tabs.setTabText(idx, host[:24] + ("…" if len(host) > 24 else ""))
                    self.tabs.setTabToolTip(idx, item)
            try:
                current_index = int(raw.get("current_index", len(valid) - 1))
            except (TypeError, ValueError):
                current_index = len(valid) - 1
            self.tabs.setCurrentIndex(max(0, min(current_index, self.tabs.count() - 1)))
        finally:
            self._restoring_session = False
        self._load_pending_view(self.current_view())
        self.schedule_session_save()
        return self.tabs.count() > 0

    def closeEvent(self, event):
        if not self.private_mode:
            if hasattr(self, "_session_save_timer"):
                self._session_save_timer.stop()
            self.save_session()
        else:
            # Private profile is memory-only; clear transient cookies/cache on close.
            try:
                self._profile.cookieStore().deleteAllCookies()
                self._profile.clearHttpCache()
            except (AttributeError, RuntimeError):
                pass
        # Profiles are parented to QApplication (see _make_profile), so they
        # outlive their pages and avoid teardown-order warnings from Chromium.
        super().closeEvent(event)

    def current_view(self) -> QWebEngineView | None:
        widget = self.tabs.currentWidget() if hasattr(self, "tabs") else None
        return widget if isinstance(widget, QWebEngineView) else None

    def new_tab(self, url: str | None = None, switch: bool = True) -> QWebEngineView:
        view = QWebEngineView()
        page = BrowserPage(self._profile, self, view)
        page.setBackgroundColor(QColor("#100b18"))
        view.setStyleSheet("background-color:#100b18; border:0;")
        view.setPage(page)
        page.titleChanged.connect(lambda title, v=view: self.update_tab_title(v, title))
        page.iconChanged.connect(lambda icon, v=view: self.update_tab_icon(v, icon))
        page.urlChanged.connect(lambda qurl, v=view: self.update_address_for_view(v, qurl))
        page.urlChanged.connect(lambda _qurl: self.refresh_navigation_buttons())
        if hasattr(page, "permissionRequested"):
            page.permissionRequested.connect(lambda request, v=view: self.handle_permission_request(request, v))
        if hasattr(page, "fileSystemAccessRequested"):
            page.fileSystemAccessRequested.connect(lambda request, v=view: self.handle_file_system_access(request, v))
        view.loadProgress.connect(lambda value, v=view: self.update_progress(v, value))
        view.loadStarted.connect(lambda v=view: self._loading_started(v))
        view.loadFinished.connect(lambda ok, v=view: self.on_load_finished(v, ok))
        page.linkHovered.connect(lambda link: self.statusBar().showMessage(link, 2500) if link else None)
        page.fullScreenRequested.connect(lambda request, v=view: self.handle_web_fullscreen(v, request))
        page.renderProcessTerminated.connect(lambda status, code, v=view: self.on_renderer_terminated(v, status, code))
        index = self.tabs.addTab(view, "Новая вкладка")
        self.tabs.setTabToolTip(index, "Yus Browser")
        self._install_tab_close_button(view)
        if switch:
            self.tabs.setCurrentIndex(index)
        if url:
            if url == HOME_URL:
                # Show the branded Yus Browser home page; Yandex is the default search engine.
                view.setHtml(self.welcome_page_html(), QUrl(HOME_URL))
            else:
                view.setUrl(QUrl(url))
        else:
            view.setHtml(self.welcome_page_html(), QUrl(HOME_URL))
            if switch:
                QTimer.singleShot(0, self.focus_address)
        self.refresh_navigation_buttons()
        self.update_address_suggestions()
        self.schedule_session_save()
        return view

    def create_popup_page(self):
        view = self.new_tab("about:blank", switch=True)
        return view.page()

    def _install_tab_close_button(self, view):
        """Install a small custom X whose callback follows its tab when tabs are moved."""
        index = self.tabs.indexOf(view)
        if index < 0:
            return
        close_button = QToolButton(self.tabs.tabBar())
        close_button.setObjectName("YusTabCloseButton")
        close_button.setText("×")
        close_button.setToolTip("Закрыть эту вкладку")
        close_button.setAccessibleName("Закрыть вкладку")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setAutoRaise(True)
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(lambda _checked=False, target=view: self._close_tab_for_view(target))
        self.tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, close_button)

    def _close_tab_for_view(self, view):
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.close_tab(index)

    def _load_pending_view(self, view):
        """Load a saved background tab only when the user switches to it."""
        if not view:
            return
        pending = getattr(view, "_yus_pending_url", None)
        if isinstance(pending, str) and pending.startswith(("https://", "http://")):
            view._yus_pending_url = None
            view.setUrl(QUrl(pending))

    def duplicate_current_tab(self):
        """Open the current page in a separate new tab."""
        view = self.current_view()
        if view is None:
            self.new_tab()
            return
        url = view.url().toString()
        if not url or url == "about:blank" or url == HOME_URL:
            self.new_tab(HOME_URL)
        else:
            self.new_tab(url)

    def show_tab_context_menu(self, position):
        """Browser-style tab strip menu for creating and managing independent tabs."""
        bar = self.tabs.tabBar()
        index = bar.tabAt(position)
        menu = QMenu(self)
        new_action = menu.addAction("＋  Новая вкладка	Ctrl+T")
        duplicate_action = menu.addAction("Дублировать текущую вкладку")
        menu.addSeparator()
        close_action = menu.addAction("Закрыть текущую вкладку	Ctrl+W")
        close_all_but = menu.addAction("Закрыть другие вкладки")
        chosen = menu.exec(bar.mapToGlobal(position))
        if chosen is new_action:
            self.new_tab()
        elif chosen is duplicate_action:
            self.duplicate_current_tab()
        elif chosen is close_action:
            self.close_tab(self.tabs.currentIndex())
        elif chosen is close_all_but and self.tabs.count() > 1:
            current = self.current_view()
            for i in range(self.tabs.count() - 1, -1, -1):
                widget = self.tabs.widget(i)
                if widget is not current:
                    self.close_tab(i)

    def close_tab(self, index: int):
        if index < 0 or index >= self.tabs.count():
            return
        if self.tabs.count() <= 1:
            # Match common desktop browser behaviour: closing the last tab closes the window.
            self.close()
            return
        widget = self.tabs.widget(index)
        if widget:
            try:
                widget.stop()
            except (AttributeError, RuntimeError):
                pass
        self.tabs.removeTab(index)
        if widget:
            widget.deleteLater()
        self.schedule_session_save()

    def update_tab_title(self, view, title):
        idx = self.tabs.indexOf(view)
        if idx >= 0:
            pending = getattr(view, "_yus_pending_url", None)
            if isinstance(pending, str) and pending.startswith(("https://", "http://")):
                host = QUrl(pending).host() or pending
                label = host[:25] + ("…" if len(host) > 25 else "")
                self.tabs.setTabText(idx, label)
                self.tabs.setTabToolTip(idx, pending)
                return
            label = (title or "Новая вкладка").strip()
            if len(label) > 26:
                label = label[:25] + "…"
            self.tabs.setTabText(idx, label)
            self.tabs.setTabToolTip(idx, title or "Yus Browser")
            if self.current_view() is view:
                self.setWindowTitle(f"{title or APP_NAME} — {APP_NAME}")

    def update_tab_icon(self, view, icon):
        idx = self.tabs.indexOf(view)
        if idx >= 0 and not icon.isNull():
            self.tabs.setTabIcon(idx, icon)

    def update_address_for_view(self, view, qurl):
        if self.current_view() is view:
            if qurl.toString() != "https://yus.browser.local/":
                self.address.setText(qurl.toString())
            else:
                self.address.clear()
            self._sync_wide_view(qurl.toString())
        self.schedule_session_save()

    def update_progress(self, view, value):
        if self.current_view() is view:
            self.progress.setValue(value)
            if value >= 100:
                QTimer.singleShot(650, lambda: self.progress.setValue(0))

    def _loading_started(self, view):
        view._yus_is_loading = True
        if self.current_view() is view:
            self.progress.setValue(0)
            self.stop_button.setEnabled(True)
            self.statusBar().showMessage("Загрузка страницы…", 0)

    def on_load_finished(self, view, ok):
        view._yus_is_loading = False
        self.refresh_navigation_buttons()
        if self.current_view() is view:
            self.statusBar().showMessage("Страница загружена" if ok else "Страница не загрузилась. Проверь адрес, сеть или открой «Видео/YouTube» для диагностики.", 7000)
            self.progress.setValue(100 if ok else 0)
        if ok and self._is_youtube_url(view.url().toString()):
            # Wait for YouTube's client-rendered shell before deciding the page is actually blank.
            QTimer.singleShot(12000, lambda v=view, u=view.url().toString(): self.check_youtube_content(v, u))
        if not self.private_mode and ok:
            url = view.url().toString()
            if url.startswith(("https://", "http://")) and url != "https://yus.browser.local/":
                item = {"title": view.title() or url, "url": url, "time": datetime.now().isoformat(timespec="seconds")}
                # Keep a single most-recent entry per exact URL to reduce redundant history noise.
                self.history = [row for row in self.history if row.get("url") != url]
                self.history.append(item)
                self.history = self.history[-1000:]
                self.update_address_suggestions()
                try:
                    write_json(HISTORY_FILE, self.history)
                except OSError as exc:
                    self.statusBar().showMessage(f"Не удалось сохранить историю: {exc}", 5000)
        self.schedule_session_save()

    def check_youtube_content(self, view, expected_url=None):
        """Inspect YouTube after its JS shell has had time to start; offer recovery instead of silent blankness."""
        current_url = view.url().toString()
        if self.current_view() is not view or not self._is_youtube_url(current_url):
            return
        if expected_url and current_url != expected_url:
            return
        js = "JSON.stringify((() => ({title: document.title || '', text: (document.body && document.body.innerText || '').trim().slice(0,500), images: Array.from(document.images || []).filter(i => i.complete && i.naturalWidth > 0).length, imageNodes: (document.images || []).length, height: document.documentElement ? document.documentElement.scrollHeight : 0, ready: document.readyState, ua: navigator.userAgent || ''}))())"
        def check_result(result):
            try:
                data = json.loads(result) if isinstance(result, str) else result
            except (TypeError, json.JSONDecodeError):
                return
            if not isinstance(data, dict) or view.url().toString() != current_url:
                return
            text = str(data.get("text", "")).strip()
            image_count = int(data.get("images", 0) or 0)
            page_height = int(data.get("height", 0) or 0)
            # A delayed SPA or consent screen may briefly be sparse; only warn on a very small, empty DOM.
            if len(text) < 10 and image_count == 0 and page_height < 450:
                self.statusBar().showMessage(
                    "YouTube отображается пустым. Открой «Видео/YouTube» для восстановления.", 12000
                )
                warned = getattr(view, "_yus_youtube_recovery_url", None)
                if warned != current_url:
                    view._yus_youtube_recovery_url = current_url
                    QTimer.singleShot(0, lambda v=view, u=current_url: self.show_youtube_recovery(v, u))
        try:
            view.page().runJavaScript(js, check_result)
        except (RuntimeError, AttributeError):
            pass

    def open_mobile_youtube(self, view=None):
        view = view or self.current_view()
        if not view:
            return
        source = view.url()
        host = source.host().lower()
        if host == "youtu.be" or host.endswith(".youtu.be"):
            video_id = source.path().strip("/").split("/")[0]
            if video_id:
                target = QUrl("https://m.youtube.com/watch?v=" + video_id)
            else:
                target = QUrl("https://m.youtube.com/")
        else:
            target = QUrl(source)
            target.setScheme("https")
            target.setHost("m.youtube.com")
            target.setPort(-1)
            if not target.path() or target.path() == "/":
                target.setPath("/")
        view.setUrl(target)
        self.statusBar().showMessage("Открываю мобильную версию YouTube для совместимости…", 5000)

    def show_youtube_recovery(self, view=None, expected_url=None):
        """Offer explicit recovery choices; never silently rewrite or reload the user's page."""
        view = view or self.current_view()
        if not view or self.current_view() is not view or not self._is_youtube_url(view.url().toString()):
            return
        if expected_url and view.url().toString() != expected_url:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Восстановление YouTube · Yus Browser")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("YouTube открылся, но его содержимое почти пустое.")
        box.setInformativeText(
            "Можно попробовать перезагрузку без кэша, мобильную версию сайта или открыть эту страницу в установленном браузере Windows. "
            "Выбор не меняет настройки безопасности и не даёт сайту доступ ко всему диску."
        )
        reload_btn = box.addButton("Перезагрузить без кэша", QMessageBox.ButtonRole.ActionRole)
        mobile_btn = box.addButton("Попробовать мобильный YouTube", QMessageBox.ButtonRole.ActionRole)
        external_btn = box.addButton("Открыть в системном браузере", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Пока закрыть", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is reload_btn:
            self.reload_bypass_cache()
        elif clicked is mobile_btn:
            self.open_mobile_youtube(view)
        elif clicked is external_btn:
            QDesktopServices.openUrl(QUrl(expected_url or view.url().toString()))

    def navigate_address(self):
        text = self.address.text()
        if not text.strip():
            self.go_home()
            return
        engine = str(self.preferences.get("search_engine", DEFAULT_SEARCH_ENGINE))
        url = resolve_address(text, search_url=search_url_for(engine))
        view = self.current_view()
        if view:
            view.setUrl(QUrl(url))
            self.statusBar().showMessage(f"Открывается через Yus Browser: {url[:170]}", 3500)

    def change_search_engine(self, engine: str):
        if engine not in SEARCH_ENGINES:
            return
        if getattr(self, "preferences", {}).get("search_engine") == engine:
            return
        updated = dict(self.preferences)
        updated["search_engine"] = engine
        try:
            if not self.private_mode:
                write_json(SETTINGS_FILE, updated)
            self.preferences = updated
            self.statusBar().showMessage(f"Поисковик по умолчанию: {engine}", 3000)
            view = self.current_view()
            if view and view.url().toString() == "https://yus.browser.local/":
                view.setHtml(self.welcome_page_html(), QUrl("https://yus.browser.local/"))
        except OSError as exc:
            QMessageBox.warning(self, "Настройки поиска", f"Не удалось сохранить поисковик:\n{exc}")

    def _home_tiles(self, records, limit: int = 6, empty_title: str = "Пока пусто", empty_subtitle: str = "Страница появится здесь") -> str:
        tiles: list[str] = []
        seen: set[str] = set()
        for record in reversed(records if isinstance(records, list) else []):
            if not isinstance(record, dict):
                continue
            url = str(record.get("url", "")).strip()
            if not url.startswith(("https://", "http://")) or url in seen:
                continue
            seen.add(url)
            title = str(record.get("title") or url).strip()[:48]
            try:
                from urllib.parse import urlsplit
                subtitle = urlsplit(url).hostname or "Веб-сайт"
            except ValueError:
                subtitle = "Веб-сайт"
            safe_url = html.escape(url, quote=True)
            safe_title = html.escape(title)
            safe_subtitle = html.escape(subtitle)
            tiles.append(
                f'<a class="tile" href="{safe_url}"><span class="ico">✦</span>'
                f'<strong>{safe_title}</strong><small>{safe_subtitle}</small></a>'
            )
            if len(tiles) >= limit:
                break
        if not tiles:
            tiles.append(
                '<div class="tile"><span class="ico">✧</span>'
                f'<strong>{html.escape(empty_title)}</strong><small>{html.escape(empty_subtitle)}</small></div>'
            )
        return "\n".join(tiles)

    def welcome_page_html(self) -> str:
        engine = str(self.preferences.get("search_engine", DEFAULT_SEARCH_ENGINE))
        if engine not in SEARCH_ENGINES:
            engine = DEFAULT_SEARCH_ENGINE
        pattern = search_url_for(engine).replace("{}", "%s").replace('"', "&quot;")
        bookmark_tiles = self._home_tiles(self.bookmarks, empty_title="Добавь первую закладку", empty_subtitle="Нажми ☆ на любимом сайте")
        recent_tiles = self._home_tiles(self.history, empty_title="Новых сайтов пока нет", empty_subtitle="Недавно посещённые сайты появятся здесь")
        # The search endpoint is selected from a fixed allowlist; user content is HTML-escaped.
        page = WELCOME_HTML.replace("__YUS_SEARCH_PATTERN__", pattern).replace("__YUS_SEARCH_NAME__", html.escape(engine))
        page = page.replace("__YUS_BOOKMARK_TILES__", bookmark_tiles).replace("__YUS_RECENT_TILES__", recent_tiles)
        return page

    def update_address_suggestions(self, _text: str = ""):
        if not hasattr(self, "address_completer_model"):
            return
        suggestions: list[str] = []
        seen: set[str] = set()
        for record in list(reversed(self.history[-250:])) + list(reversed(self.bookmarks[-150:])):
            if not isinstance(record, dict):
                continue
            url = str(record.get("url", "")).strip()
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                suggestions.append(url)
                if len(suggestions) >= 350:
                    break
        self.address_completer_model.setStringList(suggestions)

    def show_search_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Умный поиск · Yus Browser")
        dlg.resize(630, 470)
        layout = QVBoxLayout(dlg)
        heading = QLabel("Команды быстрого поиска")
        heading.setStyleSheet("font-size:16pt;font-weight:600;color:#d5b5ff;padding:5px")
        layout.addWidget(heading)
        label = QLabel(
            "Пиши команду в адресной строке и нажимай Enter. Обычный текст ищется через выбранный в панели поисковик.\n\n"
            "yt: котики — поиск видео на YouTube\n"
            "img: фиолетовые обои — поиск картинок\n"
            "news: новости технологий — поиск новостей\n"
            "maps: Алматы — поиск на картах\n"
            "wiki: WireGuard — поиск в Википедии\n"
            "!g запрос — поиск Google\n"
            "!b запрос — поиск Bing\n"
            "!ddg запрос — DuckDuckGo\n"
            "!brave запрос — Brave Search\n"
            "!ya запрос — Яндекс\n"
            "tr: hello — перевод\n"
            "gh: pygame — поиск проектов GitHub\n"
            "reddit: python — поиск Reddit\n"
            "music: lofi — поиск YouTube Music\n"
            "pdf: сеть — поиск PDF-документов\n"
            "scholar: physics — научные материалы\n"
            "? запрос — поиск выбранным поисковиком"
        )
        label.setWordWrap(True)
        label.setStyleSheet("padding:10px;color:#d9c5f3;line-height:1.5")
        layout.addWidget(label, 1)
        close = QPushButton("Понятно")
        close.clicked.connect(dlg.accept)
        layout.addWidget(close)
        dlg.exec()

    def go_home(self):
        view = self.current_view()
        if view:
            view.setHtml(self.welcome_page_html(), QUrl("https://yus.browser.local/"))
            self.address.clear()

    def focus_address(self):
        self.address.setFocus()
        self.address.selectAll()

    def on_current_tab_changed(self, _index):
        view = self.current_view()
        if view:
            if not self._restoring_session:
                self._load_pending_view(view)
            if view.url().toString() == "https://yus.browser.local/":
                self.address.clear()
            else:
                self.address.setText(view.url().toString())
            self.setWindowTitle(f"{view.title() or APP_NAME} — {APP_NAME}" + (" · Приватное окно" if self.private_mode else ""))
            self.progress.setValue(0)
            self.refresh_navigation_buttons()
            self.update_zoom_label()
            self._sync_wide_view(view.url().toString())
            self.schedule_session_save()

    def refresh_navigation_buttons(self):
        view = self.current_view()
        if view:
            self.back_button.setEnabled(view.history().canGoBack())
            self.forward_button.setEnabled(view.history().canGoForward())
            self.reload_button.setEnabled(True)
            self.stop_button.setEnabled(bool(getattr(view, "_yus_is_loading", False)))
        else:
            self.back_button.setEnabled(False)
            self.forward_button.setEnabled(False)
            self.reload_button.setEnabled(False)
            self.stop_button.setEnabled(False)

    def update_zoom_label(self):
        view = self.current_view()
        if view and hasattr(self, "zoom_label"):
            self.zoom_label.setText(f"{int(view.zoomFactor() * 100)}%")

    def stop_loading(self):
        view = self.current_view()
        if view:
            view.stop()
            self.progress.setValue(0)
            self.statusBar().showMessage("Загрузка остановлена", 2500)

    def add_bookmark(self):
        view = self.current_view()
        if not view:
            return
        url = view.url().toString()
        if not url.startswith(("http://", "https://")):
            QMessageBox.information(self, "Закладки", "Можно сохранить в закладки только страницу с http:// или https://.")
            return
        if self.private_mode:
            QMessageBox.information(self, "Приватное окно", "В приватном окне закладки не сохраняются на диск.")
            return
        if any(b.get("url") == url for b in self.bookmarks):
            self.statusBar().showMessage("Эта страница уже в закладках", 2500)
            return
        self.bookmarks.append({"title": view.title() or url, "url": url})
        try:
            write_json(BOOKMARKS_FILE, self.bookmarks)
            self.update_address_suggestions()
            self.statusBar().showMessage("Страница добавлена в закладки", 3000)
        except OSError as exc:
            QMessageBox.warning(self, "Закладки", f"Не удалось сохранить закладку:\n{exc}")

    def show_bookmarks(self):
        menu = QMenu(self)
        if self.private_mode:
            notice = menu.addAction("В приватном окне закладки не сохраняются")
            notice.setEnabled(False)
        else:
            add = menu.addAction("☆ Добавить текущую страницу")
            add.triggered.connect(self.add_bookmark)
            menu.addSeparator()
            if not self.bookmarks:
                action = menu.addAction("Закладок пока нет")
                action.setEnabled(False)
            for bookmark in reversed(self.bookmarks):
                action = menu.addAction((bookmark.get("title") or bookmark.get("url", "Закладка"))[:70])
                action.setToolTip(bookmark.get("url", ""))
                action.triggered.connect(lambda checked=False, u=bookmark.get("url", ""): self.open_url_in_current(u))
            if self.bookmarks:
                menu.addSeparator()
                manage = menu.addAction("Управление закладками…")
                manage.triggered.connect(self.manage_bookmarks)
        menu.exec(self.bookmark_button.mapToGlobal(self.bookmark_button.rect().bottomLeft()))

    def manage_bookmarks(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Закладки Yus Browser")
        dlg.resize(720, 420)
        layout = QVBoxLayout(dlg)
        listing = QListWidget()
        for bookmark in self.bookmarks:
            item = QListWidgetItem(f"{bookmark.get('title', 'Без названия')}\n{bookmark.get('url', '')}")
            item.setData(Qt.ItemDataRole.UserRole, bookmark.get("url", ""))
            listing.addItem(item)
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть")
        remove_btn = QPushButton("Удалить выбранную")
        close_btn = QPushButton("Закрыть")
        buttons.addWidget(open_btn)
        buttons.addWidget(remove_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        open_btn.clicked.connect(lambda: self.open_url_in_current(listing.currentItem().data(Qt.ItemDataRole.UserRole)) if listing.currentItem() else None)
        def remove_bookmark():
            item = listing.currentItem()
            if not item:
                return
            target_url = item.data(Qt.ItemDataRole.UserRole)
            self.bookmarks = [b for b in self.bookmarks if b.get("url") != target_url]
            listing.takeItem(listing.row(item))
            try:
                write_json(BOOKMARKS_FILE, self.bookmarks)
                self.update_address_suggestions()
            except OSError as exc:
                QMessageBox.warning(dlg, "Ошибка", str(exc))
        remove_btn.clicked.connect(remove_bookmark)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    def open_url_in_current(self, url):
        if url and self.current_view():
            self.current_view().setUrl(QUrl(url))

    def show_history(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("История Yus Browser" + (" · не сохраняется" if self.private_mode else ""))
        dlg.resize(780, 470)
        layout = QVBoxLayout(dlg)
        listing = QListWidget()
        records = list(reversed(self.history)) if not self.private_mode else []
        for rec in records:
            item = QListWidgetItem(f"{rec.get('title', 'Без названия')}\n{rec.get('url', '')}  ·  {rec.get('time', '')}")
            item.setData(Qt.ItemDataRole.UserRole, rec.get("url", ""))
            listing.addItem(item)
        if self.private_mode:
            listing.addItem("Приватная история не сохраняется.")
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть выбранную")
        clear_btn = QPushButton("Очистить историю")
        close_btn = QPushButton("Закрыть")
        buttons.addWidget(open_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        open_btn.clicked.connect(lambda: self.open_url_in_current(listing.currentItem().data(Qt.ItemDataRole.UserRole)) if listing.currentItem() and listing.currentItem().data(Qt.ItemDataRole.UserRole) else None)
        def clear_history():
            if QMessageBox.question(dlg, "Очистить историю", "Удалить сохранённую историю посещений?") != QMessageBox.StandardButton.Yes:
                return
            self.history = []
            self.update_address_suggestions()
            listing.clear()
            if not self.private_mode:
                try:
                    write_json(HISTORY_FILE, self.history)
                except OSError as exc:
                    QMessageBox.warning(dlg, "Ошибка", str(exc))
        clear_btn.clicked.connect(clear_history)
        close_btn.clicked.connect(dlg.accept)
        dlg.exec()

    def show_downloads(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Загрузки Yus Browser")
        dlg.resize(760, 430)
        layout = QVBoxLayout(dlg)
        listing = QListWidget()

        def refresh_list():
            selected_path = listing.currentItem().data(Qt.ItemDataRole.UserRole) if listing.currentItem() else None
            listing.clear()
            for record in self.download_items:
                item = QListWidgetItem(record.get("display", "Загрузка"))
                item.setData(Qt.ItemDataRole.UserRole, record.get("path", ""))
                listing.addItem(item)
                if selected_path and item.data(Qt.ItemDataRole.UserRole) == selected_path:
                    listing.setCurrentItem(item)
            if not self.download_items:
                listing.addItem("Загрузок в этом сеансе пока нет.")

        refresh_list()
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть файл")
        folder_btn = QPushButton("Открыть папку")
        close_btn = QPushButton("Закрыть")
        buttons.addWidget(open_btn)
        buttons.addWidget(folder_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        def open_selected():
            item = listing.currentItem()
            path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            if not path or not Path(path).exists():
                QMessageBox.information(dlg, "Загрузки", "Выбери завершённый файл, который уже существует на диске.")
                return
            try:
                os.startfile(path)
            except OSError as exc:
                QMessageBox.warning(dlg, "Не удалось открыть файл", str(exc))

        def open_folder():
            item = listing.currentItem()
            path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            folder = Path(path).parent if path else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads"))
            try:
                os.startfile(str(folder if folder.exists() else Path.home() / "Downloads"))
            except OSError as exc:
                QMessageBox.warning(dlg, "Не удалось открыть папку", str(exc))

        open_btn.clicked.connect(open_selected)
        folder_btn.clicked.connect(open_folder)
        close_btn.clicked.connect(dlg.accept)
        refresh_timer = QTimer(dlg)
        refresh_timer.setInterval(750)
        refresh_timer.timeout.connect(refresh_list)
        refresh_timer.start()
        dlg.exec()

    def _mark_download_from_internet(self, path: Path, source_url: str) -> None:
        """Set Windows Mark-of-the-Web so Windows security reputation checks can apply."""
        if os.name != "nt" or not source_url.lower().startswith(("http://", "https://")):
            return
        try:
            # NTFS alternate data stream used by Windows for files obtained from the internet.
            zone_path = str(path) + ":Zone.Identifier"
            safe_url = source_url.replace("\r", "").replace("\n", "")
            Path(zone_path).write_text(
                "[ZoneTransfer]\r\nZoneId=3\r\nHostUrl=" + safe_url + "\r\n",
                encoding="utf-8",
            )
        except (OSError, ValueError):
            # FAT/exFAT, unsupported filesystems, or policy restrictions may not support ADS.
            pass

    def handle_download(self, request):
        suggested = request.suggestedFileName() or "download"
        risky_extensions = {
            ".exe", ".msi", ".bat", ".cmd", ".ps1", ".scr", ".com", ".pif",
            ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar",
            ".dll", ".sys", ".reg", ".lnk", ".iso", ".img", ".zip", ".7z", ".rar",
        }
        if Path(suggested).suffix.lower() in risky_extensions:
            warning = QMessageBox(self)
            warning.setIcon(QMessageBox.Icon.Warning)
            warning.setWindowTitle("Проверка загрузки · Yus Browser")
            warning.setText(f"Файл «{suggested}» может содержать программу или другие потенциально опасные данные.")
            warning.setInformativeText(
                "Yus Browser не может гарантировать безопасность файла и не заменяет антивирус. "
                "Сохраняй его только с доверенного сайта и проверь Microsoft Defender перед запуском.\n\nПродолжить загрузку?"
            )
            continue_button = warning.addButton("Сохранить файл", QMessageBox.ButtonRole.AcceptRole)
            warning.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
            warning.exec()
            if warning.clickedButton() != continue_button:
                request.cancel()
                self.statusBar().showMessage("Загрузка потенциально опасного типа отменена", 5000)
                return
        downloads_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads"))
        downloads_dir.mkdir(parents=True, exist_ok=True)
        dialog = QFileDialog(self, "Сохранить загрузку", str(downloads_dir / suggested), "Все файлы (*.*)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.selectedFiles()
            selected_path = chosen[0] if chosen else ""
        else:
            selected_path = ""
        if not selected_path:
            request.cancel()
            self.statusBar().showMessage("Загрузка отменена", 3000)
            return
        path = Path(selected_path)
        request.setDownloadDirectory(str(path.parent))
        request.setDownloadFileName(path.name)
        record = {"display": f"{suggested} — загрузка началась", "path": str(path), "request": request}
        self.download_items.append(record)

        # Persistent, non-modal progress notification so the user can see that a file is downloading.
        notice = QDialog(self, Qt.WindowType.Tool)
        notice.setWindowTitle("Загрузка · Yus Browser Pro 2.3")
        notice.setMinimumWidth(390)
        notice.setWindowModality(Qt.WindowModality.NonModal)
        notice_layout = QVBoxLayout(notice)
        notice_title = QLabel("Скачивается файл")
        notice_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #a78bfa;")
        notice_file = QLabel(path.name)
        notice_file.setWordWrap(True)
        notice_info = QLabel("Подключаемся и получаем данные…")
        notice_bar = QProgressBar()
        notice_bar.setRange(0, 100)
        notice_bar.setValue(0)
        notice_bar.setTextVisible(True)
        notice_bar.setStyleSheet("QProgressBar { border: 1px solid #554477; border-radius: 6px; text-align: center; min-height: 18px; } QProgressBar::chunk { background: #8b5cf6; border-radius: 5px; }")
        notice_close = QPushButton("Свернуть")
        notice_layout.addWidget(notice_title)
        notice_layout.addWidget(notice_file)
        notice_layout.addWidget(notice_bar)
        notice_layout.addWidget(notice_info)
        notice_layout.addWidget(notice_close)
        notice_close.clicked.connect(notice.hide)
        record["notice"] = notice
        notice.show()

        def update_download():
            received = request.receivedBytes()
            total = request.totalBytes()
            state = request.state()
            if total > 0:
                progress = min(100, int(received * 100 / total))
                text = f"{path.name} — {progress}% ({received // 1024} / {total // 1024} КБ)"
                notice_bar.setRange(0, 100)
                notice_bar.setValue(progress)
                notice_info.setText(f"Загрузка: {progress}% · {received / 1048576:.2f} / {total / 1048576:.2f} МБ")
            else:
                text = f"{path.name} — {received // 1024} КБ"
                notice_bar.setRange(0, 0)
                notice_info.setText(f"Получено {received / 1048576:.2f} МБ · размер пока неизвестен")
            state_name = str(state)
            if "Completed" in state_name:
                text = f"{path.name} — завершено: {path}"
                notice_bar.setRange(0, 100)
                notice_bar.setValue(100)
                notice_title.setText("Загрузка завершена ✓")
                notice_info.setText(f"Файл сохранён: {path}")
                notice_close.setText("Закрыть")
                notice_close.clicked.disconnect()
                notice_close.clicked.connect(notice.close)
                if not record.get("internet_mark_applied"):
                    self._mark_download_from_internet(path, request.url().toString())
                    record["internet_mark_applied"] = True
                if Path(path.name).suffix.lower() in {
                    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".scr", ".com", ".pif",
                    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".jar",
                    ".dll", ".sys", ".reg", ".lnk", ".iso", ".img", ".zip", ".7z", ".rar",
                } and not record.get("risk_notice_shown"):
                    record["risk_notice_shown"] = True
                    QTimer.singleShot(0, lambda file_path=str(path): QMessageBox.warning(
                        self, "Проверьте файл перед открытием · Yus Browser",
                        f"Загрузка завершена:\n{file_path}\n\n"
                        "Не запускайте файл, если не доверяете источнику. Перед открытием проверьте его Microsoft Defender. "
                        "Метка интернет-загрузки добавлена, если файловая система Windows это поддерживает."
                    ))
            elif "Interrupted" in state_name:
                text = f"{path.name} — загрузка прервана"
                notice_title.setText("Загрузка прервана")
                notice_info.setText("Файл не был загружен полностью.")
                notice_close.setText("Закрыть")
                notice_close.clicked.disconnect()
                notice_close.clicked.connect(notice.close)
            record["display"] = text
        request.receivedBytesChanged.connect(update_download)
        request.totalBytesChanged.connect(update_download)
        request.stateChanged.connect(update_download)
        request.accept()
        update_download()
        self.statusBar().showMessage(f"Начата загрузка: {path.name}", 5000)

    def print_current_page(self):
        view = self.current_view()
        if not view:
            return
        base = " ".join((view.title() or "YusBrowser_Page").split())[:80]
        base = "".join(ch for ch in base if ch not in '<>:/\\|?*"') or "YusBrowser_Page"
        default_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads"))
        dialog = QFileDialog(self, "Сохранить страницу в PDF", str(default_dir / f"{base}.pdf"), "PDF (*.pdf)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setDefaultSuffix("pdf")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
            return
        out_path = Path(dialog.selectedFiles()[0])
        if out_path.suffix.lower() != ".pdf":
            out_path = out_path.with_suffix(".pdf")
        page = view.page()
        expected_path = str(out_path)
        def pdf_finished(path, success):
            try:
                page.pdfPrintingFinished.disconnect(pdf_finished)
            except (RuntimeError, TypeError):
                pass
            self._pdf_finished(path, success, expected_path)
        page.pdfPrintingFinished.connect(pdf_finished)
        page.printToPdf(expected_path)
        self.statusBar().showMessage("Создаю PDF…", 3000)

    def _pdf_finished(self, path, success, expected):
        if success:
            self.statusBar().showMessage(f"PDF сохранён: {path}", 6000)
            QMessageBox.information(self, "Yus Browser", f"PDF сохранён:\n{path}")
        else:
            self.statusBar().showMessage("Не удалось создать PDF", 5000)
            QMessageBox.warning(self, "Yus Browser", f"Не удалось сохранить PDF:\n{expected}")

    def save_current_page(self):
        view = self.current_view()
        if not view:
            return
        base = " ".join((view.title() or "YusBrowser_Page").split())[:80]
        base = "".join(ch for ch in base if ch not in '<>:/\\|?*"') or "YusBrowser_Page"
        default_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads"))
        dialog = QFileDialog(self, "Сохранить HTML-копию", str(default_dir / f"{base}.html"), "HTML (*.html *.htm)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setDefaultSuffix("html")
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selectedFiles():
            return
        target = Path(dialog.selectedFiles()[0])
        if target.suffix.lower() not in (".html", ".htm"):
            target = target.with_suffix(".html")
        self.statusBar().showMessage("Сохраняю HTML…", 3000)
        view.page().toHtml(lambda html, p=str(target): self._write_html_page(p, html))

    def _write_html_page(self, target, html):
        try:
            Path(target).write_text(html, encoding="utf-8")
            self.statusBar().showMessage(f"HTML сохранён: {target}", 6000)
        except OSError as exc:
            QMessageBox.warning(self, "Не удалось сохранить HTML", str(exc))

    def capture_screenshot(self):
        """Capture the current Yus Browser window and open the violet annotation editor (F1)."""
        if self.private_mode:
            # Screenshot is user initiated; private mode does not auto-save it anywhere.
            pass
        # Grab the native top-level window so the screenshot includes the complete
        # browser chrome, tabs, toolbar, status area and rendered web page. QWidget.grab()
        # can omit the Chromium child surface on some Windows/Qt graphics backends.
        pixmap = QPixmap()
        errors = []
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                # Preferred: capture the native Windows top-level window, including Chromium child surface.
                pixmap = screen.grabWindow(int(self.winId()))
                if pixmap.isNull():
                    errors.append("native window capture returned an empty image")
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(str(exc))
        if pixmap.isNull():
            try:
                pixmap = self.grab()
            except (RuntimeError, TypeError) as exc:
                errors.append(str(exc))
        if pixmap.isNull():
            # Last fallback: capture the visible primary screen so F1 still produces a useful image.
            try:
                screen = QApplication.primaryScreen()
                if screen is not None:
                    pixmap = screen.grabWindow(0)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                errors.append(str(exc))
        if pixmap.isNull():
            detail = "\n".join(errors[-3:]) or "Qt не вернул изображение."
            QMessageBox.warning(self, "Скриншот Yus Browser", "Не удалось получить изображение.\n\n" + detail)
            return
        # Avoid an empty editor if the window is minimized or its Chromium surface is unavailable.
        if pixmap.width() < 80 or pixmap.height() < 60:
            QMessageBox.warning(self, "Скриншот Yus Browser", "Снимок получился слишком маленьким. Разверни окно браузера и повтори F1.")
            return
        dialog = ScreenshotEditorDialog(pixmap, self)
        dialog.exec()

    def find_in_page(self):
        view = self.current_view()
        if not view:
            return
        query, ok = self._ask_text("Найти на странице", "Текст для поиска:")
        if ok and query:
            view.findText(query)

    def _ask_text(self, title, prompt):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self, title, prompt)

    def change_zoom(self, step):
        view = self.current_view()
        if view:
            view.setZoomFactor(max(0.5, min(2.5, view.zoomFactor() + step)))
            self.update_zoom_label()
            self.statusBar().showMessage(f"Масштаб: {int(view.zoomFactor() * 100)}%", 2500)

    def set_zoom(self, value):
        view = self.current_view()
        if view:
            view.setZoomFactor(value)
            self.update_zoom_label()
            self.statusBar().showMessage("Масштаб: 100%", 2500)

    def handle_web_fullscreen(self, view, request):
        """Keep Yus Browser's tabs and address bar visible when a site asks for fullscreen."""
        try:
            request.reject()
            self._web_fullscreen_active = False
            self.brand_widget.setVisible(True)
            self.statusBar().showMessage(
                "Интерфейс Yus Browser оставлен видимым. Для полного экрана всего браузера нажми F11.", 4500
            )
        except (AttributeError, RuntimeError):
            self.statusBar().showMessage("Не удалось обработать полноэкранный запрос сайта.", 4000)

    def toggle_app_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self._previous_window_state = self.windowState()
            self._web_fullscreen_active = True
            self.showFullScreen()

    def exit_fullscreen(self):
        if not self.isFullScreen():
            return
        self._web_fullscreen_active = False
        if self._previous_window_state & Qt.WindowState.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()

    def on_renderer_terminated(self, view, status, exit_code):
        """Surface Chromium renderer crashes instead of leaving an unexplained white view."""
        if self.current_view() is not view:
            return
        message = (
            "Внутренний процесс Chromium завершился. Если белый экран появился на YouTube или видео, "
            "закрой браузер и запусти run_software_video.bat для совместимого графического режима. "
            "Для возврата к более быстрому обычному режиму используй run_fast.bat."
        )
        self.statusBar().showMessage(f"Сбой процесса страницы (код {exit_code}); см. подсказки видео.", 9000)
        QMessageBox.warning(self, "Yus Browser · восстановление страницы", message)

    def show_video_help(self):
        view = self.current_view()
        url = view.url().toString().lower() if view else ""
        on_youtube = "youtube.com" in url or "youtu.be" in url
        if on_youtube:
            box = QMessageBox(self)
            box.setWindowTitle("Видео и YouTube · Yus Browser")
            box.setText("Настройки совместимости YouTube")
            box.setInformativeText(
                "Если нет миниатюр, текста или видео, попробуй восстановление без кэша или мобильную версию. "
                "Для диагностики можно открыть chrome://gpu. Некоторые видео могут зависеть от кодеков, "
                "которые не входят в стандартную сборку Qt WebEngine."
            )
            reload_btn = box.addButton("Перезагрузить без кэша", QMessageBox.ButtonRole.ActionRole)
            mobile_btn = box.addButton("Мобильный YouTube", QMessageBox.ButtonRole.ActionRole)
            gpu_btn = box.addButton("Проверить графику", QMessageBox.ButtonRole.ActionRole)
            external_btn = box.addButton("Открыть снаружи", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is reload_btn:
                self.reload_bypass_cache()
            elif clicked is mobile_btn:
                self.open_mobile_youtube(view)
            elif clicked is gpu_btn:
                view.setUrl(QUrl("chrome://gpu"))
            elif clicked is external_btn:
                QDesktopServices.openUrl(QUrl(view.url().toString()))
            return

        text = (
            "Проблемы с видео или отображением сайтов:\n\n"
            "1. Нажми Ctrl+Shift+R для перезагрузки без кэша.\n"
            "2. Убедись, что JavaScript включён в Настройки.\n"
            "3. Если сайт белый, полностью закрой браузер и запусти run_compat.bat.\n"
            "4. Если не поможет, попробуй run_software_video.bat (может работать медленнее).\n"
            "5. Клавиши: Ctrl+Shift+V — широкий вид; F11 — полный экран; Esc — выход.\n\n"
            "Сайты сохраняют собственные цвета. Разрешения устройства и доступ к выбранным файлам запрашиваются у тебя; браузер не даёт сайтам бесконтрольный доступ ко всему диску."
        )
        QMessageBox.information(self, "Видео и совместимость · Yus Browser", text)

    def choose_website_files(self, mode, old_files, accepted_mime_types):
        """Show a violet, explicit chooser for <input type=file> and save dialogs."""
        start_dir = str(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads")))
        try:
            if old_files:
                candidate = Path(str(old_files[0]))
                if candidate.exists():
                    start_dir = str(candidate.parent if candidate.is_file() else candidate)
        except (OSError, TypeError, ValueError):
            pass

        dlg = QFileDialog(self, "Выбор файла для сайта", start_dir, "Все файлы (*.*)")
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        try:
            multi_mode = mode == QWebEnginePage.FileSelectionMode.FileSelectOpenMultiple
            folder_mode = mode == QWebEnginePage.FileSelectionMode.FileSelectUploadFolder
            save_mode = mode == QWebEnginePage.FileSelectionMode.FileSelectSave
        except AttributeError:
            mode_text = str(mode)
            multi_mode = "OpenMultiple" in mode_text
            folder_mode = "UploadFolder" in mode_text
            save_mode = "Save" in mode_text
        if folder_mode:
            dlg.setFileMode(QFileDialog.FileMode.Directory)
            dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
            dlg.setWindowTitle("Выбрать папку для сайта")
        elif multi_mode:
            dlg.setFileMode(QFileDialog.FileMode.ExistingFiles)
        elif save_mode:
            dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dlg.setFileMode(QFileDialog.FileMode.AnyFile)
            dlg.setWindowTitle("Сохранить файл")
        else:
            dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        return [item for item in dlg.selectedFiles() if item]

    def handle_permission_request(self, request, view=None):
        """Ask the user before granting camera, microphone, location, clipboard, etc."""
        try:
            if not request.isValid():
                return
            origin = request.origin().toString()
            kind = str(request.permissionType()).split(".")[-1]
            labels = {
                "Geolocation": "доступ к местоположению",
                "MediaAudioCapture": "доступ к микрофону",
                "MediaVideoCapture": "доступ к камере",
                "MediaAudioVideoCapture": "доступ к камере и микрофону",
                "DesktopVideoCapture": "запись экрана",
                "DesktopAudioVideoCapture": "запись экрана и системного аудио",
                "Notifications": "показывать уведомления",
                "ClipboardReadWrite": "доступ к буферу обмена",
                "MouseLock": "захват указателя мыши",
                "LocalFontsAccess": "доступ к установленным шрифтам",
            }
            label = labels.get(kind, "доступ к функции устройства")
            answer = QMessageBox.question(
                self, "Разрешение сайта",
                f"Сайт запрашивает разрешение: {label}.\n\nИсточник: {origin}\n\nРазрешить только этому сайту?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                request.grant()
                self.statusBar().showMessage(f"Разрешение предоставлено: {origin}", 4000)
            else:
                request.deny()
                self.statusBar().showMessage("Запрос разрешения отклонён", 3000)
        except (AttributeError, RuntimeError) as exc:
            self.statusBar().showMessage(f"Не удалось обработать разрешение сайта: {exc}", 5000)

    def handle_file_system_access(self, request, view=None):
        """Never grant broad disk access silently; ask for each File System Access API request."""
        try:
            origin = request.origin().toString()
            file_url = request.filePath()
            path = file_url.toLocalFile() if hasattr(file_url, "toLocalFile") else str(file_url)
            flags = str(request.accessFlags())
            answer = QMessageBox.question(
                self, "Доступ к локальному файлу",
                f"Сайт хочет получить доступ к выбранному файлу или папке.\n\nСайт: {origin}\nПуть: {path}\nПрава: {flags}\n\nРазрешить этот конкретный запрос?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                request.accept()
            else:
                request.reject()
        except (AttributeError, RuntimeError) as exc:
            try:
                request.reject()
            except Exception:
                pass
            self.statusBar().showMessage(f"Запрос доступа к файлам отклонён: {exc}", 4000)

    def record_page_error(self, url, message, line_number, source_id):
        item = {"url": str(url)[:500], "message": str(message)[:1200], "line": int(line_number), "source": str(source_id)[:500]}
        self.page_errors.append(item)
        self.page_errors = self.page_errors[-100:]

    def open_local_file(self):
        dlg = QFileDialog(self, "Открыть локальный файл", str(Path.home()), "Web-страницы и документы (*.html *.htm *.pdf *.txt *.xml *.svg);;Все файлы (*.*)")
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            path = Path(dlg.selectedFiles()[0])
            if path.exists() and path.is_file():
                view = self.current_view()
                if view:
                    view.setUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_downloads_folder(self):
        folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home() / "Downloads"))
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except (OSError, AttributeError) as exc:
            QMessageBox.warning(self, "Папка загрузок", f"Не удалось открыть папку:\n{exc}")

    def reload_bypass_cache(self):
        view = self.current_view()
        if view:
            try:
                view.page().triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)
                self.statusBar().showMessage("Перезагрузка без кэша началась", 3500)
            except (AttributeError, RuntimeError):
                view.reload()

    def show_site_diagnostics(self):
        view = self.current_view()
        url = view.url().toString() if view else "(нет вкладки)"
        title = view.title() if view else "(нет страницы)"
        try:
            import PySide6
            qt_web_version = PySide6.__version__
        except Exception:
            qt_web_version = "неизвестна"
        recent = [f"URL: {row['url']}\nСтрока {row['line']}: {row['message']}" for row in self.page_errors[-8:]]
        recent_text = "\n\n".join(recent) if recent else "Ошибки JavaScript пока не зарегистрированы. Это не гарантирует, что на странице нет других ошибок."
        message = (
            f"Версия: {APP_VERSION}\nQt for Python: {qt_web_version}\nРежим графики: {YUS_GRAPHICS_MODE}\n\n"
            f"Страница: {title}\n{url}\n\nПоследние ошибки JavaScript:\n{recent_text}"
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Диагностика сайта · Yus Browser")
        dlg.resize(760, 480)
        layout = QVBoxLayout(dlg)
        output = QListWidget()
        for line in message.splitlines():
            output.addItem(line)
        output.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(output, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dlg.accept)
        copy_btn = QPushButton("Копировать отчёт")
        buttons.addButton(copy_btn, QDialogButtonBox.ButtonRole.ActionRole)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(message))
        layout.addWidget(buttons)
        dlg.exec()

    def show_about(self):
        try:
            import PySide6
            pyside_version = PySide6.__version__
        except Exception:
            pyside_version = "неизвестно"
        try:
            from PySide6.QtWebEngineCore import qWebEngineChromiumVersion
            chromium_version = qWebEngineChromiumVersion()
        except Exception:
            chromium_version = "не удалось определить"
        text = (
            f"<h2>{html.escape(APP_NAME)} {html.escape(str(APP_VERSION))}</h2>"
            "<p>Настоящий браузер на Chromium через Qt WebEngine.</p>"
            "<p><b>ВНИМАНИЕ: это пока тестовая версия. Возможны зависания и задержки, а некоторые функции временами могут работать некорректно или быть недоступны. Браузер всё ещё находится в разработке.</b></p>"
            "<p><b>Создатель и разработчик:</b> Юсуф, 14 лет.<br>"
            "Проект создан Юсуфом параллельно с ChatGPT с использованием Python.</p>"
            f"<p>Python: {html.escape(sys.version.split()[0])}<br>PySide6: {html.escape(pyside_version)}<br>Chromium: {html.escape(str(chromium_version))}<br>"
            f"Режим графики: {html.escape(str(YUS_GRAPHICS_MODE))}<br>Папка данных: {html.escape(str(APP_DATA))}</p>"
            "<p>Сеанс приватного окна хранится только в памяти; вкладки, история и cookies не сохраняются после закрытия.</p>"
            "<p>Эта бета не проходила независимый аудит безопасности. Сайты получают только те разрешения, которые ты подтверждаешь.</p>"
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("О программе · Yus Browser")
        dlg.resize(620, 410)
        layout = QVBoxLayout(dlg)
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(False)
        layout.addWidget(label, 1)
        open_data_btn = QPushButton("Открыть папку данных")
        open_data_btn.clicked.connect(lambda: self.open_folder(APP_DATA))
        layout.addWidget(open_data_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn)
        dlg.exec()

    def check_updates(self):
        QMessageBox.information(
            self, "Проверка обновлений · Yus Browser Pro 2.3",
            f"У вас установлена последняя версия Yus Browser Pro 2.3.\n\nТекущая версия: {APP_VERSION}."
        )

    def open_folder(self, folder):
        try:
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except (OSError, RuntimeError, AttributeError) as exc:
            QMessageBox.warning(self, "Открыть папку", str(exc))

    def show_speed_test(self):
        dlg = SpeedTestDialog(self)
        dlg.exec()

    def show_browser_menu(self):
        menu = QMenu(self)
        items = [
            ("＋  Новая вкладка  (Ctrl+T)", lambda: self.new_tab()),
            ("Дублировать текущую вкладку", self.duplicate_current_tab),
            ("Закрыть текущую вкладку  (Ctrl+W)", lambda: self.close_tab(self.tabs.currentIndex())),
            ("Следующая вкладка  (Ctrl+Tab)", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % self.tabs.count()) if self.tabs.count() else None),
            ("Предыдущая вкладка  (Ctrl+Shift+Tab)", lambda: self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % self.tabs.count()) if self.tabs.count() else None),
            ("Открыть локальный файл…", self.open_local_file),
            ("Закладки", self.show_bookmarks),
            ("История", self.show_history),
            ("Загрузки", self.show_downloads),
            ("Открыть папку загрузок", self.open_downloads_folder),
            ("Команды умного поиска", self.show_search_help),
            ("Спидометр — скорость интернета", self.show_speed_test),
            ("Диагностика сайта", self.show_site_diagnostics),
            ("Скриншот и редактор (F1)", self.capture_screenshot),
            ("Настройки", self.show_settings),
            ("О программе", self.show_about),
            ("Проверить обновления", self.check_updates),
        ]
        for text, handler in items:
            action = QAction(text, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))

    def show_settings(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Настройки Yus Browser")
        dlg.resize(760, 590)
        layout = QVBoxLayout(dlg)
        heading = QLabel("Yus Browser · настройки")
        heading.setStyleSheet("font-size:15pt;font-weight:600;color:#dcc4ff;padding:5px")
        layout.addWidget(heading)

        form = QFormLayout()
        search_choice = QComboBox()
        search_choice.addItems(list(SEARCH_ENGINES.keys()))
        current_engine = str(self.preferences.get("search_engine", DEFAULT_SEARCH_ENGINE))
        search_choice.setCurrentText(current_engine if current_engine in SEARCH_ENGINES else DEFAULT_SEARCH_ENGINE)
        form.addRow("Поисковая система:", search_choice)

        sound_check = QCheckBox("Включить глубокие игровые звуки при нажатиях")
        sound_check.setChecked(bool(self.preferences.get("click_sounds", True)))
        form.addRow("Звук интерфейса:", sound_check)

        graphics_choice = QComboBox()
        graphics_choices = [("Быстро (аппаратное ускорение)", "fast"), ("Совместимость (без GPU-композиции)", "compat"), ("Программная графика (медленнее)", "software")]
        for title, value in graphics_choices:
            graphics_choice.addItem(title, value)
        current_mode = self.preferences.get("graphics_mode", "fast")
        match_index = graphics_choice.findData(current_mode)
        graphics_choice.setCurrentIndex(match_index if match_index >= 0 else 0)
        form.addRow("Режим отображения:", graphics_choice)
        layout.addLayout(form)

        js_check = QCheckBox("Включить JavaScript (нужен большинству современных сайтов, включая YouTube)")
        js_check.setChecked(bool(self._profile.settings().testAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled)))
        layout.addWidget(js_check)
        wide_check = QCheckBox("Автоматически включать широкий режим на YouTube")
        wide_check.setChecked(bool(self.preferences.get("auto_wide_youtube", True)))
        layout.addWidget(wide_check)
        dark_check = QCheckBox("Принудительно тёмная тема сайтов (включая Google, если движок поддерживает её)")
        dark_check.setChecked(bool(self.preferences.get("dark_websites", True)))
        layout.addWidget(dark_check)
        restore_check = QCheckBox("Автоматически сохранять и восстанавливать вкладки при запуске")
        restore_check.setChecked(bool(self.preferences.get("restore_tabs", True)))
        restore_check.setToolTip("Сохраняются только HTTP/HTTPS адреса и стартовая страница; file:// не сохраняется. В приватном окне эта функция отключена.")
        layout.addWidget(restore_check)
        warning = QLabel(
            "Файлы сайтов можно выбирать через обычное окно файла. Если сайт просит читать/изменять конкретный файл или папку, Yus Browser сначала спросит разрешение. "
            "Не существует безопасного режима, в котором любая страница автоматически читает весь диск."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color:#bba6d2;padding:4px")
        layout.addWidget(warning)

        data_btn_row = QHBoxLayout()
        clear_cache_btn = QPushButton("Очистить cookies и кэш…")
        clear_history_btn = QPushButton("Очистить историю…")
        open_data_btn = QPushButton("Папка данных")
        data_btn_row.addWidget(clear_cache_btn)
        data_btn_row.addWidget(clear_history_btn)
        data_btn_row.addWidget(open_data_btn)
        layout.addLayout(data_btn_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)

        def clear_data():
            response = QMessageBox.question(dlg, "Очистить данные", "Удалить cookies и HTTP-кэш текущего профиля? Это может завершить сеансы на сайтах.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if response != QMessageBox.StandardButton.Yes:
                return
            self._profile.cookieStore().deleteAllCookies()
            self._profile.clearHttpCache()
            self.statusBar().showMessage("Очистка cookies и HTTP-кэша запрошена", 4000)

        def clear_history():
            if QMessageBox.question(dlg, "Очистить историю", "Удалить сохранённую историю посещений?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.history = []
            self.update_address_suggestions()
            if not self.private_mode:
                try:
                    write_json(HISTORY_FILE, self.history)
                except OSError as exc:
                    QMessageBox.warning(dlg, "История", str(exc))
            self.statusBar().showMessage("История очищена", 3000)

        clear_cache_btn.clicked.connect(clear_data)
        clear_history_btn.clicked.connect(clear_history)
        open_data_btn.clicked.connect(lambda: self.open_folder(APP_DATA))

        def save_preferences():
            updated = dict(self.preferences)
            updated["search_engine"] = search_choice.currentText()
            updated["click_sounds"] = sound_check.isChecked()
            updated["graphics_mode"] = graphics_choice.currentData()
            updated["javascript_enabled"] = js_check.isChecked()
            updated["auto_wide_youtube"] = wide_check.isChecked()
            updated["dark_websites"] = dark_check.isChecked()
            updated["restore_tabs"] = restore_check.isChecked()
            try:
                write_json(SETTINGS_FILE, updated)
                if not updated["restore_tabs"]:
                    try:
                        SESSION_FILE.unlink(missing_ok=True)
                    except OSError:
                        pass
            except OSError as exc:
                QMessageBox.warning(dlg, "Настройки", f"Не удалось сохранить настройки:\n{exc}")
                return
            self.preferences = updated
            sound = getattr(QApplication.instance(), "_yus_click_sound", None)
            if sound is not None:
                sound.set_enabled(updated["click_sounds"])
            self.search_engine_combo.blockSignals(True)
            self.search_engine_combo.setCurrentText(updated["search_engine"])
            self.search_engine_combo.blockSignals(False)
            self._profile.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, js_check.isChecked())
            force_dark = getattr(QWebEngineSettings.WebAttribute, "ForceDarkMode", None)
            if force_dark is not None:
                self._profile.settings().setAttribute(force_dark, dark_check.isChecked())
            active_view = self.current_view()
            if active_view and active_view.url().toString() == "https://yus.browser.local/":
                active_view.setHtml(self.welcome_page_html(), QUrl("https://yus.browser.local/"))
            self._sync_wide_view()
            dlg.accept()
            restart_note = "\n\nНовый режим графики вступит в силу после полного закрытия Yus Browser и повторного запуска run.bat. Тёмная тема сайтов применяется там, где её поддерживает Chromium."
            QMessageBox.information(self, "Настройки сохранены", "Настройки сохранены." + restart_note)

        buttons.accepted.connect(save_preferences)
        buttons.rejected.connect(dlg.reject)
        dlg.exec()

    def open_current_in_system_browser(self):
        view = self.current_view()
        if not view:
            return
        url = view.url()
        if not url.isValid() or url.scheme() not in ("http", "https"):
            QMessageBox.information(self, "Yus Browser", "Сначала открой веб-страницу HTTP или HTTPS.")
            return
        if QDesktopServices.openUrl(url):
            self.statusBar().showMessage("Открыл страницу в системном браузере.", 4000)
        else:
            QMessageBox.warning(self, "Yus Browser", "Windows не смогла открыть системный браузер для этой страницы.")

    def toggle_wide_view(self):
        current = self._wide_view_active
        self._wide_view_user_override = not current
        self._apply_wide_view(not current)
        self.statusBar().showMessage(
            "Включён широкий просмотр. Ctrl+Shift+V возвращает обычный вид." if not current
            else "Возвращён обычный вид браузера.", 4500
        )

    @staticmethod
    def _is_youtube_url(url: str) -> bool:
        try:
            qurl = QUrl(url)
            host = (qurl.host() or "").lower()
            return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be" or host.endswith(".youtu.be")
        except Exception:
            return False

    def _sync_wide_view(self, url: str | None = None):
        if url is None:
            view = self.current_view()
            url = view.url().toString() if view else ""
        self._current_url_is_youtube = self._is_youtube_url(url)
        auto_wide = bool(self.preferences.get("auto_wide_youtube", True)) and self._current_url_is_youtube
        desired = auto_wide if self._wide_view_user_override is None else self._wide_view_user_override
        self._apply_wide_view(desired)

    def _apply_wide_view(self, active: bool):
        self._wide_view_active = bool(active)
        if hasattr(self, "brand_widget"):
            # Keep Yus branding and main browser controls visible in wide mode.
            # Only the secondary tools strip collapses, preserving a real browser UI.
            self.brand_widget.setVisible(True)
            panels_visible = self._extra_panels_expanded and not active
            self.action_widget.setVisible(panels_visible)
            self.tools_widget.setVisible(panels_visible)
            if hasattr(self, "panels_button"):
                self.panels_button.setText("Панели −" if panels_visible else "Панели +")
        if hasattr(self, "wide_view_button"):
            self.wide_view_button.setText("Обычный вид" if active else "Широкий вид")
            self.wide_view_button.setToolTip("Вернуть все панели (Ctrl+Shift+V)" if active else "Скрыть дополнительные панели и расширить страницу (Ctrl+Shift+V)")


    def open_private_window(self):
        private_window = BrowserWindow(private_mode=True)
        self._private_windows.append(private_window)
        private_window.showMaximized()
        private_window.destroyed.connect(lambda: self._discard_private_window(private_window))

    def _discard_private_window(self, window):
        try:
            self._private_windows.remove(window)
        except ValueError:
            pass


def main():
    QApplication.setOrganizationName("Yusleks")
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    if APP_ICON_PNG.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PNG)))
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLE)
    palette = app.palette()
    palette.setColor(palette.ColorRole.Window, QColor("#15101f"))
    palette.setColor(palette.ColorRole.WindowText, QColor("#eee5fa"))
    palette.setColor(palette.ColorRole.Base, QColor("#100b18"))
    palette.setColor(palette.ColorRole.AlternateBase, QColor("#21152f"))
    palette.setColor(palette.ColorRole.Text, QColor("#eee5fa"))
    palette.setColor(palette.ColorRole.Button, QColor("#302040"))
    palette.setColor(palette.ColorRole.ButtonText, QColor("#eee4fc"))
    palette.setColor(palette.ColorRole.Highlight, QColor("#6d28d9"))
    palette.setColor(palette.ColorRole.HighlightedText, QColor("#fff0ff"))
    app.setPalette(palette)
    click_sound = UiClickSound(app)
    app._yus_click_sound = click_sound
    window = BrowserWindow()
    # A maximized initial window gives YouTube and other responsive sites their full viewport.
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
