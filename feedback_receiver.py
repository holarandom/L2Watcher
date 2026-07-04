"""
Приёмник обратной связи L2 Watcher — скрипт ДЛЯ РАЗРАБОТЧИКА.

Запускается у разработчика (не у пользователей). Слушает фидбек-бота
(@L2WatcherFeedbackBot) и пересылает все входящие сообщения (текст, файлы
логов, скриншоты) в личку разработчику.

Настройка (один раз):
1. Создай рядом файл feedback_config.json:
   {
     "token": "ТОКЕН_ФИДБЕК_БОТА",
     "owner_chat_id": 123456789
   }
   owner_chat_id — твой личный chat id (узнать: @userinfobot).
   Файл в .gitignore — токен не попадёт в репозиторий.
2. Запусти: python feedback_receiver.py
   (можно добавить в автозагрузку рядом с вотчером)

Защита от спама:
- не более 5 сообщений в 10 минут с одного отправителя (лишнее молча
  игнорируется);
- блок-лист: добавь chat_id в "blocked" в feedback_config.json —
  сообщения от него не пересылаются.
"""
import asyncio
import json
import logging
import os
import time

from aiogram import Bot, Dispatcher, types

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "feedback_config.json")

RATE_LIMIT_N = 5          # сообщений
RATE_LIMIT_WINDOW = 600   # за 10 минут
_history: dict = {}       # chat_id -> [timestamps]


def load_cfg() -> dict:
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def rate_ok(chat_id: int) -> bool:
    now = time.time()
    lst = [t for t in _history.get(chat_id, []) if now - t < RATE_LIMIT_WINDOW]
    if len(lst) >= RATE_LIMIT_N:
        _history[chat_id] = lst
        return False
    lst.append(now)
    _history[chat_id] = lst
    return True


async def main():
    cfg = load_cfg()
    bot = Bot(cfg["token"])
    dp = Dispatcher()
    owner = int(cfg["owner_chat_id"])
    blocked = set(cfg.get("blocked", []))

    @dp.message()
    async def on_message(message: types.Message):
        uid = message.chat.id
        if uid == owner:
            return  # свои сообщения не пересылаем сами себе
        if uid in blocked:
            return
        if not rate_ok(uid):
            return
        # Служебные команды (/start и т.п.) не пересылаем — это не фидбек
        if message.text and message.text.startswith("/"):
            if message.text.startswith("/start"):
                await message.answer(
                    "👋 Это бот обратной связи L2 Watcher.\n"
                    "Опиши проблему или идею одним сообщением — "
                    "можно приложить файл лога и скриншот."
                )
            return
        user = message.from_user
        header = (f"🐞 Фидбек от @{user.username or '—'} "
                  f"(id {uid}, {user.full_name})")
        try:
            await bot.send_message(owner, header)
            await message.forward(owner)
            await message.answer(
                "✅ Спасибо! Сообщение передано разработчику."
            )
        except Exception as e:
            logger.error(f"Пересылка не удалась: {e}")

    logger.info("Приёмник фидбека запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
