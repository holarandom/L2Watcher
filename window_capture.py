# window_capture.py
"""
Захват окон Lineage II через Windows API.
Работает даже со свёрнутыми окнами (в отличие от обычного скриншота экрана).
"""
import win32gui
import win32ui
import win32con
import cv2
import numpy as np
import logging
from PIL import Image
from typing import Optional, List

logger = logging.getLogger(__name__)

WINDOW_TITLE = "Lineage II"

# Режим захвата: False = BitBlt (лёгкий, основной), True = PrintWindow
# (читает перекрытые окна, но тяжелее). Выставляется приложением из
# конфига при старте и при изменении настроек.
USE_PRINTWINDOW = False


def get_l2_hwnds() -> List[int]:
    """Находит все открытые окна с заголовком 'Lineage II'."""
    result = []

    def callback(hwnd, lst):
        try:
            if win32gui.IsWindowVisible(hwnd):
                if win32gui.GetWindowText(hwnd) == WINDOW_TITLE:
                    lst.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, result)
    return result


def capture_window(hwnd: int, use_printwindow: bool = None) -> Optional[np.ndarray]:
    """
    Захватывает содержимое окна по hwnd.

    По умолчанию использует BitBlt — он лёгкий по нагрузке на процессор и
    отлично читает видимые (развёрнутые) окна. Это основной режим.

    use_printwindow=True включает PrintWindow с PW_RENDERFULLCONTENT —
    он умеет читать ПЕРЕКРЫТЫЕ окна (когда окно развёрнуто, но закрыто
    сверху другим окном). Цена — больше нагрузки на процессор, и для
    свёрнутых окон DirectX-игр (как L2) он всё равно не работает (игра не
    рендерит свёрнутый клиент). Поэтому по умолчанию выключен; включается
    в настройках теми, кому нужен мультибокс с перекрытием окон.

    use_printwindow=None → берётся глобальная настройка USE_PRINTWINDOW
    (её выставляет приложение из конфига).

    Возвращает кадр BGR (для OpenCV) или None при ошибке.
    """
    if use_printwindow is None:
        use_printwindow = USE_PRINTWINDOW
    try:
        if not win32gui.IsWindow(hwnd):
            return None

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
            w, h = cr - cl, cb - ct
        if w <= 0 or h <= 0:
            return None

        if use_printwindow:
            # Режим "читать перекрытые окна": сначала PrintWindow, при
            # неудаче (чёрный кадр) — откат на BitBlt.
            frame = _capture_printwindow(hwnd, w, h)
            if frame is not None and not is_window_blank(frame):
                return frame
            return _capture_bitblt(hwnd, w, h)

        # Обычный режим — только BitBlt (лёгкий, основной)
        return _capture_bitblt(hwnd, w, h)
    except Exception as e:
        logger.error(f"Ошибка захвата окна 0x{hwnd:X}: {e}")
        return None


def _capture_printwindow(hwnd: int, w: int, h: int) -> Optional[np.ndarray]:
    """Захват через PrintWindow с PW_RENDERFULLCONTENT."""
    hwndDC = dcObj = cDC = bmp = None
    try:
        import ctypes
        hwndDC = win32gui.GetWindowDC(hwnd)
        dcObj = win32ui.CreateDCFromHandle(hwndDC)
        cDC = dcObj.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dcObj, w, h)
        cDC.SelectObject(bmp)

        # PW_RENDERFULLCONTENT = 0x00000002 — заставляет окно отрисовать
        # всё содержимое (нужно для окон со сложным рендерингом).
        result = ctypes.windll.user32.PrintWindow(hwnd, cDC.GetSafeHdc(), 2)

        frame = None
        if result == 1:
            info = bmp.GetInfo()
            data = bmp.GetBitmapBits(True)
            pil_img = Image.frombuffer(
                "RGB", (info["bmWidth"], info["bmHeight"]),
                data, "raw", "BGRX", 0, 1
            )
            frame = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return frame
    except Exception as e:
        logger.debug(f"PrintWindow не сработал для 0x{hwnd:X}: {e}")
        return None
    finally:
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            if cDC is not None:
                cDC.DeleteDC()
        except Exception:
            pass
        try:
            if dcObj is not None:
                dcObj.DeleteDC()
        except Exception:
            pass
        try:
            if hwndDC is not None:
                win32gui.ReleaseDC(hwnd, hwndDC)
        except Exception:
            pass


def _capture_bitblt(hwnd: int, w: int, h: int) -> Optional[np.ndarray]:
    """Запасной захват через BitBlt (как было раньше)."""
    wDC = dcObj = cDC = bmp = None
    try:
        wDC = win32gui.GetWindowDC(hwnd)
        dcObj = win32ui.CreateDCFromHandle(wDC)
        cDC = dcObj.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dcObj, w, h)
        cDC.SelectObject(bmp)
        cDC.BitBlt((0, 0), (w, h), dcObj, (0, 0), win32con.SRCCOPY)

        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        pil_img = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]),
            data, "raw", "BGRX", 0, 1
        )
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.error(f"BitBlt не сработал для 0x{hwnd:X}: {e}")
        return None
    finally:
        # Освобождаем GDI-ресурсы ВСЕГДА, даже если выше упало исключение.
        # Иначе при каждой неудаче утекают DC/bitmap-хендлы, и со временем
        # система исчерпывает GDI-объекты (Windows лимит ~10000 на процесс).
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            if cDC is not None:
                cDC.DeleteDC()
        except Exception:
            pass
        try:
            if dcObj is not None:
                dcObj.DeleteDC()
        except Exception:
            pass
        try:
            if wDC is not None:
                win32gui.ReleaseDC(hwnd, wDC)
        except Exception:
            pass


def is_window_blank(frame: np.ndarray, threshold: float = 2.0) -> bool:
    """
    Проверяет, не является ли кадр полностью чёрным/пустым —
    бывает если окно свёрнуто на некоторых системах или ещё не отрендерилось.
    """
    if frame is None:
        return True
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(np.std(gray)) < threshold
