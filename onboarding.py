# onboarding.py
"""
Воркфлоу первого запуска: просим человека один раз умереть в игре
(и поймать дисконнект, если получится), чтобы самообучить шаблоны.

Логика:
1. Бот просит выбрать окно, по которому будем обучаться
2. Снимаем baseline-кадр (персонаж жив)
3. Просим умереть, ждём подтверждения через кнопку "Готово, я умер"
4. Снимаем 2-3 кадра после смерти, прогоняем через template_learner
5. Сохраняем шаблон, повторяем для второй версии игры если нужно
6. То же самое для дисконнекта (с возможностью пропустить —
   тогда используется заготовленный дефолтный шаблон)
"""
import asyncio
import logging
from typing import Optional
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from window_capture import capture_window, get_l2_hwnds
from template_learner import learn_template_from_window
from template_store import save_template, has_template

logger = logging.getLogger(__name__)


class OnboardingFlow:
    def __init__(self, telegram_notifier, config: dict):
        self.tg = telegram_notifier
        self.config = config
        self.app = None  # ссылка на приложение (для пометки обучаемого окна)
        self._waiting_futures = {}
        self._register_handlers()

    def _set_training(self, hwnd):
        """Помечает окно как обучаемое — мониторинг его пропускает,
        чтобы не слать ложные уведомления на таблички, вызванные для
        обучения. None = снять пометку."""
        if self.app is not None:
            self.app._training_hwnd = hwnd

    def _register_handlers(self):
        @self.tg.dp.callback_query(lambda c: c.data and c.data.startswith("onb:"))
        async def handle_onboarding_button(callback: types.CallbackQuery):
            action = callback.data.split(":", 1)[1]

            if action == "pick_skip":
                # Пропуск обучения — резолвим все pick-futures значением -1,
                # _pick_window_for_learning поймёт это как "пропустить".
                pick_keys = [k for k in self._waiting_futures if k.startswith("pick_")]
                for k in pick_keys:
                    future = self._waiting_futures.pop(k, None)
                    if future and not future.done():
                        future.set_result(-1)
                await callback.message.edit_text("⏭ Обучение пропущено")
                await callback.answer()
                return

            if action.startswith("pick_"):
                hwnd = int(action[len("pick_"):])
                # Все ожидающие futures для выбора окна получают тот же hwnd —
                # резолвим только первый незавершённый, остальные отменяем
                pick_keys = [k for k in self._waiting_futures if k.startswith("pick_")]
                for k in pick_keys:
                    future = self._waiting_futures.pop(k, None)
                    if future and not future.done():
                        future.set_result(hwnd)
                await callback.message.edit_text("✅ Окно выбрано")
                await callback.answer()
                return

            if action in self._waiting_futures:
                future = self._waiting_futures.pop(action)
                if not future.done():
                    future.set_result(True)
            await callback.answer()

    async def _wait_for_button(self, action_key: str, text: str, button_text: str,
                               skip_button: bool = False, extra_button: tuple = None) -> bool:
        """
        Показывает кнопку, ждёт нажатия. Возвращает True если нажали основную,
        False если skip/extra.
        extra_button=(текст, ключ) — произвольная вторая кнопка (например
        «Найти ещё раз»); при её нажатии возвращается False.
        """
        builder = InlineKeyboardBuilder()
        builder.button(text=button_text, callback_data=f"onb:{action_key}")
        alt_key = None
        if skip_button:
            alt_key = f"{action_key}_skip"
            builder.button(text="Пропустить (использовать заготовку)", callback_data=f"onb:{alt_key}")
        elif extra_button:
            alt_key = extra_button[1]
            builder.button(text=extra_button[0], callback_data=f"onb:{alt_key}")
        builder.adjust(1)

        await self.tg.send(text, reply_markup=builder.as_markup())

        future = asyncio.get_running_loop().create_future()
        self._waiting_futures[action_key] = future

        if alt_key is None:
            await future
            self._waiting_futures.pop(action_key, None)
            return True

        alt_future = asyncio.get_running_loop().create_future()
        self._waiting_futures[alt_key] = alt_future

        done, pending = await asyncio.wait(
            [future, alt_future], return_when=asyncio.FIRST_COMPLETED
        )
        self._waiting_futures.pop(action_key, None)
        self._waiting_futures.pop(alt_key, None)
        for f in pending:
            f.cancel()
        return future in done

    async def _pick_window_for_learning(self, version_label: str) -> Optional[int]:
        """Просит выбрать, на каком окне будем учиться (если несколько окон открыто)."""
        hwnds = get_l2_hwnds()
        if not hwnds:
            await self.tg.send(
                f"⚠️ Не вижу открытых окон Lineage II.\n"
                f"Открой игру ({version_label}) и зайди персонажем, потом напиши /retrain"
            )
            return None

        if len(hwnds) == 1:
            return hwnds[0]

        # Несколько окон — шлём скрин каждого, чтобы было видно, где какой
        # персонаж, и человек выбрал осознанно, а не наугад по номеру.
        await self.tg.send(f"Открыто несколько окон ({len(hwnds)}). Сейчас покажу каждое:")
        import cv2
        for i, hwnd in enumerate(hwnds, 1):
            try:
                frame = await asyncio.to_thread(capture_window, hwnd)
                if frame is not None:
                    ok, buf = cv2.imencode(".png", frame)
                    if ok:
                        await self.tg.send_photo(buf.tobytes(), caption=f"🖼 Окно {i}")
            except Exception as e:
                logger.warning(f"Не удалось снять скрин окна {i}: {e}")

        builder = InlineKeyboardBuilder()
        for i, hwnd in enumerate(hwnds, 1):
            builder.button(text=f"Выбрать окно {i}", callback_data=f"onb:pick_{hwnd}")
        builder.button(text="⏭ Пропустить обучение", callback_data="onb:pick_skip")
        builder.adjust(1)
        await self.tg.send(f"На каком окне сейчас персонаж ({version_label})?",
                           reply_markup=builder.as_markup())

        future = asyncio.get_running_loop().create_future()
        for hwnd in hwnds:
            self._waiting_futures[f"pick_{hwnd}"] = future

        try:
            chosen_hwnd = await asyncio.wait_for(future, timeout=600)
            if chosen_hwnd == -1:
                return None  # пользователь нажал "Пропустить"
            return chosen_hwnd
        except asyncio.TimeoutError:
            for hwnd in hwnds:
                self._waiting_futures.pop(f"pick_{hwnd}", None)
            # НЕ угадываем "первое окно" — это приводило к обучению не на том
            # окне. Честно отменяем и просим повторить, когда будет удобно.
            await self.tg.send(
                "⏱ Не дождался выбора окна. Обучение отменено.\n"
                "Запусти заново через /retrain, когда будешь готов."
            )
            return None

    async def _capture_and_learn(self, hwnd: int, baseline):
        """Общая логика снятия кадров и прогона через template_learner —
        используется и для смерти, и для дисконнекта, чтобы не дублировать код."""
        await self.tg.send("📸 Снимаю кадры...")

        def capture_fn():
            return capture_window(hwnd)

        # learn_template_from_window синхронная и внутри делает time.sleep ×3
        # (~1.8 сек). Без to_thread эти паузы блокировали бы весь asyncio
        # event loop — бот переставал отвечать на кнопки/команды на это время.
        return await asyncio.to_thread(
            learn_template_from_window,
            capture_fn, baseline_frame=baseline, n_frames=3, interval_sec=0.6
        )

    async def _confirm_template(self, template, what_label: str) -> bool:
        """
        Показывает распознанный шаблон картинкой и спрашивает, верно ли
        распозналось. Возвращает True если пользователь подтвердил, False
        если хочет переснять ("найти ещё раз").
        Так человек видит, что реально попало в шаблон (вся ли табличка
        смерти/дисконнекта влезла), и может переснять, если нет.
        """
        # Оценка качества — предупреждаем, если шаблон выглядит ненадёжным
        # (мелкий или почти чёрный фон). Частая причина — окно было свёрнуто
        # или не отрисовалось, и в шаблон попала крошечная табличка на черноте.
        # Такой шаблон потом плохо детектит. Лучше предупредить до сохранения.
        warn = ""
        try:
            import cv2
            import numpy as np
            h, w = template.shape[:2]
            gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            dark_ratio = float(np.mean(gray < 30))  # доля очень тёмных пикселей
            if w < 120 or h < 100:
                warn = ("\n\n⚠️ Шаблон выглядит МЕЛКИМ — возможно, табличка "
                        "попала не целиком. Лучше переснять.")
            elif dark_ratio > 0.6:
                warn = ("\n\n⚠️ Вокруг таблички много чёрного — возможно, окно "
                        "было свёрнуто/не отрисовалось. Такой шаблон плохо "
                        "распознаётся. Лучше переснять при развёрнутом окне.")
        except Exception:
            pass

        try:
            import cv2
            ok, buf = cv2.imencode(".png", template)
            if ok:
                await self.tg.send_photo(
                    buf.tobytes(),
                    caption=f"🔍 Вот что я распознал как «{what_label}».\n"
                            f"Табличка попала целиком и правильно?{warn}"
                )
        except Exception as e:
            logger.warning(f"Не удалось показать превью шаблона: {e}")

        proceeded = await self._wait_for_button(
            "tmpl_confirm",
            "Если табличка распозналась верно — сохраняю. Если нет "
            "(обрезана, не та, попал кусок интерфейса) — нажми «Найти ещё раз», "
            "убедись что табличка целиком на экране, и я пересниму.",
            "✅ Да, сохранить",
            skip_button=False,
            extra_button=("🔄 Найти ещё раз", "tmpl_retry")
        )
        return proceeded

    async def learn_death_template(self, version: str) -> bool:
        """Обёртка: помечает обучаемое окно (мониторинг его пропускает,
        чтобы не слать ложные уведомления), гарантированно снимает пометку."""
        try:
            return await self._learn_death_template_impl(version)
        finally:
            self._set_training(None)

    async def learn_disconnect_template(self, version: str) -> bool:
        try:
            return await self._learn_disconnect_template_impl(version)
        finally:
            self._set_training(None)

    async def _learn_death_template_impl(self, version: str) -> bool:
        """
        Воркфлоу обучения шаблона смерти для указанной версии (main/essence).
        Возвращает True если шаблон успешно сохранён.
        """
        version_label = version.capitalize()
        hwnd = await self._pick_window_for_learning(version_label)
        if hwnd is None:
            return False
        self._set_training(hwnd)

        baseline = capture_window(hwnd)
        if baseline is None:
            await self.tg.send("⚠️ Не удалось сделать скриншот окна. Попробуй позже через /retrain")
            return False
        from window_capture import is_window_blank
        if is_window_blank(baseline):
            await self.tg.send(
                "⚠️ Окно выглядит пустым (чёрным). Скорее всего оно свёрнуто.\n"
                "Разверни окно игры на экране и запусти обучение заново через /retrain."
            )
            return False

        await self._wait_for_button(
            f"death_{version}",
            f"💀 Обучение шаблона смерти ({version_label})\n\n"
            f"Дай персонажу умереть в игре (любым способом), дождись таблички "
            f"воскрешения на экране, и нажми кнопку ниже.",
            "Готово, я умер ✅"
        )

        # Цикл: распознаём → показываем превью → если «найти ещё раз»,
        # повторяем захват. Максимум несколько попыток, чтобы не зациклить.
        for attempt in range(5):
            template = await self._capture_and_learn(hwnd, baseline)
            if template is None:
                await self.tg.send(
                    "❌ Не получилось распознать табличку смерти автоматически.\n"
                    "Убедись, что табличка воскрешения целиком видна на экране."
                )
                retry = await self._wait_for_button(
                    "death_retry_fail",
                    "Попробовать ещё раз?",
                    "🔄 Попробовать снова",
                    extra_button=("⏭ Отмена", "death_cancel")
                )
                if retry:
                    continue
                return False

            confirmed = await self._confirm_template(template, f"смерть ({version_label})")
            if confirmed:
                key = f"{version}_death"
                if save_template(key, template):
                    await self.tg.send(f"✅ Шаблон смерти ({version_label}) обучён и сохранён!")
                    return True
                else:
                    await self.tg.send("❌ Не удалось сохранить шаблон (ошибка записи файла)")
                    return False
            # «найти ещё раз» — повторяем
            await self.tg.send("🔄 Пересниму. Убедись, что табличка целиком на экране.")

        await self.tg.send("⚠️ Слишком много попыток. Попробуй позже через /retrain.")
        return False

    async def _learn_disconnect_template_impl(self, version: str) -> bool:
        """Воркфлоу обучения шаблона дисконнекта для версии (main/essence).
        Можно пропустить."""
        version_label = version.capitalize()
        hwnd = await self._pick_window_for_learning(version_label)
        if hwnd is None:
            return False
        self._set_training(hwnd)

        baseline = capture_window(hwnd)
        if baseline is not None:
            from window_capture import is_window_blank
            if is_window_blank(baseline):
                await self.tg.send(
                    "⚠️ Окно выглядит пустым (чёрным). Скорее всего оно свёрнуто.\n"
                    "Разверни окно игры и запусти обучение заново через /retrain."
                )
                return False

        proceeded = await self._wait_for_button(
            f"disconnect_{version}",
            f"🔌 Обучение шаблона дисконнекта ({version_label})\n\n"
            "Дисконнект сложно вызвать специально. Если поймаешь момент "
            "разрыва соединения (табличка 'Соединение потеряно' на экране) — "
            "нажми кнопку. Если не получится — можно пропустить.",
            "Дисконнект на экране, готово ✅",
            skip_button=True
        )

        if not proceeded:
            await self.tg.send(f"⏭ Пропущено обучение дисконнекта ({version_label})")
            return False

        for attempt in range(5):
            template = await self._capture_and_learn(hwnd, baseline)
            if template is None:
                await self.tg.send(
                    "❌ Не получилось распознать табличку дисконнекта."
                )
                retry = await self._wait_for_button(
                    "dc_retry_fail",
                    "Попробовать ещё раз?",
                    "🔄 Попробовать снова",
                    extra_button=("⏭ Отмена", "dc_cancel")
                )
                if retry:
                    continue
                return False

            confirmed = await self._confirm_template(template, f"дисконнект ({version_label})")
            if confirmed:
                key = f"{version}_disconnect"
                if save_template(key, template):
                    await self.tg.send(f"✅ Шаблон дисконнекта ({version_label}) обучён и сохранён!")
                    return True
                else:
                    await self.tg.send("❌ Не удалось сохранить шаблон")
                    return False
            await self.tg.send("🔄 Пересниму. Убедись, что табличка целиком на экране.")

        await self.tg.send("⚠️ Слишком много попыток. Попробуй позже через /retrain.")
        return False

    async def _ask_which_version(self) -> Optional[list]:
        """
        Спрашивает, какую версию обучать. Возвращает список версий
        ('main'/'essence') или None если отменили. Показываются только
        версии, которые реально используются персонажами.
        """
        used = set(ch["version"] for ch in self.config.get("characters", []))
        # Если используется только одна версия — не спрашиваем, очевидно
        if len(used) <= 1:
            return list(used) if used else []

        builder = InlineKeyboardBuilder()
        builder.button(text="🔵 Только Main", callback_data="onb:ver_main")
        builder.button(text="🟢 Только Essence", callback_data="onb:ver_essence")
        builder.button(text="🔵🟢 Обе версии", callback_data="onb:ver_both")
        builder.adjust(1)
        await self.tg.send("Какую версию обучаем?", reply_markup=builder.as_markup())

        fut_main = asyncio.get_running_loop().create_future()
        fut_ess = asyncio.get_running_loop().create_future()
        fut_both = asyncio.get_running_loop().create_future()
        self._waiting_futures["ver_main"] = fut_main
        self._waiting_futures["ver_essence"] = fut_ess
        self._waiting_futures["ver_both"] = fut_both

        try:
            done, pending = await asyncio.wait(
                [fut_main, fut_ess, fut_both],
                return_when=asyncio.FIRST_COMPLETED, timeout=600
            )
            for f in pending:
                f.cancel()
            for k in ("ver_main", "ver_essence", "ver_both"):
                self._waiting_futures.pop(k, None)
            if fut_main in done:
                return ["main"]
            if fut_ess in done:
                return ["essence"]
            if fut_both in done:
                return ["main", "essence"]
            return None
        except asyncio.TimeoutError:
            for k in ("ver_main", "ver_essence", "ver_both"):
                self._waiting_futures.pop(k, None)
            return None

    async def run_full_onboarding(self):
        """Полный воркфлоу первого запуска — все нужные шаблоны."""
        await self.tg.send(
            "👋 Привет! Перед началом мониторинга нужно один раз "
            "обучить распознавание табличек смерти и дисконнекта.\n\n"
            "⚠️ Важно: окна игры должны быть РАЗВЁРНУТЫ на экране "
            "(не свёрнуты в панель задач) — со свёрнутого окна не получится "
            "считать картинку.\n\n"
            "Это займёт пару минут — нужно один раз умереть в игре."
        )

        # Спрашиваем, какую версию обучать (если используются обе)
        versions = await self._ask_which_version()
        if versions is None:
            await self.tg.send("Обучение отменено. Запусти заново через /retrain.")
            return
        if not versions:
            await self.tg.send("⚠️ Сначала добавь персонажей в настройках приложения.")
            return

        for version in versions:
            key = f"{version}_death"
            if not has_template(key):
                await self.learn_death_template(version)

        for version in versions:
            key = f"{version}_disconnect"
            if not has_template(key):
                await self.learn_disconnect_template(version)

        await self.tg.send("🎉 Обучение завершено! Используй /start чтобы запустить мониторинг.")

    async def _ask_what_to_retrain(self) -> Optional[str]:
        """Спрашивает, что переобучать: смерть / дисконнект / всё.
        Возвращает 'death' | 'disconnect' | 'both' или None если отменили."""
        builder = InlineKeyboardBuilder()
        builder.button(text="💀 Только смерть", callback_data="onb:what_death")
        builder.button(text="🔌 Только дисконнект", callback_data="onb:what_disconnect")
        builder.button(text="💀🔌 И то, и другое", callback_data="onb:what_both")
        builder.adjust(1)
        await self.tg.send("Что переобучаем?", reply_markup=builder.as_markup())

        fut_d = asyncio.get_running_loop().create_future()
        fut_dc = asyncio.get_running_loop().create_future()
        fut_b = asyncio.get_running_loop().create_future()
        self._waiting_futures["what_death"] = fut_d
        self._waiting_futures["what_disconnect"] = fut_dc
        self._waiting_futures["what_both"] = fut_b
        try:
            done, pending = await asyncio.wait(
                [fut_d, fut_dc, fut_b],
                return_when=asyncio.FIRST_COMPLETED, timeout=600
            )
            for f in pending:
                f.cancel()
            for k in ("what_death", "what_disconnect", "what_both"):
                self._waiting_futures.pop(k, None)
            if fut_d in done:
                return "death"
            if fut_dc in done:
                return "disconnect"
            if fut_b in done:
                return "both"
            return None
        except asyncio.TimeoutError:
            for k in ("what_death", "what_disconnect", "what_both"):
                self._waiting_futures.pop(k, None)
            return None

    async def run_retrain(self):
        """
        Переобучение с выбором версии И типа. Удаляет и переобучает ТОЛЬКО
        выбранное — остальные шаблоны не трогает. Так можно переобучить,
        например, только дисконнект Essence, не задев Main и не потеряв
        рабочие шаблоны.
        """
        # 1. Какую версию
        versions = await self._ask_which_version()
        if versions is None:
            await self.tg.send("Переобучение отменено.")
            return
        if not versions:
            await self.tg.send("⚠️ Сначала добавь персонажей в настройках приложения.")
            return

        # 2. Что именно (смерть/дисконнект/оба)
        what = await self._ask_what_to_retrain()
        if what is None:
            await self.tg.send("Переобучение отменено.")
            return

        do_death = what in ("death", "both")
        do_dc = what in ("disconnect", "both")

        await self.tg.send(
            "⚠️ Окна игры должны быть РАЗВЁРНУТЫ на экране.\n"
            "Начинаю переобучение выбранного."
        )

        # 3. Переобучаем только выбранные комбинации. Удаление старого
        # шаблона происходит внутри обучения через save_template (перезапись),
        # поэтому отдельно ничего не сносим — невыбранное остаётся целым.
        if do_death:
            for version in versions:
                await self.learn_death_template(version)
        if do_dc:
            for version in versions:
                await self.learn_disconnect_template(version)

        await self.tg.send("✅ Переобучение завершено.")
