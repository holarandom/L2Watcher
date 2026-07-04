# template_store.py
"""
Хранение и загрузка обученных шаблонов (main_death, essence_death, disconnect).
Шаблоны лежат в %APPDATA%/L2Monitor/templates/ — переживают обновления .exe.
"""
import os
import cv2
import numpy as np
import logging
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


def save_template(key: str, template: np.ndarray) -> bool:
    if key not in TEMPLATE_FILES:
        logger.error(f"Неизвестный ключ шаблона: {key}")
        return False
    path = template_path(key)
    try:
        cv2.imwrite(path, template)
        logger.info(f"Шаблон '{key}' сохранён: {path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения шаблона '{key}': {e}")
        return False


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
