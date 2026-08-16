# notify_outbox.py
"""
Очередь недоставленных уведомлений ("исходящие").

Зачем: раньше уведомление о смерти пробовало отправиться 3 раза за ~9 секунд
и, если Telegram был недоступен (а он недоступен регулярно — в логах сотни
ClientConnectorError к api.telegram.org), событие ТЕРЯЛОСЬ НАВСЕГДА. Человек
думал, что всё живо, а персонаж лежал. Для программы, вся задача которой —
не пропустить смерть, это самый тяжёлый дефект.

Теперь недоставленное складывается сюда, переживает перезапуск приложения и
перезагрузку компьютера, и досылается, как только связь вернётся. К тексту
добавляется пометка, что событие произошло раньше.

Диск бережём: файл пишется ТОЛЬКО когда очередь реально меняется (событие
добавили или дослали). Пока очередь пуста — обращений к диску нет вообще,
фоновый цикл просто смотрит на пустой список в памяти.
"""
import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Больше этого числа не храним: если Telegram лежит сутками, смысла в
# трёхдневной пачке "ты умер" нет, а файл рос бы бесконечно.
MAX_ITEMS = 200

# Событие старше этого срока не досылаем — оно уже неактуально.
MAX_AGE_HOURS = 12


def _path() -> str:
    from config_manager import get_config_dir
    return os.path.join(get_config_dir(), "outbox.json")


class Outbox:
    """Очередь недоставленных событий. Живёт в памяти, зеркалится в файл."""

    def __init__(self):
        self._items = []
        self._load()

    # ── диск ──────────────────────────────────────────────────────────
    def _load(self):
        path = _path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._items = json.load(f)
            if self._items:
                logger.info(f"В очереди недоставленных: {len(self._items)} событий")
        except Exception as e:
            logger.error(f"Не удалось прочитать очередь недоставленных: {e}")
            self._items = []

    def _save(self):
        """Пишется только при изменении очереди — не по таймеру."""
        path = _path()
        try:
            if not self._items:
                # Очередь опустела — файл не нужен, убираем совсем,
                # чтобы не оставлять мусор в папке настроек.
                if os.path.exists(path):
                    os.remove(path)
                return
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Не удалось сохранить очередь недоставленных: {e}")

    # ── работа с очередью ─────────────────────────────────────────────
    def add(self, event_type: str, char_name: str, version: str, hwnd=None):
        self._items.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event_type": event_type,
            "char_name": char_name,
            "version": version,
            "hwnd": hwnd,
        })
        # Переполнение — выкидываем самые старые, свежие важнее.
        if len(self._items) > MAX_ITEMS:
            self._items = self._items[-MAX_ITEMS:]
        self._save()
        logger.warning(
            f"Событие [{event_type}] {char_name} отложено в очередь "
            f"(в очереди {len(self._items)}) — дошлю, когда вернётся связь"
        )

    def is_empty(self) -> bool:
        return not self._items

    def count(self) -> int:
        """Сколько событий ждёт отправки (для /health)."""
        return len(self._items)

    def _drop_stale(self) -> int:
        """Убирает протухшие события. Возвращает сколько выкинул."""
        now = datetime.now()
        keep = []
        dropped = 0
        for it in self._items:
            try:
                age = (now - datetime.fromisoformat(it["ts"])).total_seconds() / 3600
            except Exception:
                age = 0
            if age > MAX_AGE_HOURS:
                dropped += 1
            else:
                keep.append(it)
        if dropped:
            self._items = keep
            logger.info(f"Из очереди убрано {dropped} устаревших событий (>{MAX_AGE_HOURS} ч)")
        return dropped

    async def flush(self, send_fn) -> int:
        """
        Пробует дослать всё из очереди.
        send_fn(event_type, char_name, version, hwnd, time_str) -> bool
        Возвращает сколько успешно доставлено.
        """
        if not self._items:
            return 0

        changed = bool(self._drop_stale())
        sent = 0
        remaining = []
        for it in self._items:
            try:
                ts = datetime.fromisoformat(it["ts"]).strftime("%H:%M:%S")
            except Exception:
                ts = "?"
            ok = False
            try:
                ok = await send_fn(it["event_type"], it["char_name"],
                                   it["version"], it["hwnd"], ts)
            except Exception as e:
                logger.debug(f"Досылка не удалась: {e}")
            if ok:
                sent += 1
                changed = True
            else:
                remaining.append(it)
                # Связи по-прежнему нет — не долбим остальными, попробуем
                # всю пачку на следующем заходе.
                idx = self._items.index(it)
                remaining.extend(self._items[idx + 1:])
                break

        self._items = remaining
        if sent:
            logger.info(f"Досланы отложенные события: {sent}")
        if changed:
            self._save()
        return sent
