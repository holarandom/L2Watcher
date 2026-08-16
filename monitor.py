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

# Зазор ниже порога, в котором совпадение считается "БЛИЗКИЙ ПРОМАХ"
# и логируется на INFO (диагностика пропущенных событий).
NEAR_MISS_MARGIN = 0.12

# Зазор ниже порога, в котором тик НЕ считается "события точно нет".
# Шире, чем NEAR_MISS_MARGIN, специально: шаблон мёртвого персонажа
# в логах проваливался до 0.631 при пороге 0.75, оставаясь при этом
# на экране. Всё, что выше (порог - RESET_MARGIN), считаем "событие
# возможно всё ещё на экране" и замок не открываем.
RESET_MARGIN = 0.20

# Сколько подряд ЯВНО чистых тиков нужно, чтобы открыть замок
# (~30 сек при тике в 12 сек).
CLEAR_TICKS_REQUIRED = 3

# Сколько СЕКУНД подряд событие должно ЯВНО отсутствовать, чтобы замок
# открылся. Считаем в секундах, а НЕ в тиках: check_interval меняется
# пользователем в настройках, и на маленьком интервале (1-2 сек) счётчик
# из 3 тиков открывал замок за 3 секунды — возвращался спам, который
# чинили в 1.0.3. В секундах поведение одинаковое при любом интервале.
CLEAR_SECONDS_REQUIRED = 30


