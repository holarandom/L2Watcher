# template_store.py
"""
Хранение и загрузка обученных шаблонов (main_death, essence_death, disconnect).
Шаблоны лежат в %APPDATA%/L2Monitor/templates/ — переживают обновления .exe.
"""
import os
import json
import cv2
import numpy as np
import logging
from datetime import datetime
from typing import Optional, Dict
from config_manager import get_templates_dir

logger = logging.getLogger(__name__)

TEMPLATE_FILES = {
    "main_death": "template_main_death.png",
    "essence_death": "template_essence_death.png",
    "main_disconnect": "template_main_disconnect.png",
    "essence_disconnect": "template_essence_disconnect.png",
}


def template_path(key: str) -> str:
    return os.path.join(get_templates_dir(), TEMPLATE_FILES[key])


def meta_path(key: str) -> str:
    """Путь к файлу с метаданными шаблона (рядом с png, тот же базовый имя)."""
    return os.path.splitext(template_path(key))[0] + ".json"


def save_template(key: str, template: np.ndarray, window_size=None) -> bool:
    """
    Сохраняет шаблон и, если известен размер окна на момент обучения,
    метаданные рядом.

    Зачем метаданные: шаблон обучается на конкретном разрешении окна. Если
    потом играть в окне поменьше, шаблон физически не влезет в кадр и детект
    молча перестанет работать. Записанный размер позволяет это заметить и
    честно сказать человеку "переобучи", вместо того чтобы молчать.
    """
    if key not in TEMPLATE_FILES:
        logger.error(f"Неизвестный ключ шаблона: {key}")
        return False
    path = template_path(key)
    try:
        cv2.imwrite(path, template)
        logger.info(f"Шаблон '{key}' сохранён: {path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения шаблона '{key}': {e}")
        return False

    try:
        meta = {
            "key": key,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "template_w": int(template.shape[1]),
            "template_h": int(template.shape[0]),
        }
        if window_size:
            meta["window_w"], meta["window_h"] = int(window_size[0]), int(window_size[1])
        with open(meta_path(key), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # Метаданные — приятный бонус, без них шаблон всё равно рабочий
        logger.debug(f"Метаданные шаблона '{key}' не сохранены: {e}")
    return True


def load_meta(key: str) -> Optional[dict]:
    """Метаданные шаблона или None (у шаблонов, обученных до 1.1.0, их нет)."""
    path = meta_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_all_templates() -> Dict[str, Optional[np.ndarray]]:
    """Загружает все доступные шаблоны. Отсутствующие — None."""
    result = {}
    for key in TEMPLATE_FILES:
        path = template_path(key)
        if os.path.exists(path):
            img = cv2.imread(path, cv2.IMREAD_COLOR)
            if img is not None:
                result[key] = img
                logger.info(f"Загружен шаблон '{key}': {img.shape}")
                continue
            else:
                logger.error(f"Шаблон '{key}' повреждён: {path}")
        result[key] = None
    return result


def has_template(key: str) -> bool:
    return os.path.exists(template_path(key))
