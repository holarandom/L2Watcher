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
        # Версия доступного обновления (None = обновления нет). Пункт меню
        # "Скачать обновление" показывается только когда здесь что-то есть.
        self._update_version = None

    def _build_icon(self):
        import pystray
        from version import APP_NAME, APP_VERSION, APP_ID
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
        # Пункт обновления: текст и видимость — функции, а не строки.
        # pystray пересчитывает их при каждом открытии меню, поэтому пункт
        # появляется сам, как только фоновая проверка нашла новую версию,
        # и пересобирать меню не нужно.
        #
        # Зачем вообще: раньше об обновлении сообщал ТОЛЬКО Telegram-бот.
        # Если Telegram недоступен (а он недоступен регулярно), человек про
        # новую версию не узнавал вообще. Теперь она видна в самой программе.
        menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: f"🆕 Скачать версию {self._update_version}",
                self._handle_update,
                visible=lambda item: self._update_version is not None,
            ),
            pystray.MenuItem("Настройки", self._handle_settings, default=True),
            pystray.MenuItem("Открыть лог", self._handle_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self._handle_exit),
        )

        self._icon = pystray.Icon(
            APP_ID, image, f"{APP_NAME} v{APP_VERSION}", menu,
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

    def _handle_update(self, icon, item):
        """Открывает страницу релизов в браузере."""
        try:
            from update_checker import RELEASES_PAGE
            webbrowser.open(RELEASES_PAGE)
        except Exception as e:
            logger.error(f"Не удалось открыть страницу обновления: {e}")

    def set_update_available(self, version: str):
        """
        Сообщает трею, что вышла новая версия: появляется пункт меню
        «Скачать версию X», всплывашка и приписка в подсказке.
        Вызывается фоновой проверкой обновлений.
        """
        # Проверка обновлений ходит на GitHub раз в сутки и зовёт этот метод
        # каждый раз. Пункт меню обновляем всегда, а всплывашку показываем
        # ТОЛЬКО когда версия действительно новая — иначе одно и то же
        # окошко выскакивало бы каждые 24 часа и раздражало.
        already_known = (version == self._update_version)
        self._update_version = version
        if already_known:
            return
        try:
            from version import APP_NAME, APP_VERSION
            if self._icon is not None:
                self._icon.title = (f"{APP_NAME} v{APP_VERSION} — "
                                    f"доступна версия {version}")
        except Exception as e:
            logger.debug(f"Подсказка трея не обновлена: {e}")
        self.notify(
            f"Доступна версия {version}. Нажми на иконку в трее правой "
            f"кнопкой → Скачать.",
            "Вышло обновление L2 Watcher",
        )

    def notify(self, message: str, title: str = None):
        """
        Всплывающее уведомление Windows у иконки трея.

        Нужно, чтобы о смерти было слышно и видно даже когда Telegram
        недоступен (блокировки, отвалившийся VPN). Средствами pystray —
        отдельная библиотека для тостов не нужна.
        """
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as e:
            logger.debug(f"Всплывающее уведомление не показано: {e}")

    def run_in_background(self):
        """Запускает трей в отдельном потоке — не блокирует основной asyncio event loop."""
        self._build_icon()
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._icon:
            self._icon.stop()