class GameWindow:
    """Состояние одного отслеживаемого окна игры."""

    def __init__(self, hwnd: int, char_name: str, version: str):
        self.hwnd = hwnd
        self.char_name = char_name
        self.version = version  # "main" или "essence"

        self.death_notified = False
        self.disconnect_notified = False
        # Момент начала непрерывной "чистоты" ОТДЕЛЬНО для каждого события
        # (None = сейчас не чисто). Замки независимы: раньше был один общий
        # счётчик, из-за чего замки смерти и дисконнекта открывались вместе,
        # хотя события разные. Храним время, а не число тиков — см.
        # CLEAR_SECONDS_REQUIRED.
        self._clear_since_death = None
        self._clear_since_disconnect = None

        # Размер окна на последнем удачном тике — для /health и диагностики
        # "почему не ловится" (окно меньше шаблона).
        self.last_frame_size = None

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
            # КРИТИЧНО: если кадр МЕНЬШЕ шаблона, cv2.matchTemplate НЕ падает,
            # а молча меняет картинки местами и ищет кадр внутри шаблона —
            # и почти всегда возвращает 1.000. Это давало ложные "смерть" и
            # "дисконнект", после чего замок закрывался навсегда и настоящее
            # событие уже не приходило. В логах ловилось как "совпадение 1.000"
            # десятками тиков подряд. Ловится когда окно свёрнуто (крошечный
            # WindowRect), разворачивается, или шаблон обучен на большем
            # разрешении, чем текущее окно.
            th, tw = template.shape[:2]
            fh, fw = frame.shape[:2]
            if th > fh or tw > fw:
                logger.warning(
                    f"[{self.label}] {name}: окно {fw}x{fh} МЕНЬШЕ шаблона "
                    f"{tw}x{th} — тик пропущен (окно свёрнуто или шаблон "
                    f"обучен на большем разрешении; переобучи через /retrain)"
                )
                return 0.0

            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            # Обычные тики (персонаж жив, score низкий) — на DEBUG, чтобы не
            # забивать лог. Но "ближний промах" (score близок к порогу, но не
            # дотянул) пишем на INFO — это помогает диагностировать ПРОПУЩЕННЫЕ
            # события: например, дисконнект был на экране, но шаблон чуть не
            # дотянул до порога, и уведомление не ушло. Без этого такие случаи
            # неотличимы от "ничего не было".
            if threshold - NEAR_MISS_MARGIN <= max_val < threshold:
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

    def analyze(
        self,
        templates: Dict[str, Optional[np.ndarray]],
        thresholds: Dict[str, float],
    ) -> Optional[dict]:
        """
        ТЯЖЁЛАЯ часть тика: захват окна + сравнение с шаблонами.
        Синхронная и вынесена отдельно специально — её вызывают через
        asyncio.to_thread, чтобы cv2.matchTemplate не блокировал event loop.
        Раньше сравнение крутилось прямо в цикле, и на нескольких окнах
        Telegram-бот заметно тормозил с ответами.

        Возвращает словарь флагов или None, если окна/кадра нет.
        """
        if not win32gui.IsWindow(self.hwnd):
            return None

        frame = self.capture()
        if frame is None:
            return None

        self.last_frame_size = (frame.shape[1], frame.shape[0])  # (ширина, высота)

        # ── Дисконнект ──
        # Шаблон выбирается по версии окна (main/essence) — таблички разрыва
        # соединения в этих версиях визуально разные, поэтому общий шаблон
        # матчился ненадёжно (на Essence мог не сработать).
        dc_key = f"{self.version}_disconnect"
        tmpl_dc = templates.get(dc_key)
        dc_threshold = thresholds["disconnect"]
        disconnect_active = False
        disconnect_clean = True  # тик, на котором таблички ТОЧНО нет
        if tmpl_dc is not None:
            score = self.find_template(frame, tmpl_dc, dc_threshold, dc_key)
            disconnect_active = score >= dc_threshold
            disconnect_clean = score < dc_threshold - RESET_MARGIN

        # ── Смерть ──
        death_key = f"{self.version}_death"
        tmpl_death = templates.get(death_key)
        death_threshold = thresholds["death"]
        death_active = False
        death_clean = True
        if tmpl_death is not None:
            score = self.find_template(frame, tmpl_death, death_threshold, death_key)
            death_active = score >= death_threshold
            death_clean = score < death_threshold - RESET_MARGIN

        return {
            "death_active": death_active,
            "death_clean": death_clean,
            "disconnect_active": disconnect_active,
            "disconnect_clean": disconnect_clean,
        }

    def _lock_expired(self, since) -> bool:
        """True, если событие отсутствует непрерывно уже CLEAR_SECONDS_REQUIRED."""
        if since is None:
            return False
        return (datetime.now() - since).total_seconds() >= CLEAR_SECONDS_REQUIRED

    async def check(
        self,
        templates: Dict[str, Optional[np.ndarray]],
        thresholds: Dict[str, float],
        notify_fn: Callable[..., Awaitable[None]],
    ):
        """
        Главная проверка состояния окна за один тик.
        notify_fn(event_text, label) — асинхронная функция отправки уведомления.

        Смерть и дисконнект — НЕЗАВИСИМЫЕ события, у каждого свой "замок":
          • событие появилось на экране → один пинг, замок закрывается;
          • событие продолжает висеть на экране → молчим;
          • событие точно исчезло с экрана → замок открывается молча;
          • событие появилось снова позже → снова один пинг.
        Раньше замок был общий (already_notified): смерть глушила
        последующий дисконнект, хотя это ДРУГОЙ статус и о нём нужно знать.
        """
        # Закрытие окна обрабатывается НЕ здесь, а в главном цикле через
        # registry.get_closed() — он шлёт одно уведомление и убирает окно
        # из реестра. Если бы проверка закрытия была и тут, закрытое окно
        # слало бы уведомление на каждом тике, пока цикл его не уберёт.
        import asyncio
        res = await asyncio.to_thread(self.analyze, templates, thresholds)
        if res is None:
            return

        death_active = res["death_active"]
        death_clean = res["death_clean"]
        disconnect_active = res["disconnect_active"]
        disconnect_clean = res["disconnect_clean"]

        now = datetime.now()

        # ── Замок дисконнекта ──
        if disconnect_active:
            self._clear_since_disconnect = None
            if not self.disconnect_notified:
                self.disconnect_notified = True
                await notify_fn("disconnect", self.char_name, self.version, self.hwnd)
        elif disconnect_clean:
            # Таблички точно нет — засекаем непрерывную чистоту и открываем
            # замок, когда её накопилось CLEAR_SECONDS_REQUIRED секунд.
            if self._clear_since_disconnect is None:
                self._clear_since_disconnect = now
            if self._lock_expired(self._clear_since_disconnect):
                self.disconnect_notified = False
        else:
            # Серая зона: совпадение просело ниже порога, но табличка,
            # скорее всего, ВСЁ ЕЩЁ на экране (мерцание шаблона). Замок
            # не открываем и отсчёт чистоты сбрасываем — иначе следующий
            # всплеск выше порога уйдёт как новое уведомление (это был спам).
            self._clear_since_disconnect = None

        # ── Замок смерти ──
        if death_active:
            self._clear_since_death = None
            if not self.death_notified:
                self.death_notified = True
                await notify_fn("death", self.char_name, self.version, self.hwnd)
        elif disconnect_active:
            # Табличка дисконнекта висит ПОВЕРХ окна смерти и частично его
            # закрывает — совпадение шаблона смерти проваливается, хотя
            # персонаж всё ещё мёртв. Судить о смерти в этот момент нельзя:
            # замораживаем замок как есть. Иначе после закрытия таблички
            # дисконнекта окно смерти "появлялось" заново и слало лишний пинг.
            self._clear_since_death = None
        elif death_clean:
            if self._clear_since_death is None:
                self._clear_since_death = now
            if self._lock_expired(self._clear_since_death):
                self.death_notified = False
        else:
            self._clear_since_death = None

        # ── Реальное состояние для /status ──
        # Дисконнект приоритетнее: он перекрывает экран целиком.
        # В серой зоне состояние НЕ меняем — оставляем прежнее, чтобы
        # /status не показывал "Активен" для реально лежащего персонажа.
        if disconnect_active:
            self.last_state = "disconnect"
        elif death_active:
            self.last_state = "dead"
        elif disconnect_clean and death_clean:
            self.last_state = "alive"


