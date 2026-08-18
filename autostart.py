# autostart.py
"""
Управление автозагрузкой приложения — через ЯРЛЫК в папке «Автозагрузка».

Почему не через реестр (как было до 1.1.0).
Раньше путь к программе прописывался в
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run. Работало это
нормально, но запись в ключ Run — классический признак «программа
закрепляется в системе, чтобы пережить перезагрузку», и антивирусы ищут
его в первую очередь. Windows Defender прямо назвал наш runkey среди
причин, по которым пометил сборку как Trojan:Win32/Wacatac.C!ml.

Ярлык в папке «Автозагрузка» делает ровно то же самое, но так поступают
обычные установщики, и подозрений это не вызывает. Для пользователя
ничего не меняется: та же галочка в настройках, тот же результат.

ВАЖНО: автозагрузка включается ТОЛЬКО по галочке в настройках. Сама
программа себя туда никогда не прописывает.

Работает в двух режимах:
- собранный .exe (PyInstaller): ярлык указывает на сам .exe
- обычный .py запуск: ярлык указывает на pythonw.exe с main.py (для разработки)
"""
import os
import sys
import logging

logger = logging.getLogger(__name__)

# Имя, под которым программа была прописана в реестре до 1.1.0.
# Нужно только для разового переезда на ярлык — новые записи туда не идут.
LEGACY_APP_NAME = "L2Monitor"
LEGACY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Имя файла ярлыка. Здесь публичное название — человек видит этот файл
# в папке автозагрузки, и "L2Monitor.lnk" его бы только запутал.
SHORTCUT_NAME = "L2 Watcher.lnk"


def _startup_dir() -> str:
    """
    Путь к папке «Автозагрузка» текущего пользователя.

    Основной способ — спросить у самой Windows (CSIDL_STARTUP): он
    правильный даже если папку перенесли или система нестандартная.
    Запасной — собрать путь руками; со времён Vista физический путь всегда
    английский, локализуется только отображаемое имя.
    """
    try:
        import ctypes
        from ctypes import wintypes
        CSIDL_STARTUP = 0x0007
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # SHGetFolderPathW(hwnd, csidl, token, flags, out) -> 0 при успехе
        if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_STARTUP, None, 0, buf) == 0:
            if buf.value:
                return buf.value
    except Exception as e:
        logger.debug(f"SHGetFolderPath не сработал: {e}")

    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def _shortcut_path() -> str:
    return os.path.join(_startup_dir(), SHORTCUT_NAME)


def _launch_target():
    """
    Возвращает (что запускать, аргументы, рабочая папка).

    В собранном .exe sys.executable — путь к самому .exe, аргументы не нужны.
    В режиме разработки запускаем через pythonw.exe (без чёрного окна консоли).
    """
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return exe, "", os.path.dirname(exe)

    here = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(here, "main.py")
    # pythonw рядом с python — запуск без консольного окна
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    return exe, f'"{main_py}"', here


def is_enabled() -> bool:
    """Проверяет, есть ли ярлык в автозагрузке."""
    if sys.platform != "win32":
        return False
    try:
        return os.path.exists(_shortcut_path())
    except Exception as e:
        logger.error(f"Ошибка проверки автозагрузки: {e}")
        return False


def enable() -> bool:
    """Создаёт ярлык в автозагрузке. Возвращает True при успехе."""
    if sys.platform != "win32":
        logger.warning("Автозагрузка поддерживается только на Windows")
        return False
    try:
        import win32com.client

        target, args, workdir = _launch_target()
        path = _shortcut_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)

        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortCut(path)
        link.TargetPath = target
        link.Arguments = args
        link.WorkingDirectory = workdir
        link.Description = "L2 Watcher — мониторинг окон Lineage II"
        # Иконка — сам exe. В режиме разработки у pythonw своя, и это нормально.
        try:
            link.IconLocation = target
        except Exception:
            pass
        link.save()

        logger.info(f"Добавлено в автозагрузку (ярлык): {path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления в автозагрузку: {e}")
        return False


def disable() -> bool:
    """Убирает ярлык из автозагрузки (и старую запись в реестре, если осталась).
    Возвращает True, если после вызова автозапуска точно нет."""
    if sys.platform != "win32":
        return False
    ok = True
    try:
        path = _shortcut_path()
        if os.path.exists(path):
            os.remove(path)
            logger.info("Убрано из автозагрузки (ярлык удалён)")
    except Exception as e:
        logger.error(f"Ошибка удаления ярлыка автозагрузки: {e}")
        ok = False

    # Подчищаем и реестр — вдруг переезд не отработал раньше, и тогда
    # программа продолжала бы стартовать вопреки снятой галочке.
    if _legacy_registry_exists():
        ok = _remove_legacy_registry() and ok
    return ok


def set_enabled(enabled: bool) -> bool:
    """Удобный переключатель: enable() или disable() в зависимости от флага."""
    return enable() if enabled else disable()


# ── Переезд со старой записи в реестре ────────────────────────────────

def _legacy_registry_exists() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LEGACY_RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                value, _ = winreg.QueryValueEx(key, LEGACY_APP_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except Exception:
        return False


def _remove_legacy_registry() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, LEGACY_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, LEGACY_APP_NAME)
                logger.info("Старая запись автозагрузки убрана из реестра")
            except FileNotFoundError:
                pass
        return True
    except Exception as e:
        logger.error(f"Не удалось убрать старую запись из реестра: {e}")
        return False


def migrate_from_registry() -> bool:
    """
    Разовый переезд: старая запись в реестре → ярлык в автозагрузке.

    Вызывается при старте приложения. Без него у тех, у кого автозапуск
    уже был включён, он бы молча отвалился после обновления — человек узнал
    бы об этом, только пропустив смерть.

    Возвращает True, если переезд реально произошёл.
    """
    if sys.platform != "win32":
        return False
    if not _legacy_registry_exists():
        return False

    logger.info("Найдена старая автозагрузка через реестр — перевожу на ярлык")
    created = enable()
    if not created:
        # Ярлык не создался — реестр НЕ трогаем, иначе человек останется
        # вообще без автозапуска. Попробуем в следующий раз.
        logger.warning("Ярлык не создан, старая запись в реестре оставлена")
        return False
    _remove_legacy_registry()
    return True
