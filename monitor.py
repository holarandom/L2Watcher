# monitor.py
"""
Основная логика мониторинга окон игры.
Детект смерти/дисконнекта по шаблонам, уведомления в Telegram.
"""
import cv2
import numpy as np
import logging
from datetime import datetime
from typing import Optional, Dict, List, Callable, Awaitable

from window_capture import get_l2_hwnds, capture_window
import win32gui

logger = logging.getLogger(__name__)


class GameWindow:
    """Состояние одного отслеживаемого окна игры."""

    def __init__(self, hwnd: int, char_name: str, version: str):
        self.hwnd = hwnd
        self.char_name = char_name
        self.version = version  # "main" или "essence"

        self.death_notified = False
        self.disconnect_notified = False

        # Реальное состояние на последнем тике — для /status. В отличие от
        # *_notified флагов (которые завязаны на combined-логику "одно
        # уведомление на проблему" и молча сбрасываются), это поле всегда
        # отражает что РЕАЛЬНО видно на экране сейчас: "alive"/"dead"/
        # "disconnect". Раньше /status читал notified-флаги и мог показать
        # "Активен" для реально мёртвого окна.
        self.last_state = "alive"

    @property
    def label(self) -> str:
        return f"{self.char_name} ({self.version.capitalize()})"

    def capture(self) -> Optional[np.ndarray]:
        # ВАЖНО: НЕ отбрасываем кадр по is_window_blank. Эта проверка ложно
        # срабатывала на однотонных экранах (например, белые снежные локации
        # дают низкий разброс яркости), и тогда capture возвращал None, а
        # check() выходил раньше, чем доходил до проверки дисконнекта/смерти.
        # Из-за этого в светлых локациях детект вообще не работал. Для
        # сравнения с шаблоном годится любой реальный кадр — пусть решает
        # find_template по score. Blank-проверка осталась только для
        # скриншотов в Telegram (там она к месту — чтобы не слать черноту).
        return capture_window(self.hwnd)

    def find_template(
        self, frame: np.ndarray, template: np.ndarray, threshold: float, name: str = ""
    ) -> float:
        """Возвращает реальный score совпадения (для логов и решений)."""
        try:
            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            # Обычные тики (персонаж жив, score низкий) — на DEBUG, чтобы не
            # забивать лог. Но "ближний промах" (score близок к порогу, но не
            # дотянул) пишем на INFO — это помогает диагностировать ПРОПУЩЕННЫЕ
            # события: например, дисконнект был на экране, но шаблон чуть не
            # дотянул до порога, и уведомление не ушло. Без этого такие случаи
            # неотличимы от "ничего не было".
            if threshold - 0.12 <= max_val < threshold:
                logger.info(
                    f"[{self.label}] {name}: БЛИЗКИЙ ПРОМАХ совпадение "
                    f"{max_val:.3f} (порог {threshold}) — событие почти "
                    f"задетектилось, но не дотянуло"
                )
            else:
                logger.debug(f"[{self.label}] {name}: совпадение {max_val:.3f} (порог {threshold})")
            return float(max_val)
        except Exception as e:
            logger.error(f"Ошибка template matching [{self.label}] {name}: {e}")
            return 0.0

    async def check(
        self,
        templates: Dict[str, Optional[np.ndarray]],
        thresholds: Dict[str, float],
        notify_fn: Callable[..., Awaitable[None]],
    ):
        """
        Главная проверка состояния окна за один тик.
        notify_fn(event_text, label) — асинхронная функция отправки уведомления.

        Смерть и дисконнект считаются одной "проблемной ситуацией" —
        если уже отправили уведомление об одном из них, второе не шлём
        (например: умер → потом дисконнект пока лежит мёртвый — это не
        новая информация, человек и так уже знает что нужно подойти).
        Уведомление сбрасывается молча, без отдельного сообщения о том,
        что всё разрешилось — это только что бы открыть путь следующему
        уведомлению, если ситуация повторится позже.
        """
        # Закрытие окна обрабатывается НЕ здесь, а в главном цикле через
        # registry.get_closed() — он шлёт одно уведомление и убирает окно
        # из реестра. Если бы проверка закрытия была и тут, закрытое окно
        # слало бы уведомление на каждом тике, пока цикл его не уберёт.
        if not win32gui.IsWindow(self.hwnd):
            return

        frame = self.capture()
        if frame is None:
            return

        already_notified = self.death_notified or self.disconnect_notified

        # ── Дисконнект ──
        # Шаблон выбирается по версии окна (main/essence) — таблички разрыва
        # соединения в этих версиях визуально разные, поэтому общий шаблон
        # матчился ненадёжно (на Essence мог не сработать).
        dc_key = f"{self.version}_disconnect"
        tmpl_dc = templates.get(dc_key)
        disconnect_active = False
        if tmpl_dc is not None:
            score = self.find_template(frame, tmpl_dc, thresholds["disconnect"], dc_key)
            disconnect_active = score >= thresholds["disconnect"]
            if disconnect_active:
                self.last_state = "disconnect"
                if not already_notified:
                    self.disconnect_notified = True
                    await notify_fn("disconnect", self.char_name, self.version, self.hwnd)
                return
            # ВАЖНО: НЕ сбрасываем disconnect_notified здесь. Раньше тут был
            # сброс флага на каждый тик, когда табличка не распозналась — но
            # табличка дисконнекта может на миг "мигнуть"/не совпасть, и тогда
            # флаг сбрасывался, а на следующем тике слалось НОВОЕ уведомление.
            # Отсюда был спам раз в минуту. Флаг сбрасываем только ниже —
            # когда персонаж реально снова ЖИВ (не дисконнект и не смерть).

        # ── Смерть ──
        tmpl_key = f"{self.version}_death"
        tmpl_death = templates.get(tmpl_key)
        if tmpl_death is not None:
            score = self.find_template(frame, tmpl_death, thresholds["death"], tmpl_key)
            if score >= thresholds["death"]:
                self.last_state = "dead"
                if not already_notified:
                    self.death_notified = True
                    await notify_fn("death", self.char_name, self.version, self.hwnd)
                return

        # Сюда дошли — значит ни дисконнекта, ни смерти на экране нет.
        # Персонаж в игре: сбрасываем ОБА флага, открывая путь следующим
        # уведомлениям, если ситуация повторится позже.
        self.last_state = "alive"
        self.death_notified = False
        self.disconnect_notified = False


