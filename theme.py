# theme.py
"""
Определение системной темы (тёмная/светлая) и применение её к окну
через sv-ttk. Окно настроек подстраивается под тему ОС: если у человека
в Windows выбран тёмный режим — окно тёмное, если светлый — светлое.
Никаких ручных переключателей: само под систему.
"""
import sys
import logging

logger = logging.getLogger(__name__)


def get_system_theme() -> str:
    """
    Возвращает 'dark' или 'light' по настройкам системы.
    На Windows читает реестр (AppsUseLightTheme): 0 = тёмная, 1 = светлая.
    Если прочитать не удалось или не Windows — безопасный фолбэк на 'dark'.
    """
    if sys.platform == "win32":
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                # AppsUseLightTheme: 1 — приложения в светлой теме, 0 — в тёмной
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if value == 1 else "dark"
        except Exception as e:
            logger.warning(f"Не удалось определить тему системы: {e}, использую тёмную")
            return "dark"
    return "dark"


def set_window_titlebar_dark(root, dark: bool = True):
    """
    Красит нативную рамку-заголовок окна Windows в тёмный/светлый цвет.
    sv-ttk красит содержимое окна, но НЕ трогает верхнюю полосу заголовка
    (её рисует сама Windows) — поэтому при тёмной теме заголовок остаётся
    белым. Этот вызов через DWM API исправляет это.

    Работает на Windows 10 1809+ и Windows 11. На старых системах или при
    ошибке тихо пропускается (заголовок просто останется светлым).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        root.update_idletasks()  # окно должно существовать, чтобы получить HWND
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        value = ctypes.c_int(1 if dark else 0)
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (на старых 1809 было 19) — пробуем оба
        for attr in (20, 19):
            res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
            if res == 0:  # S_OK
                break
        root.update_idletasks()
    except Exception as e:
        logger.warning(f"Не удалось покрасить рамку окна: {e}")


def apply_theme(root, theme: str = None):
    """
    Применяет тему к окну tkinter через sv-ttk.
    theme=None → берём из системы.
    Возвращает (theme, sv_ttk_applied):
      theme — 'dark' / 'light'
      sv_ttk_applied — True если sv-ttk реально применился (иначе окно
                       осталось на стандартном ttk и красить tk-виджеты
                       в тёмное НЕ нужно, чтобы не было бело-чёрного микса)
    """
    if theme is None:
        theme = get_system_theme()
    try:
        import sv_ttk
        sv_ttk.set_theme(theme)
        logger.info(f"Применена тема оформления: {theme}")
        # Красим и нативную рамку-заголовок Windows в тон теме —
        # иначе при тёмной теме верхняя полоса окна остаётся белой.
        set_window_titlebar_dark(root, dark=(theme == "dark"))
        return theme, True
    except ImportError:
        logger.info("sv-ttk не установлен — стандартное оформление ttk")
        return theme, False
    except Exception as e:
        logger.warning(f"Не удалось применить тему {theme}: {e}")
        return theme, False
