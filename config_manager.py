# config_manager.py
"""
Управление конфигурацией приложения.
Конфиг хранится в %APPDATA%/L2Monitor/config.json — НЕ в папке программы.
Это значит: конфиг переживает обновления .exe, и его не утащишь
случайно если просто скопируешь папку с программой.
"""
import os
import json
import logging
import copy

logger = logging.getLogger(__name__)

APP_NAME = "L2Monitor"


def get_config_dir() -> str:
    """Возвращает путь к папке конфига в %APPDATA%, создаёт если нет."""
    appdata = os.getenv("APPDATA")
    if not appdata:
        # fallback на случай если переменной нет (не Windows)
        appdata = os.path.expanduser("~")
    config_dir = os.path.join(appdata, APP_NAME)
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_config_path() -> str:
    return os.path.join(get_config_dir(), "config.json")


def get_templates_dir() -> str:
    """Папка для шаблонов — тоже в APPDATA, чтобы самообученные
    шаблоны не потерялись при обновлении .exe."""
    templates_dir = os.path.join(get_config_dir(), "templates")
    os.makedirs(templates_dir, exist_ok=True)
    return templates_dir


def get_log_path() -> str:
    return os.path.join(get_config_dir(), "l2bot.log")


DEFAULT_CONFIG = {
    "token": "",
    "chat_id": "",
    "characters": [],          # [{"name": "конь", "version": "essence"}, ...]
    "window_check_interval": 5,
    "check_interval": 10,
    "match_threshold_death": 0.75,
    "match_threshold_disconnect": 0.78,  # ниже смерти специально: табличка
                                         # дисконнекта в Essence содержит
                                         # бегущий таймер (<7>→<6>...), из-за
                                         # которого score колеблется. Порог
                                         # 0.85 был слишком высоким — детект
                                         # срывался на смене цифры.
    "autostart_monitoring": False,   # запускать мониторинг сразу при старте,
                                     # не дожидаясь команды /start в Telegram
    "message_style": "card",         # стиль оформления сообщений бота:
                                     # minimal / card / strict / lineage
    "char_pick_format": "A",         # формат выбора персонажа при детекте окна:
                                     # "A" — скрин + версия, потом персонаж (2 шага)
                                     # "B" — скрин + сразу все персонажи (1 шаг)
    "new_window_capture_delay_sec": 5,  # пауза перед скрином нового окна —
                                        # чтобы клиент успел отрисоваться
    "update_notified_version": "",   # о какой новой версии уже уведомили
    "quiet_hours": {                 # тихий режим: в этом диапазоне НЕ сканируем
        "enabled": False,            # (ноль нагрузки на проц, кулеры молчат)
        "start": "02:00",            # начало (ЧЧ:ММ)
        "end": "10:00"               # конец (ЧЧ:ММ); если start>end — через полночь
    },
    "read_overlapped_windows": False  # читать перекрытые окна (PrintWindow):
                                      # вкл = можно мультибоксить с перекрытием
                                      # окон, но больше нагрузка на процессор.
                                      # Свёрнутые окна не читаются в любом случае.
}


def load_config() -> dict:
    """Загружает конфиг, создаёт дефолтный если файла нет."""
    path = get_config_path()
    if not os.path.exists(path):
        logger.info(f"Конфиг не найден, создаю дефолтный: {path}")
        default = copy.deepcopy(DEFAULT_CONFIG)
        save_config(default)
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Добавляем отсутствующие ключи верхнего уровня (на случай обновления структуры)
        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged.update(cfg)
        # quiet_hours — тоже вложенный словарь, нужен глубокий merge
        merged_qh = dict(DEFAULT_CONFIG["quiet_hours"])
        merged_qh.update(cfg.get("quiet_hours", {}))
        merged["quiet_hours"] = merged_qh
        return merged
    except Exception as e:
        logger.error(f"Ошибка чтения конфига: {e}. Использую дефолтный.")
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg: dict) -> bool:
    path = get_config_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        logger.info(f"Конфиг сохранён: {path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения конфига: {e}")
        return False


def is_configured(cfg: dict) -> bool:
    """Проверяет, заполнены ли минимально необходимые поля."""
    return bool(cfg.get("token")) and bool(cfg.get("chat_id")) and bool(cfg.get("characters"))


def is_quiet_now(cfg: dict) -> bool:
    """
    Возвращает True, если СЕЙЧАС попадает в тихий режим (по системному
    времени). В тихом режиме приложение не сканирует окна — ноль нагрузки
    на процессор. Корректно обрабатывает диапазон через полночь
    (например 23:00–08:00).
    """
    qh = cfg.get("quiet_hours", {})
    if not qh.get("enabled"):
        return False
    from datetime import datetime
    try:
        now = datetime.now().time()
        sh, sm = map(int, qh.get("start", "02:00").split(":"))
        eh, em = map(int, qh.get("end", "10:00").split(":"))
        start_min = sh * 60 + sm
        end_min = eh * 60 + em
        now_min = now.hour * 60 + now.minute
        if start_min == end_min:
            return False  # пустой диапазон
        if start_min < end_min:
            # обычный диапазон в пределах суток (например 02:00–10:00)
            return start_min <= now_min < end_min
        else:
            # через полночь (например 23:00–08:00)
            return now_min >= start_min or now_min < end_min
    except Exception:
        return False