class WindowRegistry:
    """Реестр всех отслеживаемых окон + обнаружение новых/закрытых."""

    def __init__(self, window_check_interval: int = 30):
        self.windows: Dict[int, GameWindow] = {}
        self.window_check_interval = window_check_interval
        self._last_check = datetime.now()
        self._pending: set = set()  # hwnd уже отправлены на регистрацию, ждём ответа

    def due_for_check(self) -> bool:
        return (datetime.now() - self._last_check).total_seconds() >= self.window_check_interval

    def refresh(self) -> List[int]:
        """Возвращает список НОВЫХ hwnd, обнаруженных с последней проверки.
        Окна, для которых регистрация уже запущена (ждём ответ в Telegram),
        повторно не возвращаются — иначе плодились бы дублирующие запросы."""
        self._last_check = datetime.now()
        current = get_l2_hwnds()
        new_hwnds = [h for h in current if h not in self.windows and h not in self._pending]
        for h in new_hwnds:
            self._pending.add(h)
        return new_hwnds

    def get_closed(self) -> List[int]:
        """Возвращает hwnd окон из реестра, которые больше не существуют."""
        current = set(get_l2_hwnds())
        return [h for h in self.windows if h not in current]

    def get_closed_pending(self) -> List[int]:
        """
        Возвращает hwnd окон, которые ждали регистрации (_pending), но
        закрылись, не дождавшись выбора персонажа. Их нужно убрать из
        _pending и отменить связанные кнопки/ожидание в Telegram — иначе
        кнопки выбора висят в чате вечно, ссылаясь на несуществующее окно.
        """
        current = set(get_l2_hwnds())
        closed = [h for h in self._pending if h not in current]
        for h in closed:
            self._pending.discard(h)
        return closed

    def add(self, hwnd: int, char_name: str, version: str):
        self.windows[hwnd] = GameWindow(hwnd, char_name, version)
        self._pending.discard(hwnd)


    def discard_pending(self, hwnd: int):
        """Убирает одно окно из списка ожидающих (для переназначения одного
        окна без сброса остальных)."""
        self._pending.discard(hwnd)


    def all(self) -> List[GameWindow]:
        return list(self.windows.values())
