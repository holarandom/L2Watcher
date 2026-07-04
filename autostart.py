# autostart.py
"""
Управление автозагрузкой приложения через реестр Windows
(HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run).

HKCU (не HKLM) — потому что не требует прав администратора:
запись в свой пользовательский раздел реестра доступна без UAC-запроса,
что важно для друзей, которые просто запускают .exe двойным кликом.

Работает в двух режимах:
- собранный .exe (PyInstaller): в автозагрузку прописывается путь к самому .exe
- обычный .py запуск: прописывается "python ...\\main.py" (для разработки)
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)

APP_NAME = "L2Monitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _get_launch_command() -> str:
    """
    Возвращает команду, которую нужно прописать в автозагрузку.

    В собранном .exe sys.frozen == True и sys.executable — это путь к .exe.
    В обычном запуске .py — это путь к python.exe, и нужно дописать main.py.
    Пути оборачиваем в кавычки на случай пробелов в пути
    (а у тебя путь как раз с пробелом: 'monitor exe').
    """
    if getattr(sys, "frozen", False):
        # Собранный .exe — просто путь к нему
        return f'"{sys.executable}"'
    else:
        # Режим разработки: python + путь к main.py
        main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        return f'"{sys.executable}" "{main_py}"'


def is_enabled() -> bool:
    """Проверяет, прописано ли приложение в автозагрузке."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except Exception as e:
        logger.error(f"Ошибка чтения автозагрузки: {e}")
        return False


def enable() -> bool:
    """Добавляет приложение в автозагрузку. Возвращает True при успехе."""
    if sys.platform != "win32":
        logger.warning("Автозагрузка поддерживается только на Windows")
        return False
    try:
        import winreg
        command = _get_launch_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        logger.info(f"Добавлено в автозагрузку: {command}")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления в автозагрузку: {e}")
        return False


def disable() -> bool:
    """Убирает приложение из автозагрузки. Возвращает True при успехе
    (или если записи и так не было)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
                logger.info("Убрано из автозагрузки")
            except FileNotFoundError:
                # Записи и не было — это не ошибка
                pass
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления из автозагрузки: {e}")
        return False


def set_enabled(enabled: bool) -> bool:
    """Удобный переключатель: enable() или disable() в зависимости от флага."""
    return enable() if enabled else disable()
