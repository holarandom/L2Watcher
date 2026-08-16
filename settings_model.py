# settings_model.py
"""
Логика настроек БЕЗ интерфейса: проверка введённого и сборка конфига.

Зачем отдельным файлом: здесь только чистые функции «словарь на входе —
словарь на выходе», без tkinter. Их можно запускать автотестами, не открывая
ни одного окна. Раньше эта логика жила внутри метода _save() окна настроек, и
проверить «что будет, если ввести время 25:99» можно было только руками.

Правило: в этом файле НЕТ ни одного import tkinter.
"""


def validate_time(value: str, default: str) -> str:
    """
    Приводит время к виду ЧЧ:ММ. Кривой ввод молча заменяется дефолтом —
    настройки не должны падать из-за опечатки в поле времени.

    "7:5" -> "07:05", "25:99" -> default, "" -> default
    """
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except Exception:
        pass
    return default


def validate_form(token: str, chat_id: str, characters: list):
    """
    Проверяет, что заполнено минимально необходимое.
    Возвращает (ok, заголовок_ошибки, текст_ошибки). Тексты здесь, а не в
    GUI, чтобы формулировки проверялись тестами вместе с логикой.
    """
    if not str(token).strip() or not str(chat_id).strip():
        return False, "Не всё заполнено", "Заполни токен и Chat ID"
    if not characters:
        return False, "Не всё заполнено", "Добавь хотя бы одного персонажа"
    return True, None, None


def build_config(base_cfg: dict, form: dict) -> dict:
    """
    Собирает новый конфиг из старого и того, что ввели в окне.

    base_cfg не меняется — возвращается новый словарь. Так окно настроек
    физически не может испортить конфиг, если сохранение сорвётся на
    полпути.

    form ожидает ключи: token, chat_id, characters, autostart_monitoring,
    message_style, char_pick_format, read_overlapped_windows, local_sound,
    local_popup, quiet_enabled, quiet_start, quiet_end.
    """
    cfg = dict(base_cfg)

    cfg["token"] = str(form.get("token", "")).strip()
    cfg["chat_id"] = str(form.get("chat_id", "")).strip()
    cfg["characters"] = list(form.get("characters", []))
    cfg["autostart_monitoring"] = bool(form.get("autostart_monitoring", False))
    cfg["message_style"] = form.get("message_style", cfg.get("message_style", "card"))
    cfg["char_pick_format"] = form.get("char_pick_format", cfg.get("char_pick_format", "A"))
    cfg["read_overlapped_windows"] = bool(form.get("read_overlapped_windows", False))
    cfg["local_sound"] = bool(form.get("local_sound", True))
    cfg["local_popup"] = bool(form.get("local_popup", True))

    cfg["quiet_hours"] = {
        "enabled": bool(form.get("quiet_enabled", False)),
        "start": validate_time(form.get("quiet_start", ""), "02:00"),
        "end": validate_time(form.get("quiet_end", ""), "10:00"),
    }
    return cfg
