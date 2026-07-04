# telegram_bot.py
"""
Telegram-бот: уведомления, регистрация персонажей через кнопки,
команды управления мониторингом.
"""
import asyncio
import logging
from typing import Dict, List, Tuple
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, characters: List[dict], message_style: str = "card"):
        from aiogram.client.default import DefaultBotProperties
        # parse_mode=HTML глобально — стили используют <b>/<i> теги, которые
        # одинаково рендерятся на всех клиентах Telegram.
        self.bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        self.dp = Dispatcher()
        self.chat_id = chat_id
        self.characters = characters  # [{"name": ..., "version": ...}]
        self.message_style = message_style
        self.pending_registrations: Dict[int, asyncio.Future] = {}
        self.pending_messages: Dict[int, object] = {}  # hwnd -> сообщение с кнопками

        self._on_start_callback = None
        self._on_stop_callback = None
        self._status_provider = None
        self._is_running_provider = None  # узнаёт у main.py реальное состояние

        self._register_handlers()

    def _register_handlers(self):
        @self.dp.callback_query(lambda c: c.data == "noop")
        async def handle_noop(callback: types.CallbackQuery):
            # Кнопка-заголовок (разделитель Main/Essence) — просто гасим
            # "часики" на кнопке, ничего не делаем.
            await callback.answer()

        @self.dp.message(Command("start"))
        async def start_cmd(message: types.Message):
            already_running = self._is_running_provider() if self._is_running_provider else False
            if not already_running:
                if self._on_start_callback:
                    await self._on_start_callback()
                await message.answer("🟢 Мониторинг запущен")
            else:
                await message.answer("⚠️ Мониторинг уже запущен")

        @self.dp.message(Command("stop"))
        async def stop_cmd(message: types.Message):
            if self._on_stop_callback:
                await self._on_stop_callback()
            await message.answer("🔴 Мониторинг остановлен")

        @self.dp.message(Command("status"))
        async def status_cmd(message: types.Message):
            if self._status_provider:
                text = self._status_provider()
            else:
                text = "⚠️ Нет данных о статусе"
            await message.answer(text)

        @self.dp.callback_query(lambda c: c.data and c.data.startswith("ver:"))
        async def handle_version_pick(callback: types.CallbackQuery):
            # Выбрана версия — раскрываем список персонажей этой версии,
            # редактируя то же сообщение (не плодим новые).
            try:
                _, hwnd_str, version = callback.data.split(":", 2)
                hwnd = int(hwnd_str)
                marker = "🔵 Main" if version == "main" else "🟢 Essence"
                await self._edit_msg_text(
                    callback.message, f"{marker} — выбери персонажа:",
                    self._build_char_picker(hwnd, version)
                )
                await callback.answer()
            except Exception as e:
                logger.error(f"Ошибка выбора версии: {e}")
                await callback.answer("Ошибка")

        @self.dp.callback_query(lambda c: c.data and c.data.startswith("verback:"))
        async def handle_version_back(callback: types.CallbackQuery):
            # Назад к выбору версии
            try:
                hwnd = int(callback.data.split(":", 1)[1])
                await self._edit_msg_text(
                    callback.message, "Какой это персонаж?",
                    self._build_version_picker(hwnd)
                )
                await callback.answer()
            except Exception as e:
                logger.error(f"Ошибка возврата к версиям: {e}")
                await callback.answer("Ошибка")

        @self.dp.callback_query(lambda c: c.data and c.data.startswith("reg:"))
        async def handle_char_select(callback: types.CallbackQuery):
            try:
                _, hwnd_str, char_name, version = callback.data.split(":", 3)
                hwnd = int(hwnd_str)

                if hwnd in self.pending_registrations:
                    future = self.pending_registrations.pop(hwnd)
                    if not future.done():
                        future.set_result((char_name, version))
                self.pending_messages.pop(hwnd, None)

                await self._edit_msg_text(callback.message, f"✅ Записал: {char_name} ({version.capitalize()})")
                await callback.answer()
            except Exception as e:
                logger.error(f"Ошибка обработки выбора персонажа: {e}")
                await callback.answer("Ошибка")

    def on_start(self, callback):
        self._on_start_callback = callback

    def on_stop(self, callback):
        self._on_stop_callback = callback

    def set_status_provider(self, fn):
        self._status_provider = fn

    def set_is_running_provider(self, fn):
        self._is_running_provider = fn

    async def notify_event(self, event_type: str, char_name: str,
                           version: str, hwnd: int = None, time_str: str = ""):
        """
        Шлёт уведомление о событии, отформатированное по текущему стилю.
        event_type: "death" | "disconnect" | "window_closed".
        Под смертью/дисконнектом — кнопки Скрин и Статус (если есть hwnd).
        """
        import message_styles
        from datetime import datetime
        if not time_str:
            time_str = datetime.now().strftime("%H:%M:%S")

        msg = message_styles.format_event(
            self.message_style, event_type, char_name, version, time_str
        )

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        b = InlineKeyboardBuilder()
        if hwnd is not None:
            b.button(text="🖼 Скрин", callback_data=f"showwin:{hwnd}")
        b.button(text="📊 Статус", callback_data="menu:status")
        b.adjust(2)  # максимум 2 в ряд — влезает на узких телефонах
        reply_markup = b.as_markup()

        # Уведомление о событии важно (смерть/дисконнект) — пробуем несколько
        # раз с паузой, чтобы кратковременный обрыв сети (моргнул VPN) не
        # потерял событие.
        for attempt in range(3):
            try:
                await self.bot.send_message(self.chat_id, msg, reply_markup=reply_markup)
                logger.info(f"Уведомление [{event_type}]: {char_name} ({version})")
                return
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"Не отправилось уведомление (попытка {attempt+1}/3): {e}")
                    await asyncio.sleep(3)
                else:
                    logger.error(f"Уведомление [{event_type}] не доставлено после 3 попыток: {e}")

    async def send(self, text: str, reply_markup=None):
        try:
            return await self.bot.send_message(self.chat_id, text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")
            return None

    async def send_photo(self, image_bytes: bytes, caption: str = None, reply_markup=None):
        """Отправляет скриншот окна (опц. с кнопками под ним) — помогает
        понять, какой персонаж в каком окне."""
        try:
            from aiogram.types import BufferedInputFile
            photo = BufferedInputFile(image_bytes, filename="window.png")
            return await self.bot.send_photo(self.chat_id, photo, caption=caption,
                                             reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка отправки фото в Telegram: {e}")
            return None

    async def _edit_msg_text(self, message, text, reply_markup=None):
        """Редактирует сообщение: если это фото — меняем подпись (caption),
        иначе текст. Telegram не даёт edit_text для фото-сообщений."""
        try:
            if getattr(message, "photo", None):
                await message.edit_caption(caption=text, reply_markup=reply_markup)
            else:
                await message.edit_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось отредактировать сообщение: {e}")

    async def send_log_file(self, log_path: str, tail_lines: int = 0):
        """
        Отправляет файл лога в чат как документ. Полезно глянуть, что
        происходит, когда нет доступа к компьютеру.
        tail_lines > 0 — отправить только последние N строк (чтобы не слать
        многомегабайтный файл), 0 — файл целиком.
        """
        import os
        if not os.path.exists(log_path):
            await self.send("⚠️ Файл лога ещё не создан")
            return None
        try:
            from aiogram.types import BufferedInputFile
            if tail_lines > 0:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()[-tail_lines:]
                data = "".join(lines).encode("utf-8")
            else:
                with open(log_path, "rb") as f:
                    data = f.read()
            doc = BufferedInputFile(data, filename="l2bot.log")
            return await self.bot.send_document(
                self.chat_id, doc, caption="📄 Лог приложения"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки лога в Telegram: {e}")
            await self.send("⚠️ Не удалось отправить лог")
            return None

    async def ask_character_for_window(self, hwnd: int, timeout: int = 600, screenshot_bytes: bytes = None) -> Tuple[str, str]:
        """
        Показывает выбор персонажа для нового окна одним сообщением:
        скриншот окна + кнопки выбора прямо под ним (фото с reply_markup).
        Так скрин и выбор не разъезжаются, когда окон несколько.

        Формат выбора задаётся настройкой char_pick_format:
          "A" — скрин + кнопки версий, по клику раскрываются персонажи (2 шага)
          "B" — скрин + сразу все персонажи с маркерами версий (1 шаг)
        """
        fmt = getattr(self, "char_pick_format", "A")
        if fmt == "B":
            markup = self._build_flat_char_picker(hwnd)
        else:
            markup = self._build_version_picker(hwnd)

        caption = "🆕 Обнаружено новое окно игры.\nКакой это персонаж?"

        # Скрин + кнопки В ОДНОМ сообщении
        if screenshot_bytes:
            sent = await self.send_photo(screenshot_bytes, caption=caption,
                                         reply_markup=markup)
        else:
            sent = await self.send(caption, reply_markup=markup)
        self.pending_messages[hwnd] = sent

        future = asyncio.get_running_loop().create_future()
        self.pending_registrations[hwnd] = future
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            self.pending_registrations.pop(hwnd, None)
            self.pending_messages.pop(hwnd, None)
            return "Безымянное окно", "main"

    def _build_flat_char_picker(self, hwnd: int):
        """Формат Б: сразу все персонажи одним списком с маркерами версий."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        row = []
        for ch in self.characters:
            marker = "🔵" if ch["version"] == "main" else "🟢"
            row.append(InlineKeyboardButton(
                text=f"{marker} {ch['name']}",
                callback_data=f"reg:{hwnd}:{ch['name']}:{ch['version']}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _build_version_picker(self, hwnd: int):
        """Первый уровень: кнопки выбора версии. Показываем только те версии,
        для которых есть персонажи."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        has_main = any(c["version"] == "main" for c in self.characters)
        has_ess = any(c["version"] == "essence" for c in self.characters)
        row = []
        if has_main:
            row.append(InlineKeyboardButton(text="🔵 Main", callback_data=f"ver:{hwnd}:main"))
        if has_ess:
            row.append(InlineKeyboardButton(text="🟢 Essence", callback_data=f"ver:{hwnd}:essence"))
        rows.append(row)
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def _build_char_picker(self, hwnd: int, version: str):
        """Второй уровень: персонажи выбранной версии + кнопка 'назад'."""
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        marker = "🔵" if version == "main" else "🟢"
        chars_v = [c for c in self.characters if c["version"] == version]
        rows = []
        row = []
        for ch in chars_v:
            row.append(InlineKeyboardButton(
                text=f"{marker} {ch['name']}",
                callback_data=f"reg:{hwnd}:{ch['name']}:{ch['version']}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(text="⬅️ Назад к версиям", callback_data=f"verback:{hwnd}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def cancel_registration(self, hwnd: int):
        """
        Отменяет висящий запрос регистрации для закрытого окна:
        отменяет ожидающий future и убирает кнопки выбора из сообщения
        (заменяет текст на пометку, что окно закрылось). Вызывается, когда
        окно закрылось, не дождавшись выбора персонажа.
        """
        future = self.pending_registrations.pop(hwnd, None)
        if future and not future.done():
            future.cancel()
        msg = self.pending_messages.pop(hwnd, None)
        if msg is not None:
            await self._edit_msg_text(msg, "⚪ Окно закрылось, не дождавшись выбора персонажа", None)

    async def setup_bot_menu(self):
        """
        Регистрирует список команд в Telegram + кнопку «Меню» слева от поля
        ввода. Команды ставим со scope на конкретный чат пользователя —
        так они привязываются надёжнее, чем глобально (у друга глобальные
        команды иногда не подхватывались/появлялись с задержкой).
        """
        from aiogram.types import (
            BotCommand, BotCommandScopeChat, MenuButtonCommands
        )
        commands = [
            BotCommand(command="start", description="🟢 Запустить мониторинг"),
            BotCommand(command="stop", description="🔴 Остановить мониторинг"),
            BotCommand(command="status", description="📊 Статус персонажей"),
            BotCommand(command="health", description="🩺 Проверка настроек"),
            BotCommand(command="windows", description="🖼 Скрины окон"),
            BotCommand(command="redetect", description="🔄 Переназначить окна"),
            BotCommand(command="retrain", description="🔄 Переобучить шаблоны"),
            BotCommand(command="style", description="🎨 Стиль оформления"),
            BotCommand(command="menu", description="📋 Меню действий (кнопки)"),
            BotCommand(command="feedback", description="🐞 Сообщить о проблеме"),
            BotCommand(command="log", description="📄 Прислать лог файлом"),
        ]
        try:
            # Глобально (для всех) + явно для этого чата — для надёжности
            await self.bot.set_my_commands(commands)
            try:
                chat_id_int = int(self.chat_id)
                await self.bot.set_my_commands(
                    commands, scope=BotCommandScopeChat(chat_id=chat_id_int)
                )
                # Кнопка "Меню" слева от поля ввода
                await self.bot.set_chat_menu_button(
                    chat_id=chat_id_int, menu_button=MenuButtonCommands()
                )
            except (ValueError, TypeError):
                # chat_id не число (редко) — глобальных команд достаточно
                pass
            logger.info("Меню команд бота установлено")
        except Exception as e:
            logger.error(f"Не удалось установить меню команд: {e}")

    async def start_polling(self):
        """
        Запускает обработку сообщений бота с устойчивостью к потере сети.

        Если связь с Telegram пропадёт (отвалился VPN, провайдер режет DNS,
        api.telegram.org недоступен) — polling не падает намертво, а ждёт
        и переподключается. Когда интернет вернётся, бот продолжит работу
        сам, без перезапуска приложения. Это важно при нестабильном доступе
        к Telegram (блокировки, VPN).
        """
        logger.info("Telegram бот запущен")

        # Меню пробуем поставить, но если сети нет — не падаем, поставим позже
        try:
            await self.setup_bot_menu()
        except Exception as e:
            logger.warning(f"Меню команд не установлено (нет сети?): {e}")

        backoff = 5          # пауза перед повтором, сек
        max_backoff = 60     # потолок паузы

        while True:
            try:
                # handle_signals=False нужен, т.к. polling может работать не в
                # главном потоке (там нельзя ставить обработчики сигналов).
                # На случай несовместимости версии — откат на вызов без него.
                try:
                    await self.dp.start_polling(self.bot, handle_signals=False)
                except TypeError:
                    await self.dp.start_polling(self.bot)
                logger.info("Polling завершён штатно")
                break
            except asyncio.CancelledError:
                logger.info("Polling остановлен (отмена)")
                raise
            except Exception as e:
                logger.error(
                    f"Связь с Telegram потеряна ({type(e).__name__}). "
                    f"Повтор через {backoff} сек..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                # Когда сеть вернётся, start_polling на след. итерации
                # поднимется заново. После успешного старта backoff не
                # сбрасываем здесь — он сбросится логикой ниже при работе.
                continue
