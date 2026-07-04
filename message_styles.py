# message_styles.py
"""
Стили оформления сообщений Telegram-бота.

Одно и то же событие (смерть, дисконнект, статус) рендерится по-разному
в зависимости от выбранного пользователем стиля. Стиль хранится в конфиге
и меняется либо в окне настроек (вкладка "Оформление"), либо в боте
командой /style.

ВАЖНО про адаптивность: Telegram сам решает, как рисовать текст на разных
клиентах (iOS/Android/Desktop/Web). Мы НЕ используем псевдографику-рамки
(┌─┐), потому что на части телефонов они съезжают из-за разных шрифтов.
Вместо этого — жирный текст, эмодзи-разделители и короткие строки, которые
выглядят одинаково ровно везде. Кнопки — максимум 2 в ряд, чтобы влезали
на узких экранах.

Telegram parse_mode = HTML (поддерживается везде одинаково).
"""

STYLES = ["minimal", "card", "strict", "lineage"]

STYLE_LABELS = {
    "minimal": "Минимал",
    "card": "Карточка",
    "strict": "Строгий",
    "lineage": "Lineage",
}

STYLE_DESCRIPTIONS = {
    "minimal": "Чисто и коротко, ничего лишнего",
    "card": "Структурно: поля события блоком",
    "strict": "Премиум-типографика, много воздуха",
    "lineage": "Атмосферный, в стиле игры",
}


# ── Событийные данные ──
# event_type: "death" | "disconnect" | "window_closed"
# Тексты заголовков по типу события в каждом стиле задаются ниже.

_EVENT_TITLES = {
    "death": {
        "minimal": "💀 Погиб",
        "card": "💀 СМЕРТЬ",
        "strict": "Персонаж погиб",
        "lineage": "💀 Твой герой пал в бою",
    },
    "disconnect": {
        "minimal": "🔌 Дисконнект",
        "card": "🔌 ДИСКОННЕКТ",
        "strict": "Соединение потеряно",
        "lineage": "🔌 Связь с миром прервалась",
    },
    "window_closed": {
        "minimal": "🛑 Окно закрылось",
        "card": "🛑 ОКНО ЗАКРЫЛОСЬ",
        "strict": "Окно игры закрыто",
        "lineage": "🛑 Врата в мир захлопнулись",
    },
}


def format_event(style: str, event_type: str, char_name: str,
                 version: str, time_str: str = "") -> str:
    """Форматирует уведомление о событии в выбранном стиле. Возвращает HTML."""
    style = style if style in STYLES else "card"
    title = _EVENT_TITLES.get(event_type, {}).get(style, event_type)
    ver = version.capitalize()

    if style == "minimal":
        # Голо и коротко
        line = f"<b>{title}</b>\n{char_name} · {ver}"
        if time_str:
            line += f"\n<i>{time_str}</i>"
        return line

    if style == "card":
        # Структурно: поля блоком, выровнено через неразрывные пробелы.
        # Без псевдографики — жирный заголовок + поля с эмодзи.
        lines = [f"<b>{title}</b>", "➖➖➖➖➖➖➖➖"]
        lines.append(f"👤 Персонаж:  <b>{char_name}</b>")
        lines.append(f"🎮 Версия:    {ver}")
        if time_str:
            lines.append(f"🕐 Время:     {time_str}")
        return "\n".join(lines)

    if style == "strict":
        # Премиум: много воздуха, без эмодзи-шума, аккуратная типографика
        lines = [f"<b>{title.upper()}</b>", ""]
        lines.append(f"{char_name} — {ver}")
        if time_str:
            lines.append(f"<i>{time_str}</i>")
        return "\n".join(lines)

    if style == "lineage":
        # Атмосферный, под игру
        sep = "⚔️ ━━━━━━━━━━━━━━"
        lines = [sep, f"   <b>{title}</b>", ""]
        lines.append(f"   🧙 <b>{char_name}</b>  ⟨{ver}⟩")
        if time_str:
            lines.append(f"   🕐 {time_str}")
        lines.append(sep)
        return "\n".join(lines)

    return f"{title}\n{char_name} · {ver}"


