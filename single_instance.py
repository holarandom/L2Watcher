# single_instance.py
"""
Защита от запуска нескольких копий приложения одновременно.

Зачем: если запущены две копии, обе пытаются поллить одного Telegram-бота,
и Telegram отдаёт ошибку Conflict (terminated by other getUpdates) — бот
начинает сбоить, уведомления теряются. Особенно легко поймать это при
автозагрузке + ручном запуске, или при двойном клике по .exe.

Механизм: именованный мьютекс Windows. Первая копия создаёт мьютекс;
вторая видит, что он уже существует (ERROR_ALREADY_EXISTS), и понимает,
что приложение уже запущено. На не-Windows используем файловую блокировку
как запасной вариант.
"""
import sys
import logging

logger = logging.getLogger(__name__)

MUTEX_NAME = "Global\\L2Monitor_SingleInstance_Mutex"


class SingleInstance:
    """
    Контекст single-instance. Использование:
        lock = SingleInstance()
        if lock.already_running():
            # другая копия уже работает — выходим
            sys.exit(0)
        # ... основная программа ...
    Мьютекс держится живым, пока жив объект (и процесс).
    """

    def __init__(self):
        self._mutex = None
        self._lockfile = None
        self._is_running = self._acquire()

    def _acquire(self) -> bool:
        """Возвращает True, если другая копия УЖЕ запущена."""
        if sys.platform == "win32":
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_windows(self) -> bool:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # CreateMutexW(NULL, FALSE, name)
            self._mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            last_error = kernel32.GetLastError()
            ERROR_ALREADY_EXISTS = 183
            if last_error == ERROR_ALREADY_EXISTS:
                logger.warning("Обнаружена уже запущенная копия приложения")
                return True
            return False
        except Exception as e:
            # Если механизм не сработал — лучше разрешить запуск, чем
            # заблокировать единственную копию. Не критично.
            logger.error(f"Ошибка single-instance (Windows): {e}")
            return False

    def _acquire_posix(self) -> bool:
        import os
        import tempfile
        lock_path = os.path.join(tempfile.gettempdir(), "l2monitor.lock")
        try:
            import fcntl
            self._lockfile = open(lock_path, "w")
            try:
                fcntl.lockf(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except OSError:
                return True
        except Exception as e:
            logger.error(f"Ошибка single-instance (POSIX): {e}")
            return False

    def already_running(self) -> bool:
        return self._is_running

    def release(self):
        """Освобождает мьютекс/файл. Обычно не нужно — ОС освободит сама
        при завершении процесса, но можно вызвать явно."""
        if self._mutex is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.ReleaseMutex(self._mutex)
                ctypes.windll.kernel32.CloseHandle(self._mutex)
            except Exception:
                pass
            self._mutex = None
        if self._lockfile is not None:
            try:
                self._lockfile.close()
            except Exception:
                pass
            self._lockfile = None
