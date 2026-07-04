# tray.py
"""
Иконка в системном трее с меню: Настройки, Открыть лог, Выход.

Без этого после сборки в .exe (без видимой консоли) у пользователя
не было бы способа ни закрыть программу нормально, ни открыть
настройки повторно — только через Диспетчер задач.
"""
import os
import sys
import threading
import logging
import webbrowser

logger = logging.getLogger(__name__)

ICON_FILENAME = "tray_icon.png"


def _resource_path(filename: str) -> str:
    """
    Находит путь к ресурсу (иконке) и в режиме обычного запуска (.py),
    и при запуске из собранного PyInstaller .exe (где ресурсы лежат
    во временной папке sys._MEIPASS).
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


class TrayIcon:
    def __init__(self, on_open_settings, on_exit, log_path: str):
        self.on_open_settings = on_open_settings
        self.on_exit = on_exit
        self.log_path = log_path
        self._icon = None
        self._thread = None

    def _build_icon(self):
        import pystray
        from PIL import Image

        icon_path = _resource_path(ICON_FILENAME)
        try:
            image = Image.open(icon_path)
        except Exception as e:
            logger.error(f"Не удалось загрузить иконку трея ({icon_path}): {e}")
            # Запасной вариант — простая цветная заглушка, чтобы трей
            # всё равно появился, даже если файл иконки потерялся
            image = Image.new("RGB", (64, 64), (45, 140, 80))

        # default=True на "Настройки" — это штатный механизм pystray:
        # клик/двойной клик по иконке в трее (Windows) вызывает именно
        # default-пункт меню. Раньше для этого использовался on_activate,
        # но он на Windows срабатывает ненадёжно и не на всех версиях
        # pystray — поэтому переехали на default-пункт.
        menu = pystray.Menu(
            pystray.MenuItem("Настройки", self._handle_settings, default=True),
            pystray.MenuItem("Открыть лог", self._handle_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._handle_exit),
        )

        self._icon = pystray.Icon(
            "L2Monitor", image, "L2 Monitor", menu,
        )

    def _handle_settings(self, icon, item):
        if self.on_open_settings:
            self.on_open_settings()

    def _handle_open_log(self, icon, item):
        # Логируем путь — если пункт "не работает", в логе будет видно,
        # по какому пути приложение искало файл и что пошло не так.
        logger.info(f"Открытие лога по запросу из трея: {self.log_path}")
        if not os.path.exists(self.log_path):
            logger.warning(f"Файл лога не найден: {self.log_path}")
            return
        if sys.platform != "win32":
            webbrowser.open(self.log_path)
            return
        # os.startfile падает с OSError, если у расширения .log нет
        # ассоциации с программой в системе (на части машин .log ни с чем
        # не связан). Поэтому пробуем startfile, а при неудаче — явно
        # открываем через notepad.
        try:
            os.startfile(self.log_path)
        except Exception as e:
            logger.warning(f"startfile не смог открыть лог ({e}), пробую notepad")
            try:
                import subprocess
                subprocess.Popen(["notepad.exe", self.log_path])
            except Exception as e2:
                logger.error(f"Не удалось открыть лог и через notepad: {e2}")

    def _handle_exit(self, icon, item):
        icon.stop()
        if self.on_exit:
            self.on_exit()

    def run_in_background(self):
        """Запускает трей в отдельном потоке — не блокирует основной asyncio event loop."""
        self._build_icon()
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon:
            self._icon.stop()
