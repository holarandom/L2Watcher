# local_notify.py
"""
Локальные уведомления Windows: звук + всплывающее окошко у иконки трея.

Зачем: Telegram регулярно недоступен (в логах сотни ClientConnectorError к
api.telegram.org), а локальное уведомление не зависит ни от интернета, ни от
VPN, ни от блокировок. Если человек за компьютером — он узнает о смерти
мгновенно и бесплатно, даже когда всё остальное лежит.

Никаких новых библиотек: всплывашку рисует pystray (уже используется для
иконки трея), звук играет winsound из стандартной поставки Python.
"""
import logging

logger = logging.getLogger(__name__)

# Заголовки и звук под каждое событие. Смерть — самый тревожный звук
# (SystemHand), дисконнект и закрытие окна — послабее, чтобы на слух
# отличать одно от другого не глядя на экран.
EVENT_STYLE = {
    "death":         ("Персонаж погиб",  "SystemHand"),
    "disconnect":    ("Разрыв соединения", "SystemExclamation"),
    "window_closed": ("Окно игры закрылось", "SystemAsterisk"),
}


class LocalNotifier:
    """
    Показывает уведомление на самом компьютере.

    tray_provider — функция без аргументов, возвращающая объект TrayIcon
    (или None). Через функцию, а не напрямую, потому что трей поднимается
    позже приложения, и на момент создания нотификатора его ещё нет.
    """

    def __init__(self, tray_provider=None, sound: bool = True, popup: bool = True):
        self.tray_provider = tray_provider
        self.sound = sound
        self.popup = popup

    def notify(self, event_type: str, char_name: str, version: str):
        title, sound_name = EVENT_STYLE.get(
            event_type, ("L2 Watcher", "SystemAsterisk")
        )
        text = f"{char_name} ({version.capitalize()})"

        if self.sound:
            self._play(sound_name)
        if self.popup:
            self._popup(title, text)

    # ── звук ──────────────────────────────────────────────────────────
    def _play(self, sound_name: str):
        try:
            import winsound
            # SND_ASYNC — не ждём окончания звука, иначе тик мониторинга
            # подвисал бы на длительность wav-файла.
            winsound.PlaySound(
                sound_name,
                winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception as e:
            logger.debug(f"Не удалось проиграть звук уведомления: {e}")

    # ── всплывашка у трея ─────────────────────────────────────────────
    def _popup(self, title: str, text: str):
        try:
            tray = self.tray_provider() if self.tray_provider else None
            if tray is None:
                return
            tray.notify(text, title)
        except Exception as e:
            # Windows умеет глушить всплывашки (режим «Не беспокоить»,
            # фокусировка внимания) — это не ошибка приложения.
            logger.debug(f"Всплывающее уведомление не показано: {e}")