class WindowRegistry:
    """Реестр всех отслеживаемых окон + обнаружение новых/закрытых."""

    def __init__(self, window_check_interval: int = 30):
        self.windows: Dict[int, GameWindow] = {}
        self.window_check_interval = window_check_interval
        self._last_check = datetime.now()
        self._pending: set = set()  # hwnd уже отправлены на регистрацию, ждём ответа

    def due_for_check(self) -> bool:
        return (datetime.now() - self._last_check).total_seconds() >= self.window_check_interval

    # ПРИМЕЧАНИЕ: три метода ниже принимают уже готовый список окон
    # (current). Раньше каждый сам звал get_l2_hwnds(), и за один тик
    # Windows перебирала ВСЕ окна системы трижды подряд. Теперь главный
    # цикл делает это один раз и передаёт результат сюда.

    def refresh(self, current: List[int] = None) -> List[int]:
        """Возвращает список НОВЫХ hwnd, обнаруженных с последней проверки.
        Окна, для которых регистрация уже запущена (ждём ответ в Telegram),
        повторно не возвращаются — иначе плодились бы дублирующие запросы."""
        self._last_check = datetime.now()
        if current is None:
            current = get_l2_hwnds()
        new_hwnds = [h for h in current if h not in self.windows and h not in self._pending]
        for h in new_hwnds:
            self._pending.add(h)
        return new_hwnds

    def get_closed(self, current: List[int] = None) -> List[int]:
        """Возвращает hwnd окон из реестра, которые больше не существуют."""
        cur = set(get_l2_hwnds() if current is None else current)
        return [h for h in self.windows if h not in cur]

    def get_closed_pending(self, current: List[int] = None) -> List[int]:
        """
        Возвращает hwnd окон, которые ждали регистрации (_pending), но
        закрылись, не дождавшись выбора персонажа. Их нужно убрать из
        _pending и отменить связанные кнопки/ожидание в Telegram — иначе
        кнопки выбора висят в чате вечно, ссылаясь на несуществующее окно.
        """
        cur = set(get_l2_hwnds() if current is None else current)
        closed = [h for h in self._pending if h not in cur]
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