def format_status(style: str, windows: list) -> str:
    """
    Форматирует сводку статуса в выбранном стиле.
    windows: список словарей {name, version, state} где
    state ∈ {"alive","dead","disconnect","closed"}.
    """
    style = style if style in STYLES else "card"

    if not windows:
        return {
            "minimal": "Окон нет",
            "card": "<b>📊 СТАТУС</b>\n➖➖➖➖➖➖➖➖\nАктивных окон нет",
            "strict": "<b>СТАТУС</b>\n\nАктивных окон нет",
            "lineage": "⚔️ ━━━━━━━━━━━━━━\n   Отряд пуст",
        }.get(style, "Окон нет")

    alive = [w for w in windows if w["state"] == "alive"]
    dead = [w for w in windows if w["state"] == "dead"]
    disc = [w for w in windows if w["state"] == "disconnect"]
    closed = [w for w in windows if w["state"] == "closed"]
    problems = dead + disc + closed

    def _state_word(state):
        return {"dead": "погиб", "disconnect": "дисконнект",
                "closed": "окно закрыто", "alive": "жив"}.get(state, state)

    if style == "minimal":
        lines = []
        for w in windows:
            icon = {"alive": "🟢", "dead": "💀", "disconnect": "🔌",
                    "closed": "🛑"}.get(w["state"], "•")
            lines.append(f"{icon} {w['name']} · {w['version'].capitalize()}")
        return "\n".join(lines)

    if style == "card":
        lines = ["<b>📊 СТАТУС ПЕРСОНАЖЕЙ</b>", "➖➖➖➖➖➖➖➖"]
        lines.append(f"🟢 Живы:        <b>{len(alive)}</b>")
        if dead:
            lines.append(f"💀 Мёртвы:      <b>{len(dead)}</b>")
        if disc:
            lines.append(f"🔌 Дисконнект:  <b>{len(disc)}</b>")
        if closed:
            lines.append(f"🛑 Закрыты:     <b>{len(closed)}</b>")
        lines.append("")
        if not problems:
            lines.append(f"✅ Все {len(alive)} в порядке")
        else:
            if alive:
                lines.append("🟢 <b>ЖИВЫ</b>")
                for w in alive:
                    lines.append(f"   {w['name']} · {w['version'].capitalize()}")
                lines.append("")
            lines.append("⚠️ <b>ТРЕБУЮТ ВНИМАНИЯ</b>")
            for w in problems:
                lines.append(f"   {w['name']} · {w['version'].capitalize()} — {_state_word(w['state'])}")
        return "\n".join(lines)

    if style == "strict":
        lines = ["<b>СТАТУС ПЕРСОНАЖЕЙ</b>", ""]
        lines.append(f"Живы: {len(alive)}   Проблемы: {len(problems)}")
        lines.append("")
        if not problems:
            lines.append("Все персонажи в порядке.")
        else:
            for w in problems:
                lines.append(f"{w['name']} ({w['version'].capitalize()}) — {_state_word(w['state'])}")
        return "\n".join(lines)

    if style == "lineage":
        sep = "⚔️ ━━━━━━━━━━━━━━"
        lines = [sep, "   <b>📊 ПЕРСОНАЖИ</b>", ""]
        lines.append(f"   🟢 В строю: {len(alive)}")
        if problems:
            lines.append(f"   ☠️ Пали/потеряны: {len(problems)}")
        lines.append("")
        if not problems:
            lines.append("   ✅ Все персонажи в строю")
        else:
            for w in problems:
                icon = {"dead": "💀", "disconnect": "🔌", "closed": "🛑"}.get(w["state"], "•")
                lines.append(f"   {icon} {w['name']} ⟨{w['version'].capitalize()}⟩")
        lines.append(sep)
        return "\n".join(lines)

    return "Статус недоступен"


def style_preview(style: str) -> str:
    """Пример уведомления о смерти в данном стиле — для выбора в /style."""
    return format_event(style, "death", "волк", "main", "20:45:08")
