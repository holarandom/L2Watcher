# main.py
"""
Главный файл приложения L2 Monitor.

Запуск:
1. Загружает конфиг (или открывает GUI настроек если конфиг не заполнен)
2. Запускает Telegram-бота
3. При первом запуске (если шаблоны не обучены) — запускает онбординг
4. Затем работает в режиме мониторинга: /start, /stop, /status управляют им
"""
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from config_manager import load_config, get_log_path, is_configured
from template_store import load_all_templates, has_template
from monitor import WindowRegistry
from telegram_bot import TelegramNotifier
from onboarding import OnboardingFlow


def setup_logging(cfg: dict):
    log_path = get_log_path()
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
    )
    # В файл лога и консоль идут только важные события — debug-level
    # совпадения шаблонов (которые раньше спамили консоль построчно
    # каждые 10 секунд) не пишутся вообще.
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[console_handler, file_handler]
    )
    # Приглушаем болтливые логи самой библиотеки aiogram/aiohttp
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)

# Ссылка на single-instance lock — держим живой всю сессию, чтобы мьютекс
# не освободился раньше времени (иначе вторая копия смогла бы запуститься).
_instance_lock = None


class L2MonitorApp:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.registry = WindowRegistry(window_check_interval=cfg["window_check_interval"])
        self.tg = TelegramNotifier(cfg["token"], cfg["chat_id"], cfg["characters"],
                                   message_style=cfg.get("message_style", "card"))
        self.tg.char_pick_format = cfg.get("char_pick_format", "A")
        # Режим захвата окон (BitBlt / PrintWindow) — выставляем глобально
        import window_capture
        window_capture.USE_PRINTWINDOW = cfg.get("read_overlapped_windows", False)
        self.onboarding = OnboardingFlow(self.tg, cfg)
        self.onboarding.app = self  # для пометки обучаемого окна (_training_hwnd)
        self.templates = {}
        self._monitor_task = None
        self._registration_tasks: list = []
        self._loop = None  # ссылка на главный asyncio loop (нужна потоку трея)
        self._capture_lock = None  # создаётся лениво при первом захвате
        self._training_hwnd = None  # окно, по которому идёт обучение (пропускается в мониторинге)

        self.tg.on_start(self._handle_start)
        self.tg.on_stop(self._handle_stop)
        self.tg.set_status_provider(self._status_text)
        self.tg.set_is_running_provider(self._is_monitoring_running)

        self._register_extra_commands()

    def _register_extra_commands(self):
        from aiogram.filters import Command
        from aiogram import types

        from aiogram.utils.keyboard import InlineKeyboardBuilder

        def _main_menu_kb():
            """Клавиатура главного меню — все действия кнопками."""
            b = InlineKeyboardBuilder()
            b.button(text="🟢 Старт", callback_data="menu:start")
            b.button(text="🔴 Стоп", callback_data="menu:stop")
            b.button(text="📊 Статус", callback_data="menu:status")
            b.button(text="🩺 Проверка настроек", callback_data="menu:health")
            b.button(text="🖼 Скрины окон", callback_data="menu:windows")
            b.button(text="🔄 Переназначить окна", callback_data="menu:redetect")
            b.button(text="🔄 Переобучить", callback_data="menu:retrain")
            b.button(text="🎨 Стиль", callback_data="style:show")
            b.button(text="🐞 Сообщить о проблеме", callback_data="menu:feedback")
            b.button(text="📄 Лог", callback_data="menu:log")
            b.adjust(2)
            return b.as_markup()

        @self.tg.dp.message(Command("menu"))
        async def menu_cmd(message: types.Message):
            await message.answer("📋 Что сделать?", reply_markup=_main_menu_kb())

        @self.tg.dp.message(Command("style"))
        async def style_cmd(message: types.Message):
            await self._send_style_picker(message)

        @self.tg.dp.callback_query(lambda c: c.data and c.data.startswith("style:"))
        async def handle_style(callback: types.CallbackQuery):
            import message_styles
            choice = callback.data.split(":", 1)[1]
            await callback.answer()
            if choice == "show":
                await self._send_style_picker(callback.message)
                return
            if choice in message_styles.STYLES:
                # Сохраняем выбор в конфиг и применяем на лету
                self.cfg["message_style"] = choice
                self.tg.message_style = choice
                from config_manager import save_config
                save_config(self.cfg)
                label = message_styles.STYLE_LABELS.get(choice, choice)
                preview = message_styles.style_preview(choice)
                await callback.message.answer(
                    f"✅ Стиль изменён на «{label}». Пример:\n\n{preview}"
                )

        @self.tg.dp.message(Command("health"))
        async def health_cmd(message: types.Message):
            await message.answer(self._health_text())

        @self.tg.dp.message(Command("log"))
        async def log_cmd(message: types.Message):
            from config_manager import get_log_path
            # Шлём последние 500 строк — достаточно для диагностики, не грузит
            # многомегабайтным файлом.
            await self.tg.send_log_file(get_log_path(), tail_lines=500)

        @self.tg.dp.message(Command("feedback"))
        async def feedback_cmd(message: types.Message):
            await message.answer(
                "🐞 Нашёл баг или есть идея?\n\n"
                "Напиши сюда: @L2WatcherFeedbackBot — сообщение попадёт "
                "напрямую разработчику.\n\n"
                "Что указать: версию (в /health), что делал, что произошло. "
                "Лог можно получить командой /log и переслать боту файлом."
            )

        @self.tg.dp.message(Command("redetect"))
        async def redetect_cmd(message: types.Message):
            await self._redetect_windows(message)

        @self.tg.dp.callback_query(lambda c: c.data and c.data.startswith("menu:"))
        async def handle_menu(callback: types.CallbackQuery):
            action = callback.data.split(":", 1)[1]
            await callback.answer()
            if action == "start":
                running = self._is_monitoring_running()
                if not running:
                    await self._handle_start()
                    await callback.message.answer("🟢 Мониторинг запущен")
                else:
                    await callback.message.answer("⚠️ Мониторинг уже запущен")
            elif action == "stop":
                await self._handle_stop()
                await callback.message.answer("🔴 Мониторинг остановлен")
            elif action == "status":
                await callback.message.answer(self._status_text())
            elif action == "health":
                await callback.message.answer(self._health_text())
            elif action == "windows":
                await self._send_windows_list(callback.message)
            elif action == "redetect":
                await self._redetect_windows(callback.message)
            elif action == "feedback":
                await callback.message.answer(
                    "🐞 Нашёл баг или есть идея?\n\n"
                    "Напиши сюда: @L2WatcherFeedbackBot — сообщение попадёт "
                    "напрямую разработчику.\n\n"
                    "Что указать: версию (в /health), что делал, что произошло. "
                    "Лог можно получить командой /log и переслать боту файлом."
                )
            elif action == "log":
                from config_manager import get_log_path
                await self.tg.send_log_file(get_log_path(), tail_lines=500)
            elif action == "retrain":
                await callback.message.answer("🔄 Переобучение шаблонов")
                asyncio.create_task(self._run_onboarding_and_reload(mode="retrain"))

        @self.tg.dp.message(Command("retrain"))
        async def retrain_cmd(message: types.Message):
            # Умное переобучение: бот спросит версию и тип, удалит/переобучит
            # ТОЛЬКО выбранное (не трогая остальные рабочие шаблоны).
            await message.answer("🔄 Переобучение шаблонов")
            asyncio.create_task(self._run_onboarding_and_reload(mode="retrain"))

        @self.tg.dp.message(Command("windows"))
        async def windows_cmd(message: types.Message):
            await self._send_windows_list(message)

        @self.tg.dp.callback_query(lambda c: c.data and c.data.startswith("showwin:"))
        async def handle_show_window(callback: types.CallbackQuery):
            try:
                hwnd = int(callback.data.split(":", 1)[1])
                screenshot_bytes = await asyncio.to_thread(self._capture_window_as_png, hwnd)
                if screenshot_bytes:
                    known = self.registry.windows.get(hwnd)
                    caption = known.label if known else "Окно (персонаж не назначен)"
                    await self.tg.send_photo(screenshot_bytes, caption=caption)
                else:
                    await self.tg.send("⚠️ Не удалось сделать скриншот окна")
                await callback.answer()
            except Exception as e:
                logger.error(f"Ошибка показа скриншота окна: {e}")
                await callback.answer("Ошибка")

        @self.tg.dp.callback_query(lambda c: c.data and c.data.startswith("reassign:"))
        async def handle_reassign(callback: types.CallbackQuery):
            try:
                hwnd = int(callback.data.split(":", 1)[1])
                await callback.answer("Переснимаю окно...")
                await self._reassign_window(hwnd)
            except Exception as e:
                logger.error(f"Ошибка переназначения окна: {e}")
                await callback.answer("Ошибка")

    async def _run_onboarding_and_reload(self, mode="full"):
        # Защита от повторного запуска: если онбординг уже идёт, второй
        # запуск (двойное нажатие кнопки/команды) игнорируем — иначе
        # пользователю дублируются все сообщения обучения.
        if getattr(self, "_onboarding_running", False):
            await self.tg.send("⏳ Обучение уже идёт. Заверши текущее или дождись окончания.")
            return
        self._onboarding_running = True
        try:
            if mode == "retrain":
                await self.onboarding.run_retrain()
            else:
                await self.onboarding.run_full_onboarding()
            self.templates = load_all_templates()
            # Автостарт мониторинга сразу после обучения
            await self._handle_start()
            await self.tg.send("🟢 Мониторинг запущен автоматически")
        finally:
            self._onboarding_running = False

    def _status_text(self) -> str:
        import message_styles
        windows = []
        for w in self.registry.all():
            # Реальное состояние последнего тика. Закрытых окон здесь уже
            # нет — они удаляются из реестра сразу после уведомления.
            if w.last_state == "disconnect":
                state = "disconnect"
            elif w.last_state == "dead":
                state = "dead"
            else:
                state = "alive"
            windows.append({"name": w.char_name, "version": w.version, "state": state})
        return message_styles.format_status(self.cfg.get("message_style", "card"), windows)

    def _health_text(self) -> str:
        """
        Диагностика 'покажи что видишь' — чтобы пользователь мог убедиться,
        что приложение реально работает и правильно настроено, а не молчит
        потому что сломалось.
        """
        from window_capture import get_l2_hwnds
        from template_store import has_template

        from version import APP_VERSION, APP_NAME
        lines = [f"🩺 Проверка состояния\n{APP_NAME} v{APP_VERSION}\n"]

        # Мониторинг
        running = self._is_monitoring_running()
        lines.append(f"Мониторинг: {'🟢 идёт' if running else '🔴 остановлен (нажми Старт)'}")

        # Тихий режим
        from config_manager import is_quiet_now
        qh = self.cfg.get("quiet_hours", {})
        if qh.get("enabled"):
            now_quiet = is_quiet_now(self.cfg)
            state = "🌙 сейчас активен (не сканирую)" if now_quiet else "☀️ сейчас не активен"
            lines.append(f"Тихий режим: {qh.get('start','?')}–{qh.get('end','?')}, {state}")

        # Окна сейчас открыты в игре
        open_hwnds = get_l2_hwnds()
        lines.append(f"Окон Lineage II открыто: {len(open_hwnds)}")

        # Окна, которые приложение отслеживает (назначены персонажи)
        tracked = self.registry.all()
        lines.append(f"Отслеживается (с именем): {len(tracked)}")
        for w in tracked:
            lines.append(f"   • {w.label}")

        # Загруженные шаблоны — по ним видно, что детект вообще возможен
        lines.append("\nШаблоны распознавания:")
        labels = {
            "main_death": "Смерть (Main)",
            "essence_death": "Смерть (Essence)",
            "main_disconnect": "Дисконнект (Main)",
            "essence_disconnect": "Дисконнект (Essence)",
        }
        for key, label in labels.items():
            mark = "✅" if has_template(key) else "❌ нет"
            lines.append(f"   {mark} {label}")


        # Подсказка если что-то не так
        needed = set(ch["version"] for ch in self.cfg.get("characters", []))
        missing = [k for k in labels if k.split("_")[0] in needed and not has_template(k)]
        if missing:
            lines.append("\n⚠️ Часть шаблонов не обучена — соответствующие "
                         "события ловиться не будут. Обучи через 🔄 Переобучить.")

        return "\n".join(lines)

    async def _send_style_picker(self, message):
        """Показывает выбор стиля оформления с превью каждого варианта."""
        import message_styles
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        current = self.cfg.get("message_style", "card")
        cur_label = message_styles.STYLE_LABELS.get(current, current)

        # Собираем превью всех стилей в одном сообщении
        parts = [f"🎨 <b>Стиль оформления</b>\nСейчас: <b>{cur_label}</b>\n"]
        for s in message_styles.STYLES:
            label = message_styles.STYLE_LABELS[s]
            desc = message_styles.STYLE_DESCRIPTIONS[s]
            preview = message_styles.style_preview(s)
            mark = " ✅" if s == current else ""
            parts.append(f"\n━━━ <b>{label}</b>{mark} ━━━\n<i>{desc}</i>\n{preview}")

        b = InlineKeyboardBuilder()
        for s in message_styles.STYLES:
            b.button(text=message_styles.STYLE_LABELS[s], callback_data=f"style:{s}")
        b.adjust(2)

        await message.answer("\n".join(parts), reply_markup=b.as_markup())

    async def _redetect_windows(self, message):
        """Переназначить окна: показывает ВСЕ открытые окна (и уже названные)
        с кнопкой смены персонажа. Для случая 'ошибся при выборе персонажа'."""
        from window_capture import get_l2_hwnds
        hwnds = get_l2_hwnds()
        if not hwnds:
            await message.answer("⚠️ Сейчас нет открытых окон Lineage II")
            return
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for i, hwnd in enumerate(hwnds, 1):
            known = self.registry.windows.get(hwnd)
            label = known.label if known else f"Окно {i} (не назначено)"
            rows.append([InlineKeyboardButton(
                text=f"✏️ {label}", callback_data=f"reassign:{hwnd}")])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(
            f"Открыто окон: {len(hwnds)}\n"
            f"Нажми на окно, чтобы выбрать/сменить персонажа:",
            reply_markup=markup
        )

    async def _send_windows_list(self, message):
        """Скрины окон: показывает список открытых окон, по кнопке — свежий
        снимок. Только просмотр, без переназначения."""
        from window_capture import get_l2_hwnds
        hwnds = get_l2_hwnds()
        if not hwnds:
            await message.answer("⚠️ Сейчас нет открытых окон Lineage II")
            return
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for i, hwnd in enumerate(hwnds, 1):
            known = self.registry.windows.get(hwnd)
            label = known.label if known else f"Окно {i} (не назначено)"
            rows.append([InlineKeyboardButton(
                text=f"🖼 {label}", callback_data=f"showwin:{hwnd}")])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer(
            f"Открыто окон: {len(hwnds)}\nНажми, чтобы увидеть скрин окна:",
            reply_markup=markup
        )

    async def _reassign_window(self, hwnd: int):
        """Переназначение персонажа окну (даже если уже назначено).
        Снимает свежий скрин и заново предлагает выбрать персонажа.
        Закрывает кейсы: окно было свёрнуто (скрин чёрный) — теперь развернул
        и хочешь свежий; или ошибся при выборе и хочешь сменить персонажа."""
        from window_capture import get_l2_hwnds
        if hwnd not in get_l2_hwnds():
            await self.tg.send("⚠️ Это окно уже закрыто.")
            return
        # Убираем старое назначение этого окна (если было) и его пометку
        # в _pending — чтобы запустить регистрацию заново ТОЛЬКО для него,
        # не трогая остальные окна, ждущие ответа.
        if hwnd in self.registry.windows:
            del self.registry.windows[hwnd]
        self.registry.discard_pending(hwnd)
        task = asyncio.create_task(self._register_window_async(hwnd))
        self._registration_tasks.append(task)

    async def _register_window_async(self, hwnd: int):
        """
        Регистрирует новое окно в фоне — ждёт выбора персонажа в Telegram,
        но НЕ блокирует основной цикл мониторинга (другие уже
        зарегистрированные окна продолжают проверяться пока ждём ответ).
        Перед кнопками шлём скриншот окна — иначе пользователь видит
        только бессмысленный hex ID и не понимает, какое окно выбирает.
        """
        # Задержка перед первым захватом: только что появившееся окно игры
        # ещё грузится (загрузочный экран / чёрный кадр), скринить рано.
        # Ждём, пока клиент отрисуется. Настраивается через конфиг.
        delay = self.cfg.get("new_window_capture_delay_sec", 5)
        if delay > 0:
            await asyncio.sleep(delay)
            # Окно могло закрыться за время ожидания — проверим
            from window_capture import get_l2_hwnds
            if hwnd not in get_l2_hwnds():
                logger.info(f"Окно 0x{hwnd:X} закрылось за время ожидания отрисовки")
                return

        # Захват скрина с повтором: при детекте нескольких окон разом
        # параллельные GDI-захваты могут конфликтовать, и часть окон
        # возвращала None → сообщение уходило без скрина. Делаем до 3
        # попыток с небольшой паузой, чтобы скрин точно приложился.
        # Захваты окон сериализуем (lock), чтобы параллельные GDI-вызовы
        # не конфликтовали. Lock создаём лениво — здесь loop уже работает.
        if self._capture_lock is None:
            self._capture_lock = asyncio.Lock()

        screenshot_bytes = None
        for attempt in range(3):
            async with self._capture_lock:
                screenshot_bytes = await asyncio.to_thread(self._capture_window_as_png, hwnd)
            if screenshot_bytes:
                break
            await asyncio.sleep(0.4)
        if not screenshot_bytes:
            logger.warning(f"Не удалось снять скрин окна 0x{hwnd:X} после 3 попыток")

        try:
            char_name, version = await self.tg.ask_character_for_window(hwnd, screenshot_bytes=screenshot_bytes)
        except asyncio.CancelledError:
            logger.info(f"Регистрация окна 0x{hwnd:X} прервана (окно закрылось)")
            return
        self.registry.add(hwnd, char_name, version)
        logger.info(f"Добавлено окно: {char_name} ({version}) 0x{hwnd:X}")

    @staticmethod
    def _capture_window_as_png(hwnd: int):
        """Захватывает окно и кодирует в PNG bytes для отправки в Telegram.
        Возвращает None если кадр пустой (чёрный) — чтобы вызывающий код
        мог переснять."""
        from window_capture import capture_window, is_window_blank
        import cv2
        frame = capture_window(hwnd)
        if frame is None or is_window_blank(frame):
            return None
        ok, buf = cv2.imencode(".png", frame)
        return buf.tobytes() if ok else None

    def _is_monitoring_running(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    async def _handle_start(self):
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("Мониторинг запущен")

    async def _handle_stop(self):
        if self._monitor_task is not None:
            self._monitor_task.cancel()
        for task in self._registration_tasks:
            if not task.done():
                task.cancel()
        self._registration_tasks.clear()
        logger.info("Мониторинг остановлен")

    async def _monitor_loop(self):
        from config_manager import is_quiet_now
        self._was_quiet = False  # для отслеживания входа/выхода из тихого режима
        try:
            while True:
                # Тихий режим: в заданном диапазоне НЕ сканируем вообще —
                # ноль нагрузки на процессор (кулеры молчат). Проверяем по
                # системному времени на каждом тике.
                quiet = is_quiet_now(self.cfg)
                if quiet:
                    if not self._was_quiet:
                        self._was_quiet = True
                        qh = self.cfg.get("quiet_hours", {})
                        logger.info("Вошёл в тихий режим — сканирование приостановлено")
                        try:
                            await self.tg.send(
                                f"🌙 Тихий режим до {qh.get('end', '?')} — "
                                f"мониторинг приостановлен, не нагружаю систему. "
                                f"Утром продолжу сам."
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(30)  # спим, периодически проверяя время
                    continue
                else:
                    if self._was_quiet:
                        self._was_quiet = False
                        logger.info("Вышел из тихого режима — сканирование возобновлено")
                        try:
                            await self.tg.send("☀️ Тихий режим закончился — снова на страже.")
                        except Exception:
                            pass

                # ПРИМЕЧАНИЕ: раньше здесь стояла глобальная пауза мониторинга
                # на время обучения (_onboarding_running). Она оказалась вредной:
                # обучение ждёт действий пользователя минутами, и всё это время
                # НЕ сканировались ВСЕ окна (не только обучаемое). А если флоу
                # обучения прерывался, флаг мог залипнуть и детект вставал колом
                # до перезапуска — отсюда пропадал, например, дисконнект.
                # Защита от ложных срабатываний при обучении делается точечно
                # ниже: окно, по которому прямо сейчас идёт обучение, помечается
                # в registry и пропускается индивидуально, не трогая остальные.

                # Пороги читаем из self.cfg на КАЖДОМ тике, а не один раз при
                # старте — чтобы изменения, внесённые в настройках (и применённые
                # через _reload_config_live), подхватывались без перезапуска
                # мониторинга. check_interval ниже тоже читается из self.cfg.
                thresholds = {
                    "death": self.cfg["match_threshold_death"],
                    "disconnect": self.cfg["match_threshold_disconnect"],
                }
                try:
                    if self.registry.due_for_check():
                        new_hwnds = self.registry.refresh()
                        closed_hwnds = self.registry.get_closed()

                        for hwnd in closed_hwnds:
                            w = self.registry.windows[hwnd]
                            await self.tg.notify_event("window_closed", w.char_name, w.version, None)
                            # Окна больше нет — убираем из реестра совсем, а не
                            # держим вечно с флагом "закрыто". Иначе закрытая
                            # запись висела бы в /status, дублируя живое окно с
                            # тем же именем (если ты переоткрыл окно того же перса).
                            del self.registry.windows[hwnd]
                            logger.info(f"Окно 0x{hwnd:X} ({w.label}) закрылось, убрано из реестра")

                        # Окна, которые закрылись, не дождавшись выбора
                        # персонажа — убираем висящие кнопки регистрации
                        # из чата и чистим ожидание (баг с "висящими плашками").
                        for hwnd in self.registry.get_closed_pending():
                            await self.tg.cancel_registration(hwnd)
                            logger.info(f"Регистрация окна 0x{hwnd:X} отменена — окно закрылось")

                        for hwnd in new_hwnds:
                            # Регистрация окна (ожидание выбора персонажа в Telegram)
                            # запускается в фоне — не блокирует проверку уже
                            # зарегистрированных окон пока человек не ответит
                            task = asyncio.create_task(self._register_window_async(hwnd))
                            self._registration_tasks.append(task)

                        # Чистим завершённые задачи, чтобы список не рос бесконечно
                        self._registration_tasks = [t for t in self._registration_tasks if not t.done()]

                    training_hwnd = getattr(self, "_training_hwnd", None)
                    for window in self.registry.all():
                        # Окно, по которому ПРЯМО СЕЙЧАС идёт обучение, временно
                        # пропускаем: там намеренно вызвана табличка смерти/
                        # дисконнекта, и реагировать на неё не нужно. Остальные
                        # окна продолжают отслеживаться как обычно.
                        if training_hwnd is not None and window.hwnd == training_hwnd:
                            continue
                        await window.check(self.templates, thresholds, self.tg.notify_event)
                except Exception as e:
                    logger.error(f"Ошибка в теле цикла мониторинга: {e}", exc_info=True)

                await asyncio.sleep(self.cfg["check_interval"])
        except asyncio.CancelledError:
            logger.info("Цикл мониторинга остановлен")

    async def run(self):
        # Запоминаем работающий event loop — потоку трея он нужен, чтобы
        # после закрытия окна настроек (в отдельном процессе) безопасно
        # запланировать перечитывание конфига в этом цикле.
        self._loop = asyncio.get_running_loop()

        # Проверяем валидность токена ДО всего остального. Если токен битый
        # или отозван — бот не сможет ничего отправить, и без явной проверки
        # это выглядело бы как "приложение молча не работает". Показываем
        # понятное окно с подсказкой.
        try:
            me = await self.tg.bot.get_me()
            logger.info(f"Бот авторизован: @{me.username}")
        except Exception as e:
            logger.error(f"Не удалось авторизоваться в Telegram: {e}")
            self._show_token_error()
            return

        # Проверяем какие шаблоны есть, но НЕ блокируем старт их отсутствием.
        # Мониторинг работает с тем, что есть: для окна без шаблона смерти
        # проверка смерти просто пропускается (см. monitor.py check —
        # tmpl_death is None → ветка не выполняется), а дисконнект и
        # "окно закрылось" работают независимо от наличия death-шаблона.
        self.templates = load_all_templates()
        needed_versions = set(ch["version"] for ch in self.cfg.get("characters", []))
        missing = [v for v in needed_versions if not has_template(f"{v}_death")]
        missing_dc = [v for v in needed_versions if not has_template(f"{v}_disconnect")]

        warn = ""
        if missing or missing_dc:
            parts = []
            for v in missing:
                parts.append(f"смерть ({v.capitalize()})")
            for v in missing_dc:
                parts.append(f"дисконнект ({v.capitalize()})")
            warn = (
                "\n\n⚠️ Не обучены шаблоны: " + ", ".join(parts) + ".\n"
                "Мониторинг запустится для остального. Когда будет удобно — "
                "обучи недостающее командой /retrain (умирать прямо сейчас не нужно)."
            )

        from version import APP_NAME, APP_VERSION
        await self.tg.send(
            f"🤖 {APP_NAME} v{APP_VERSION} запущен и готов к работе.\n\n"
            "Я буду присылать уведомления, когда персонаж погибнет, "
            "потеряет соединение или его окно закроется.\n\n"
            "👉 Нажми /menu — все действия доступны кнопками "
            "(старт, статус, проверка). Команды можно не печатать руками.\n"
            "🩺 /health — проверка настроек: что вижу и всё ли готово."
            + warn
        )


        # Автостарт мониторинга, если включена галка в настройках —
        # тогда не ждём команду /start, а запускаем мониторинг сразу.
        # Удобно в паре с автозагрузкой Windows: комп включился → прога
        # поднялась → мониторинг уже идёт, без ручных действий.
        if self.cfg.get("autostart_monitoring", False):
            await self._handle_start()
            await self.tg.send("🟢 Мониторинг запущен автоматически")

        await self.tg.start_polling()

    def _show_token_error(self):
        """Показывает понятное окно при невалидном токене Telegram."""
        try:
            import tkinter as tk
            import tkinter.messagebox as mb
            r = tk.Tk()
            r.withdraw()
            mb.showerror(
                "Ошибка подключения к Telegram",
                "Не удалось подключиться к боту Telegram.\n\n"
                "Скорее всего, токен бота неверный или был отозван.\n\n"
                "Что делать:\n"
                "1. Открой настройки (иконка в трее → Настройки)\n"
                "2. Проверь токен бота — он должен быть скопирован\n"
                "   из @BotFather полностью, без пробелов\n"
                "3. Сохрани и перезапусти приложение"
            )
            r.destroy()
        except Exception:
            logger.error("Невалидный токен Telegram — проверьте настройки")

    def open_settings_window(self):
        """
        Открывает окно настроек ОТДЕЛЬНЫМ ПРОЦЕССОМ (вызывается из трея).

        Раньше настройки запускались вторым tk.Tk() в отдельном потоке —
        tkinter не потокобезопасен, и это могло крашить на чужих машинах.
        Отдельный процесс полностью развязывает GUI и бота: окно настроек
        живёт само по себе, краш/зависание GUI не трогает бота, и (важно!)
        GUI физически не может трогать self.cfg бота из другого потока —
        это убирает и потенциальный race при сохранении.

        После закрытия окна перечитываем конфиг с диска и применяем то,
        что можно применить без перезапуска (персонажи, пороги, интервалы).
        """
        import threading
        threading.Thread(target=self._run_settings_process, daemon=True).start()

    def _run_settings_process(self):
        """Запускает settings_gui.py как отдельный процесс и ждёт его закрытия."""
        import subprocess

        if getattr(sys, "frozen", False):
            # В собранном .exe нет отдельного python — нужно, чтобы .exe
            # умел запускать себя в режиме настроек по флагу --settings
            # (обрабатывается в main() ниже).
            cmd = [sys.executable, "--settings"]
        else:
            settings_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings_gui.py")
            cmd = [sys.executable, settings_py]

        try:
            subprocess.run(cmd)
        except Exception as e:
            logger.error(f"Не удалось открыть окно настроек: {e}")
            return

        # Окно закрылось — перечитываем конфиг и применяем на лету.
        # Делаем это в asyncio-цикле через потокобезопасный вызов.
        if self._loop is not None:
            import asyncio as _asyncio
            _asyncio.run_coroutine_threadsafe(self._reload_config_live(), self._loop)

    async def _reload_config_live(self):
        """
        Перечитывает конфиг с диска и применяет изменения без перезапуска.
        Применяется на лету: список персонажей, пороги детекта, интервалы.
        Требуют перезапуска (и потому НЕ трогаются тут): токен и chat_id —
        для них нужно переподключение Telegram-бота.
        """
        from config_manager import load_config
        new_cfg = load_config()

        # Персонажи — обновляем и в конфиге, и в активном боте
        self.cfg["characters"] = new_cfg.get("characters", self.cfg["characters"])
        self.tg.characters = self.cfg["characters"]

        # Пороги и интервалы — обновляем в self.cfg; монитор-цикл читает
        # их из self.cfg на каждом тике (см. _monitor_loop), так что
        # новые значения подхватятся сами, без перезапуска мониторинга.
        for k in ("match_threshold_death", "match_threshold_disconnect",
                  "check_interval", "window_check_interval"):
            self.cfg[k] = new_cfg.get(k, self.cfg[k])

        # Интервал проверки новых окон живёт в registry — обновляем и там
        self.registry.window_check_interval = self.cfg["window_check_interval"]


        # Стиль оформления — тоже на лету
        self.cfg["message_style"] = new_cfg.get("message_style", self.cfg.get("message_style", "card"))
        self.tg.message_style = self.cfg["message_style"]
        self.cfg["char_pick_format"] = new_cfg.get("char_pick_format", self.cfg.get("char_pick_format", "A"))
        self.tg.char_pick_format = self.cfg["char_pick_format"]
        self.cfg["quiet_hours"] = new_cfg.get("quiet_hours", self.cfg.get("quiet_hours", {}))
        # Режим захвата окон на лету
        import window_capture
        window_capture.USE_PRINTWINDOW = new_cfg.get("read_overlapped_windows",
                                                     self.cfg.get("read_overlapped_windows", False))
        self.cfg["read_overlapped_windows"] = new_cfg.get("read_overlapped_windows",
                                                          self.cfg.get("read_overlapped_windows", False))

        logger.info("Настройки перечитаны и применены на лету")


    def request_exit(self):
        """Корректно останавливает приложение (вызывается из трея 'Выход')."""
        logger.info("Завершение приложения по запросу из трея")
        # Гасим приёмник фидбека (режим разработчика), если он был запущен —
        # иначе фоновый python.exe останется висеть и держать файлы в dist,
        # ломая следующую сборку.
        try:
            if _feedback_proc is not None and _feedback_proc.poll() is None:
                _feedback_proc.kill()
        except Exception:
            pass
        logging.shutdown()  # принудительно сбрасывает буферы всех хендлеров лога на диск
        os._exit(0)  # простой и надёжный способ остановить все потоки/event loop сразу


_feedback_proc = None  # процесс приёмника фидбека (режим разработчика)


def main():
    # DPI awareness: без него Windows при масштабировании экрана (125%/150%)
    # отдаёт приложению "виртуальные" координаты/размеры, и захват окна
    # получается обрезанным/смещённым — шаблоны не совпадают. Объявляем себя
    # per-monitor DPI aware, чтобы работать в реальных пикселях. Делаем это
    # САМЫМ ПЕРВЫМ, до любых GUI/захватов. Не критично если не вышло.
    try:
        import ctypes
        try:
            # Windows 8.1+ : PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            # старее — System DPI aware
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    # Режим "только настройки": когда приложение запущено с флагом --settings
    # (так трей открывает настройки в собранном .exe — отдельным процессом
    # того же экзешника). Просто показываем окно настроек и выходим.
    if "--settings" in sys.argv:
        from settings_gui import SettingsWindow
        SettingsWindow().run()
        sys.exit(0)

    # Single-instance: если приложение уже запущено — не плодим вторую копию
    # (иначе обе копии поллят одного бота → Telegram Conflict, сбои). Держим
    # ссылку на lock в течение всей жизни процесса (на модульном уровне ниже).
    global _instance_lock
    # Режим разработчика: если рядом с приложением лежит feedback_config.json,
    # автоматически запускаем приёмник обратной связи отдельным процессом.
    # У обычных пользователей этого файла нет — блок молча пропускается.
    try:
        import subprocess, shutil
        _base = os.path.dirname(os.path.abspath(sys.argv[0]))
        _fb_cfg = os.path.join(_base, "feedback_config.json")
        _fb_script = os.path.join(_base, "feedback_receiver.py")
        if os.path.exists(_fb_cfg) and os.path.exists(_fb_script):
            # В собранном .exe sys.executable — это сам L2Watcher.exe, им
            # скрипт не запустить. Ищем настоящий python в системе.
            _py = None
            if not getattr(sys, "frozen", False):
                _py = sys.executable
            else:
                _py = (shutil.which("pythonw") or shutil.which("python")
                       or shutil.which("py"))
            if _py:
                global _feedback_proc
                _feedback_proc = subprocess.Popen(
                    [_py, _fb_script],
                    cwd=_base,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                )
                logging.getLogger(__name__).info(
                    "Приёмник фидбека запущен (режим разработчика)")
            else:
                logging.getLogger(__name__).warning(
                    "feedback_config.json найден, но Python в системе не "
                    "найден — приёмник фидбека НЕ запущен")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Приёмник фидбека не запустился: {e}")

    from single_instance import SingleInstance
    _instance_lock = SingleInstance()
    if _instance_lock.already_running():
        # Пытаемся показать дружелюбное сообщение, но без критичной зависимости
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            r = tk.Tk(); r.withdraw()
            mb.showinfo("L2 Monitor уже запущен",
                        "L2 Monitor уже работает.\n"
                        "Иконка приложения есть в системном трее (рядом с часами).")
            r.destroy()
        except Exception:
            print("L2 Monitor уже запущен (см. иконку в трее).")
        sys.exit(0)

    cfg = load_config()

    if not is_configured(cfg):
        # Конфиг не заполнен — открываем GUI настроек, ждём сохранения
        from settings_gui import SettingsWindow

        result_holder = {}

        def on_save(new_cfg):
            result_holder["cfg"] = new_cfg

        window = SettingsWindow(on_save_callback=on_save)
        window.run()

        if "cfg" not in result_holder:
            print("Настройки не были сохранены. Завершение.")
            sys.exit(0)

        cfg = result_holder["cfg"]

    setup_logging(cfg)
    from version import APP_NAME, APP_VERSION
    logger.info(f"=== {APP_NAME} v{APP_VERSION} запускается ===")

    app = L2MonitorApp(cfg)

    # Трей — иконка в системном трее с меню (Настройки / Лог / Выход).
    # Без него после сборки в .exe без консоли не было бы способа
    # ни закрыть программу нормально, ни открыть настройки повторно.
    from tray import TrayIcon
    from config_manager import get_log_path

    tray = TrayIcon(
        on_open_settings=app.open_settings_window,
        on_exit=app.request_exit,
        log_path=get_log_path()
    )
    tray.run_in_background()

    asyncio.run(app.run())


if __name__ == "__main__":
    main()
