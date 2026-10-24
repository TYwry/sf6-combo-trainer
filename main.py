"""SF6 連段練習器：程式入口。"""
import logging
import sys
import traceback
from pathlib import Path
from logging.handlers import RotatingFileHandler

from app_info import (APP_DIR, APP_DISPLAY_NAME, APP_NAME, APP_USER_MODEL_ID, APP_VERSION, ASSETS_DIR, FROZEN,
                      logs_dir)


def setup_logging():
    handler = RotatingFileHandler(logs_dir() / "app.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def set_app_id():
    if sys.platform == "win32":
        import ctypes
        # 讓工作列把這個程式當成獨立 App，顯示自己的圖示（不歸到 Python 底下）
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def main():
    setup_logging()
    logging.info("啟動 %s v%s", APP_NAME, APP_VERSION)
    set_app_id()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setQuitOnLastWindowClosed(False)      # 關掉主視窗時縮到系統匣，由選單的「結束」關閉程式
    start_in_tray = "--tray" in sys.argv
    icon = ASSETS_DIR / "app_ink.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    # 同時只能開一個：已經開著就叫出原本的視窗
    server_name = "sf6-combo-trainer-single"
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    from ui import theme
    theme.apply_palette(app)

    shown_errors: set[str] = set()
    showing = [False]

    def show_error(exc_type, exc, tb):
        logging.error("未預期的錯誤：%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        # 同一個錯誤只跳一次視窗，視窗開著時也不再疊新的，才不會一直跳、關不掉
        key = f"{exc_type.__name__}:{exc}"
        if showing[0] or key in shown_errors:
            return
        shown_errors.add(key)
        showing[0] = True
        try:
            QMessageBox.warning(None, APP_DISPLAY_NAME,
                                f"程式發生錯誤，但會繼續執行。\n\n{exc}\n\n"
                                f"詳細記錄在：{logs_dir() / 'app.log'}")
        finally:
            showing[0] = False
    sys.excepthook = show_error

    from data.config import load_config
    cfg, message = load_config()
    if cfg.get("ui_font") == "jhenghei" or cfg.get("ui_theme", "light") != "light":
        theme.set_font_mode(cfg.get("ui_font", "hand"))
        theme.set_theme_mode(cfg.get("ui_theme", "light"))
        theme.apply_palette(app)
    if message:
        QMessageBox.information(None, APP_DISPLAY_NAME, message)

    from ui.main_window import MainWindow
    window = MainWindow(cfg)

    def bring_to_front():
        conn = server.nextPendingConnection()
        if conn:
            conn.close()
        window.show_main()
    server.newConnection.connect(bring_to_front)

    def on_state(state):
        window.set_foreground(state == Qt.ApplicationActive)
    app.applicationStateChanged.connect(on_state)

    def on_scheme(_):
        theme.apply_palette(app)
        window.apply_theme()
    try:
        app.styleHints().colorSchemeChanged.connect(on_scheme)
    except AttributeError:
        pass

    from PySide6.QtCore import QTimer
    from ui.tray import Tray
    if start_in_tray and Tray.available():
        # 開機自動啟動：直接縮在系統匣
        QTimer.singleShot(300, lambda: window.start_background(quiet=True))
    else:
        if window.start_maximized:
            window.showMaximized()
        else:
            window.show()
        QTimer.singleShot(300, window.start_background)
        QTimer.singleShot(600, window.ask_shortcut_once)
    code = app.exec()
    if window.restart_requested:
        # 換字體、主題：先放開「只能開一個」的佔位，再開一個新的程式
        server.close()
        import subprocess
        args = [a for a in sys.argv[1:] if a != "--tray"]
        # 分享版直接重開 exe；自用版用同一個 Python 重新執行 main.py
        cmd = [sys.executable, *args] if FROZEN else [sys.executable, str(Path(__file__).resolve()), *args]
        try:
            subprocess.Popen(cmd, cwd=str(APP_DIR))
        except OSError:
            logging.exception("重新開啟程式失敗")
    return code


if __name__ == "__main__":
    sys.exit(main())
