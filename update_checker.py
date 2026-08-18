# update_checker.py
"""
Проверка обновлений через GitHub Releases.

Раз в сутки (и при старте) спрашивает у GitHub последний релиз репозитория
и сравнивает с текущей версией. Если вышла новее — шлёт ОДНО уведомление
в Telegram со ссылкой на скачивание. Про одну и ту же версию повторно
не напоминает (помечает в конфиге).

Сеть недоступна / GitHub лёг — молча пропускаем, попробуем в следующий раз.
"""
import asyncio
import json
import logging
import re
import urllib.request

from version import APP_VERSION

logger = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/holarandom/L2Watcher/releases/latest"
RELEASES_PAGE = "https://github.com/holarandom/L2Watcher/releases/latest"
CHECK_INTERVAL_SEC = 24 * 3600  # раз в сутки


def _fetch_latest_version() -> str | None:
    """Возвращает тег последнего релиза (например '1.0.1') или None."""
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"User-Agent": "L2Watcher"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = str(data.get("tag_name", "")).strip()
        return tag.lstrip("vV") or None
    except Exception as e:
        logger.debug(f"Проверка обновлений не удалась: {e}")
        return None


def _newer(latest: str, current: str) -> bool:
    """True если latest > current (сравнение по числам SemVer)."""
    def parts(v):
        # Берём только ведущие числа: "1.0.4-beta" -> [1, 0, 4].
        # Раньше любой нечисловой суффикс ронял разбор в [0], и тег вроде
        # "1.0.4-beta" считался СТАРШЕ ничего — уведомление об обновлении
        # не приходило вообще.
        nums = re.findall(r"\d+", str(v).split("-")[0].split("+")[0])
        return [int(x) for x in nums] or [0]
    a, b = parts(latest), parts(current)
    # выравниваем длину
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return a > b


async def update_check_loop(app):
    """
    Фоновый цикл: проверка при старте и далее раз в сутки.
    app — экземпляр приложения (нужны app.cfg, app.tg, сохранение конфига).
    """
    await asyncio.sleep(20)  # даём приложению спокойно подняться
    while True:
        try:
            latest = await asyncio.to_thread(_fetch_latest_version)
            if latest and _newer(latest, APP_VERSION):
                # Трею сообщаем ВСЕГДА, на каждом заходе: пункт меню
                # «Скачать версию X» должен быть на месте и после
                # перезапуска приложения, а не только один раз.
                # Это единственный способ узнать об обновлении, когда
                # Telegram недоступен.
                try:
                    if getattr(app, "tray", None) is not None:
                        app.tray.set_update_available(latest)
                except Exception as e:
                    logger.debug(f"Трей не уведомлён об обновлении: {e}")

                notified = app.cfg.get("update_notified_version", "")
                if notified != latest:
                    app.cfg["update_notified_version"] = latest
                    try:
                        from config_manager import save_config
                        save_config(app.cfg)
                    except Exception:
                        pass
                    await app.tg.send(
                        f"🆕 Вышла новая версия L2 Watcher: {latest} "
                        f"(у тебя {APP_VERSION}).\n"
                        f"Скачать: {RELEASES_PAGE}"
                    )
                    logger.info(f"Доступно обновление: {latest}")
        except Exception as e:
            logger.debug(f"Цикл проверки обновлений: {e}")
        await asyncio.sleep(CHECK_INTERVAL_SEC)


def update_status_line() -> str | None:
    """Строка для /health: есть ли обновление (синхронно, быстрая проверка кэша не делается — просто отсутствует, чтобы health не тормозил)."""
    return None
