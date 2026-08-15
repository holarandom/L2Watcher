# settings_gui.py
"""
Окно настроек приложения. Открывается:
- автоматически при первом запуске (если конфиг не заполнен)
- по запросу из трея ("Настройки") в любой момент
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from config_manager import load_config, save_config
import autostart


def _resource_path(filename: str) -> str:
    """Путь к ресурсу рядом с exe/скриптом. В собранном PyInstaller-exe
    файлы распакованы во временную папку sys._MEIPASS."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


class SidebarNav(ttk.Frame):
    """
    Боковая навигация настроек (макет 1b): вертикальный список разделов
    вместо вкладок Notebook. В ttk нет готового пункта навигации, поэтому
    каждый пункт собран из tk.Frame + tk.Label — только так можно покрасить
    фон активного пункта и левую акцентную полосу.
    """

    def __init__(self, parent, colors, on_select, width=220):
        super().__init__(parent, width=width)
        self.pack_propagate(False)
        self._colors = colors
        self._on_select = on_select
        self._items = {}       # key -> (row_frame, accent_bar, label)
        self._active = None

        self._holder = tk.Frame(self, bg=colors["side_bg"], highlightthickness=0, bd=0)
        self._holder.pack(fill="both", expand=True)

    def add(self, key: str, title: str):
        c = self._colors
        row = tk.Frame(self._holder, bg=c["side_bg"], highlightthickness=0, bd=0)
        row.pack(fill="x", padx=6, pady=1)

        # Акцентная полоса слева (2 px) — видна только у активного пункта.
        bar = tk.Frame(row, bg=c["side_bg"], width=2)
        bar.pack(side="left", fill="y")

        lbl = tk.Label(
            row, text=title, bg=c["side_bg"], fg=c["side_fg"],
            anchor="w", padx=12, pady=8, font=("", 10)
        )
        lbl.pack(side="left", fill="x", expand=True)

        for w in (row, lbl, bar):
            w.bind("<Button-1>", lambda _e, k=key: self.select(k))
            w.bind("<Enter>", lambda _e, k=key: self._hover(k, True))
            w.bind("<Leave>", lambda _e, k=key: self._hover(k, False))
            try:
                w.config(cursor="hand2")
            except Exception:
                pass

        self._items[key] = (row, bar, lbl)
        if self._active is None:
            self.select(key)

    def _hover(self, key, entering):
        if key == self._active:
            return
        c = self._colors
        row, bar, lbl = self._items[key]
        bg = c["side_hover"] if entering else c["side_bg"]
        row.config(bg=bg)
        lbl.config(bg=bg)
        bar.config(bg=bg)

    def select(self, key: str):
        c = self._colors
        for k, (row, bar, lbl) in self._items.items():
            if k == key:
                row.config(bg=c["side_active_bg"])
                lbl.config(bg=c["side_active_bg"], fg=c["side_active_fg"])
                bar.config(bg=c["accent"])
            else:
                row.config(bg=c["side_bg"])
                lbl.config(bg=c["side_bg"], fg=c["side_fg"])
                bar.config(bg=c["side_bg"])
        self._active = key
        self._on_select(key)


class ScrollableFrame(ttk.Frame):
    """
    Прокручиваемый контейнер: Canvas + внутренний Frame + скроллбар.
    Нужен, чтобы содержимое раздела могло быть любой высоты — лишнее
    уезжает под скролл, а не за край окна. Это убирает необходимость
    подбирать высоту окна под каждую новую секцию настроек.
    """
    def __init__(self, parent, theme_colors=None):
        super().__init__(parent)
        bg = (theme_colors or {}).get("bg", None)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        if bg:
            self.canvas.config(background=bg)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._bar_shown = False

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        # Внутренний фрейм растягиваем по ширине канваса
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.configure(yscrollcommand=self._scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        # Скроллбар НЕ пакуется сразу: он появляется только когда контент
        # реально не помещается (см. _sync_scrollbar). Раньше полоса висела
        # всегда, даже на полупустых разделах — выглядело неряшливо.

        # Прокрутка колесом мыши, пока курсор над этим контейнером
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, e):
        self.canvas.itemconfig(self._window, width=e.width)
        self._sync_scrollbar()

    def _sync_scrollbar(self):
        """Показывает полосу прокрутки только если контент выше окна.
        Состояние меняется ТОЛЬКО при реальной смене — иначе pack/forget
        меняет ширину канваса, тот шлёт <Configure>, и получается
        бесконечный цикл мигания полосы."""
        try:
            need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 1
        except Exception:
            return
        if need and not self._bar_shown:
            self._scrollbar.pack(side="right", fill="y")
            self._bar_shown = True
        elif not need and self._bar_shown:
            self._scrollbar.pack_forget()
            self._bar_shown = False

    def _bind_wheel(self, _):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        # Если контент помещается целиком — колесо не крутит ничего,
        # иначе канвас "уезжает" и содержимое пропадает вверх.
        if not self._bar_shown:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class CollapsibleSection(ttk.Frame):
    """
    Сворачиваемая секция в стиле настроек Windows 11: заголовок-кнопка
    со стрелкой слева, по клику разворачивает/сворачивает содержимое.
    Контент кладётся в self.body.
    """
    def __init__(self, parent, title, expanded=True):
        super().__init__(parent)
        self._expanded = expanded

        self._header = ttk.Button(
            self, text=self._title_text(title), command=self._toggle,
            style="Section.TButton"
        )
        self._header.pack(fill="x")
        self._title = title

        self.body = ttk.Frame(self)
        if expanded:
            self.body.pack(fill="x", padx=4, pady=(2, 6))

    def _title_text(self, title):
        arrow = "▾" if self._expanded else "▸"
        return f"  {arrow}  {title}"

    def _toggle(self):
        self._expanded = not self._expanded
        self._header.config(text=self._title_text(self._title))
        if self._expanded:
            self.body.pack(fill="x", padx=4, pady=(2, 6))
        else:
            self.body.forget()


class SettingsWindow:
    def __init__(self, on_save_callback=None):
        self.on_save_callback = on_save_callback
        self.cfg = load_config()
        self.characters_data = []  # backing list, синхронизирован с chars_listbox по индексу

        self.root = tk.Tk()
        try:
            from version import APP_NAME, APP_VERSION
            self.root.title(f"{APP_NAME} v{APP_VERSION} — Настройки")
        except Exception:
            self.root.title("L2 Watcher — Настройки")
        # Ширина увеличена относительно версии с вкладками: боковая
        # навигация забирает 200 px, и при старых 600 px поле токена с
        # кнопкой-глазом и кнопка инструкции не помещались в колонку.
        self.root.geometry("820x660")
        self.root.minsize(720, 520)
        self.root.resizable(True, True)

        # Иконка окна настроек (та же монограмма, что в трее и на exe).
        # Сначала .ico — Windows показывает его чётче в панели задач;
        # если .ico нет (запуск из исходников без сборки) — PNG.
        try:
            self.root.iconbitmap(_resource_path("app_icon.ico"))
        except Exception:
            try:
                self._icon_img = tk.PhotoImage(file=_resource_path("tray_icon.png"))
                self.root.iconphoto(True, self._icon_img)
            except Exception:
                pass

        # Тема оформления под систему (тёмная/светлая) — до построения UI,
        # чтобы виджеты сразу создавались в нужной теме.
        import theme as theme_mod
        self.theme, self._sv_ttk_ok = theme_mod.apply_theme(self.root)

        self._build_ui()
        self._load_values()

        # Снимок текущих значений — для отслеживания "были ли изменения".
        # Кнопка Сохранить активна только если что-то реально изменилось
        # относительно этого снимка.
        self._initial_snapshot = self._snapshot_values()
        self._wire_change_tracking()

        # Перехват закрытия окна (крестик) — если есть несохранённые
        # изменения, спросим подтверждение.
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Повторно красим рамку-заголовок после полной сборки окна —
        # на этот момент окно гарантированно существует, и DWM-вызов
        # надёжно применяется (при раннем вызове рамка иногда не успевает).
        if self._sv_ttk_ok:
            self.root.after(50, lambda: theme_mod.set_window_titlebar_dark(
                self.root, dark=(self.theme == "dark")))

    def _snapshot_values(self) -> dict:
        """Снимок всех значений настроек для сравнения 'изменилось ли что-то'."""
        try:
            return {
                "token": self.token_entry.get(),
                "chat_id": self.chatid_entry.get(),
                "characters": [dict(c) for c in self.characters_data],
                "autostart": self.autostart_var.get(),
                "autostart_monitoring": self.autostart_monitoring_var.get(),
                "style": self.style_combo.get(),
                "char_pick_format": self.char_pick_format_var.get(),
                "quiet_enabled": self.quiet_enabled_var.get(),
                "quiet_start": self.quiet_start_entry.get(),
                "quiet_end": self.quiet_end_entry.get(),
                "overlap": self.overlap_var.get(),
            }
        except Exception:
            return {}

    def _has_changes(self) -> bool:
        return self._snapshot_values() != self._initial_snapshot

    def _update_save_state(self, *args):
        """Включает/выключает кнопку Сохранить по факту наличия изменений."""
        try:
            if self._has_changes():
                self.save_btn.config(state="normal")
            else:
                self.save_btn.config(state="disabled")
        except Exception:
            pass

    def _wire_change_tracking(self):
        """Навешивает отслеживание изменений на все поля настроек."""
        # Текстовые поля — по любому изменению содержимого
        for entry in (self.token_entry, self.chatid_entry,
                      self.quiet_start_entry, self.quiet_end_entry):
            entry.bind("<KeyRelease>", self._update_save_state)
        # Чекбоксы и комбобоксы — через trace/событие
        for var in (self.autostart_var, self.autostart_monitoring_var,
                    self.char_pick_format_var,
                    self.quiet_enabled_var, self.overlap_var):
            var.trace_add("write", self._update_save_state)
        for combo in (self.style_combo,):
            combo.bind("<<ComboboxSelected>>", self._update_save_state, add="+")

    def _on_close(self):
        """Закрытие крестиком — спрашиваем, если есть несохранённые изменения."""
        if self._has_changes():
            if not messagebox.askyesno(
                "Несохранённые изменения",
                "Точно выйти без сохранения?\nВсе изменения будут потеряны."
            ):
                return
        self.root.destroy()

    def _theme_colors(self) -> dict:
        """Цвета для tk-виджетов (Listbox, Text), которые sv-ttk не
        стилизует сам. Если sv-ttk НЕ применился — возвращаем светлые цвета,
        чтобы tk-виджеты не были тёмными на светлом стандартном ttk-окне
        (иначе вышел бы уродливый бело-чёрный микс)."""
        if not getattr(self, "_sv_ttk_ok", False):
            return {"bg": "#ffffff", "fg": "#000000", "sel": "#cce4ff"}
        if self.theme == "light":
            return {"bg": "#fbfbfb", "fg": "#202020", "sel": "#cfe8ff"}
        return {"bg": "#1c1c1c", "fg": "#e0e0e0", "sel": "#2f5d8a"}

    def _nav_colors(self) -> dict:
        """Палитра боковой навигации (макет 1b). Акцент #4cc2ff общий для
        обеих тем, фоны подбираются под тёмную/светлую."""
        accent = "#4cc2ff"
        if self.theme == "light" and getattr(self, "_sv_ttk_ok", False):
            return {
                "accent": accent,
                "side_bg": "#f3f3f3",
                "side_hover": "#e8e8e8",
                "side_active_bg": "#dcf0fb",
                "side_fg": "#404040",
                "side_active_fg": "#0b1420",
            }
        if not getattr(self, "_sv_ttk_ok", False):
            return {
                "accent": accent,
                "side_bg": "#f0f0f0",
                "side_hover": "#e4e4e4",
                "side_active_bg": "#dcf0fb",
                "side_fg": "#303030",
                "side_active_fg": "#0b1420",
            }
        return {
            "accent": accent,
            "side_bg": "#1f1e22",
            "side_hover": "#2a2a2f",
            "side_active_bg": "#1e3a4a",
            "side_fg": "#a8a8ad",
            "side_active_fg": "#f2f2f2",
        }

    def _style_tk_widget(self, widget):
        """Применяет цвета темы к tk-виджету (Listbox/Text)."""
        c = self._theme_colors()
        try:
            widget.config(
                background=c["bg"], foreground=c["fg"],
                selectbackground=c["sel"], selectforeground=c["fg"],
                relief="flat", highlightthickness=0,
                borderwidth=0
            )
        except Exception:
            # Text не имеет selectforeground и т.п. — пробуем мягче
            try:
                widget.config(background=c["bg"], foreground=c["fg"],
                              relief="flat", borderwidth=0, highlightthickness=0)
            except Exception:
                pass

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Кнопки Сохранить/Отмена паковываются ПЕРВЫМИ с side="bottom" —
        # это гарантирует, что они всегда останутся видимыми внизу окна,
        # независимо от того, сколько места займут блоки выше (раньше
        # из-за порядка паковки кнопка могла визуально "уезжать" за
        # пределы окна, хотя физически существовала в коде).
        # Нижняя панель ОБЩАЯ для всех разделов — она в self.root, а не
        # внутри страницы, поэтому при переключении разделов не мигает.
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(side="bottom", fill="x", padx=10, pady=10)

        self.save_btn = ttk.Button(btn_frame, text="Сохранить",
                                   command=self._save, state="disabled")
        self.save_btn.pack(side="right", padx=4)

        # Кнопка Сохранить — акцентная. Цвет даёт сам sv_ttk через свой
        # стиль Accent.TButton (#57c8ff, практически совпадает с #4cc2ff
        # из макета). Своя style.configure здесь НЕ ставится: тема sv_ttk
        # перекрывает её собственным layout'ом, строка была бы мёртвой.
        try:
            self.save_btn.config(style="Accent.TButton")
        except Exception:
            pass

        # ── Две колонки: слева навигация, справа страница раздела ──
        # Пакуется ПОСЛЕ кнопок (у тех side="bottom"), поэтому занимает
        # всё оставшееся место, а кнопки всегда видны внизу.
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        nav_colors = self._nav_colors()
        self.sidebar = SidebarNav(body, nav_colors, self._show_page, width=200)
        self.sidebar.pack(side="left", fill="y")

        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)

        # Каждая страница прокручиваемая: содержимое любой высоты уезжает
        # под скролл, а не за край окна.
        self.pages = {}
        page_defs = [
            ("telegram",   "Telegram",        self._build_page_telegram),
            ("characters", "Персонажи",       self._build_page_characters),
            ("templates",  "Шаблоны",         self._build_page_templates),
            ("autostart",  "Автозапуск",      self._build_page_autostart),
            ("quiet",      "Тихий режим",     self._build_page_quiet),
            ("capture",    "Захват окон",     self._build_page_capture),
            ("style",      "Оформление",      self._build_style_tab),
            ("feedback",   "Обратная связь",  self._build_feedback_tab),
        ]

        # Стиль для заголовков секций (используется старым кодом секций)
        try:
            style = ttk.Style()
            style.configure("Section.TButton", anchor="w", font=("", 10, "bold"))
        except Exception:
            pass

        for key, title, builder in page_defs:
            scroll = ScrollableFrame(self.content, theme_colors=self._theme_colors())
            page = scroll.inner
            builder(page)
            self.pages[key] = scroll

        # Пункты навигации добавляем ПОСЛЕ создания всех страниц: add()
        # у первого пункта сразу вызывает select() -> _show_page(), а тот
        # обращается к self.pages. Если добавить раньше — KeyError.
        for key, title, _builder in page_defs:
            self.sidebar.add(key, title)

    def _show_page(self, key: str):
        """Показывает один раздел, прячет остальные."""
        for k, frame in self.pages.items():
            frame.pack_forget()
        self.pages[key].pack(fill="both", expand=True, padx=16, pady=12)

    # ── Страницы разделов ────────────────────────────────────────────────
    def _page_title(self, parent, text):
        ttk.Label(parent, text=text, font=("", 13, "bold")).pack(
            anchor="w", padx=8, pady=(4, 10))

    def _build_page_telegram(self, parent):
        self._page_title(parent, "Telegram (бот и чат)")
        tg_frame = ttk.Frame(parent)
        tg_frame.pack(fill="x", padx=8)

        ttk.Label(tg_frame, text="Токен бота:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        self.token_entry = ttk.Entry(tg_frame, width=42, show="●")
        self.token_entry.grid(row=0, column=1, padx=8, pady=4)

        self.show_token_var = tk.BooleanVar(value=False)
        self.eye_btn = ttk.Button(
            tg_frame, text="👁", width=3,
            command=self._toggle_token_visibility
        )
        self.eye_btn.grid(row=0, column=2, padx=4)

        ttk.Label(tg_frame, text="Chat ID:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        self.chatid_entry = ttk.Entry(tg_frame, width=45)
        self.chatid_entry.grid(row=1, column=1, padx=8, pady=4, columnspan=2, sticky="w")

        ttk.Button(
            tg_frame, text="Как получить токен и chat_id?",
            command=self._show_instructions
        ).grid(row=2, column=0, columnspan=3, pady=6)

    def _build_page_characters(self, parent):
        self._page_title(parent, "Персонажи (окна игры)")
        chars_frame = ttk.Frame(parent)
        chars_frame.pack(fill="both", expand=True, padx=8)

        ttk.Label(
            chars_frame,
            text="Заведи имя для каждого открытого окна игры — любое,\n"
                 "как удобно: \"танк\", \"хилер\", \"иса\", \"волк\" и т.д.\n"
                 "При запуске нового окна бот пришлёт его скриншот и\n"
                 "попросит выбрать персонажа из этого списка.",
            justify="left", foreground="#888"
        ).pack(padx=8, pady=(0, 8), anchor="w")

        list_frame = ttk.Frame(chars_frame)
        list_frame.pack(fill="both", expand=True, padx=8, pady=8)

        self.chars_listbox = tk.Listbox(list_frame, height=8)
        self._style_tk_widget(self.chars_listbox)
        self.chars_listbox.pack(side="left", fill="both", expand=True)
        self.chars_listbox.bind("<<ListboxSelect>>", self._on_char_select)

        scrollbar = ttk.Scrollbar(list_frame, command=self.chars_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.chars_listbox.config(yscrollcommand=scrollbar.set)

        add_frame = ttk.Frame(chars_frame)
        add_frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(add_frame, text="Имя:").grid(row=0, column=0, padx=4)
        self.new_name_entry = ttk.Entry(add_frame, width=15)
        self.new_name_entry.grid(row=0, column=1, padx=4)

        ttk.Label(add_frame, text="Версия:").grid(row=0, column=2, padx=4)
        self.new_version_combo = ttk.Combobox(
            add_frame, values=["main", "essence"], width=10, state="readonly"
        )
        self.new_version_combo.current(0)
        self.new_version_combo.grid(row=0, column=3, padx=4)

        self.add_btn = ttk.Button(add_frame, text="Добавить", command=self._add_character)
        self.add_btn.grid(row=0, column=4, padx=8)

        btn_row2 = ttk.Frame(chars_frame)
        btn_row2.pack(pady=4)
        ttk.Button(btn_row2, text="🗑 Убрать персонажа из списка",
                   command=self._remove_character).pack(side="left", padx=4)

        ttk.Label(
            chars_frame,
            text="💡 Двойной клик по персонажу — изменить его имя или версию",
            foreground="#888"
        ).pack(padx=8, pady=(0, 4), anchor="w")

        # Двойной клик по записи — режим редактирования
        self.chars_listbox.bind("<Double-Button-1>", self._on_char_edit)

        self._editing_index = None  # индекс редактируемой записи (None — режим добавления)

    def _build_page_templates(self, parent):
        self._page_title(parent, "Шаблоны распознавания")
        tmpl_frame = ttk.Frame(parent)
        tmpl_frame.pack(fill="x", padx=8)

        self.tmpl_status_label = ttk.Label(tmpl_frame, text="")
        self.tmpl_status_label.pack(padx=8, pady=4, anchor="w")

        btn_row = ttk.Frame(tmpl_frame)
        btn_row.pack(fill="x", padx=8, pady=4)
        ttk.Button(
            btn_row, text="Переобучить шаблоны заново",
            command=self._show_retrain_info
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            btn_row, text="📂 Открыть папку шаблонов",
            command=self._open_templates_folder
        ).pack(side="left")

    def _build_page_autostart(self, parent):
        self._page_title(parent, "Запуск с Windows")
        auto_frame = ttk.Frame(parent)
        auto_frame.pack(fill="x", padx=8)

        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            auto_frame,
            text="Запускать L2 Watcher автоматически при включении компьютера",
            variable=self.autostart_var
        ).pack(padx=8, pady=8, anchor="w")

        self.autostart_monitoring_var = tk.BooleanVar(
            value=self.cfg.get("autostart_monitoring", False)
        )
        ttk.Checkbutton(
            auto_frame,
            text="Запускать мониторинг сразу (не ждать команду /start в Telegram)",
            variable=self.autostart_monitoring_var
        ).pack(padx=8, pady=(0, 8), anchor="w")


    def _build_feedback_tab(self, parent):
        """Раздел обратной связи: ссылка на фидбек-бота + помощь с логом."""
        pad = {"padx": 10, "pady": 6}

        frame = ttk.LabelFrame(parent, text="Нашёл баг или есть идея?")
        frame.pack(fill="x", **pad)

        ttk.Label(
            frame,
            text="Напиши в Telegram-бот обратной связи — сообщение попадёт\n"
                 "напрямую разработчику. Опиши проблему и, если можешь,\n"
                 "приложи файл лога (кнопка ниже откроет папку с ним).",
            justify="left"
        ).pack(padx=8, pady=(8, 4), anchor="w")

        link = ttk.Label(
            frame, text="🐞 @L2WatcherFeedbackBot — открыть в Telegram",
            foreground="#4a9eff", cursor="hand2"
        )
        link.pack(padx=8, pady=4, anchor="w")
        link.bind("<Button-1>", lambda e: __import__("webbrowser").open(
            "https://t.me/L2WatcherFeedbackBot"))

        def _open_log_folder():
            import os, subprocess
            from config_manager import get_log_path
            folder = os.path.dirname(get_log_path())
            try:
                subprocess.Popen(f'explorer "{folder}"')
            except Exception:
                pass

        ttk.Button(
            frame, text="📂 Открыть папку с логом (l2bot.log)",
            command=_open_log_folder
        ).pack(padx=8, pady=(4, 10), anchor="w")

        ttk.Label(
            frame,
            text="Что писать: какая версия (видна в /health), что делал,\n"
                 "что ожидал и что произошло. Скрин приветствуется.",
            foreground="#888", justify="left"
        ).pack(padx=8, pady=(0, 8), anchor="w")

    def _build_style_tab(self, parent):
        """Раздел выбора стиля оформления сообщений бота с превью."""
        import message_styles
        # ВАЖНО: свой ScrollableFrame здесь больше НЕ создаётся. Каждый
        # раздел настроек уже обёрнут в прокручиваемую страницу в
        # _build_ui — вложенный скролл давал два скроллбара подряд и
        # сплющивал содержимое в узкую полосу.
        pad = {"padx": 10, "pady": 6}

        sel_frame = ttk.LabelFrame(parent, text="Стиль сообщений в Telegram")
        sel_frame.pack(fill="x", **pad)

        ttk.Label(
            sel_frame,
            text="Выбери, как будут выглядеть уведомления и статус в боте.\n"
                 "Этот же выбор можно менять в самом боте командой /style.",
            foreground="#888", justify="left"
        ).pack(padx=8, pady=(8, 4), anchor="w")

        row = ttk.Frame(sel_frame)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Стиль:").pack(side="left", padx=(0, 6))
        self.style_combo = ttk.Combobox(
            row,
            values=[message_styles.STYLE_LABELS[s] for s in message_styles.STYLES],
            width=18, state="readonly"
        )
        cur = self.cfg.get("message_style", "card")
        self.style_combo.set(message_styles.STYLE_LABELS.get(cur, "Карточка"))
        self.style_combo.pack(side="left")
        self.style_combo.bind("<<ComboboxSelected>>", self._on_style_preview)

        # ── Формат выбора персонажа при детекте окна ──
        fmt_frame = ttk.LabelFrame(parent, text="Выбор персонажа при появлении окна")
        fmt_frame.pack(fill="x", **pad)

        ttk.Label(
            fmt_frame,
            text="Как бот предлагает выбрать персонажа для нового окна:",
            foreground="#888"
        ).pack(padx=8, pady=(8, 4), anchor="w")

        self.char_pick_format_var = tk.StringVar(
            value=self.cfg.get("char_pick_format", "A")
        )
        ttk.Radiobutton(
            fmt_frame,
            text="Вариант A: скрин → версия → персонаж",
            variable=self.char_pick_format_var, value="A"
        ).pack(padx=8, pady=(2, 0), anchor="w")
        ttk.Label(
            fmt_frame,
            text="    2 шага. Чище, когда персонажей много.",
            foreground="#888", font=("", 8)
        ).pack(padx=8, pady=(0, 6), anchor="w")

        ttk.Radiobutton(
            fmt_frame,
            text="Вариант B: скрин → сразу все персонажи",
            variable=self.char_pick_format_var, value="B"
        ).pack(padx=8, pady=(2, 0), anchor="w")
        ttk.Label(
            fmt_frame,
            text="    1 шаг. Быстрее, если персонажей немного.",
            foreground="#888", font=("", 8)
        ).pack(padx=8, pady=(0, 8), anchor="w")

        # ── Превью ──
        prev_frame = ttk.LabelFrame(parent, text="Как это выглядит (пример смерти персонажа)")
        prev_frame.pack(fill="both", expand=True, **pad)

        self.style_preview_text = tk.Text(prev_frame, wrap="word", height=12,
                                          relief="flat", borderwidth=0)
        self._style_tk_widget(self.style_preview_text)
        self.style_preview_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._render_style_preview(cur)

    def _label_to_style(self, label: str) -> str:
        import message_styles
        for s, lbl in message_styles.STYLE_LABELS.items():
            if lbl == label:
                return s
        return "card"

    def _render_style_preview(self, style: str):
        """Показывает в превью пример уведомления + статуса для стиля.
        HTML-теги убираем для читаемого вида в tkinter."""
        import message_styles, re
        ev = message_styles.style_preview(style)
        st = message_styles.format_status(style, [
            {"name": "волк", "version": "main", "state": "alive"},
            {"name": "иса", "version": "main", "state": "alive"},
            {"name": "хил", "version": "main", "state": "dead"},
            {"name": "конь", "version": "essence", "state": "disconnect"},
        ])
        # tkinter Text не рендерит HTML — выкидываем теги для превью
        strip = lambda s: re.sub(r"</?[bi]>", "", s)
        text = "── Уведомление ──\n\n" + strip(ev) + "\n\n\n── Статус ──\n\n" + strip(st)
        self.style_preview_text.config(state="normal")
        self.style_preview_text.delete("1.0", tk.END)
        self.style_preview_text.insert("1.0", text)
        self.style_preview_text.config(state="disabled")

    def _on_style_preview(self, event=None):
        self._render_style_preview(self._label_to_style(self.style_combo.get()))

    def _build_page_quiet(self, parent):
        """Раздел Тихий режим — пауза по расписанию."""
        self._page_title(parent, "Тихий режим (пауза по расписанию)")
        quiet_frame = ttk.Frame(parent)
        quiet_frame.pack(fill="x", padx=8)

        qh_cfg = self.cfg.get("quiet_hours", {})

        ttk.Label(
            quiet_frame,
            text="В заданные часы приложение не сканирует окна — не нагружает\n"
                 "процессор и не шумит кулерами. Например, ночью. Время системное.",
            foreground="#888", justify="left"
        ).pack(padx=8, pady=(0, 8), anchor="w")

        self.quiet_enabled_var = tk.BooleanVar(value=qh_cfg.get("enabled", False))
        ttk.Checkbutton(
            quiet_frame, text="Включить тихий режим по расписанию",
            variable=self.quiet_enabled_var
        ).pack(padx=8, pady=4, anchor="w")

        time_row = ttk.Frame(quiet_frame)
        time_row.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Label(time_row, text="С").pack(side="left", padx=(0, 4))
        self.quiet_start_entry = ttk.Entry(time_row, width=7)
        self.quiet_start_entry.insert(0, qh_cfg.get("start", "02:00"))
        self.quiet_start_entry.pack(side="left", padx=(0, 8))
        ttk.Label(time_row, text="по").pack(side="left", padx=(0, 4))
        self.quiet_end_entry = ttk.Entry(time_row, width=7)
        self.quiet_end_entry.insert(0, qh_cfg.get("end", "10:00"))
        self.quiet_end_entry.pack(side="left", padx=(0, 8))
        ttk.Label(time_row, text="(формат ЧЧ:ММ, можно через полночь)",
                  foreground="#888").pack(side="left")

    def _build_page_capture(self, parent):
        """Раздел Захват окон — чтение перекрытых окон."""
        self._page_title(parent, "Захват окон")
        overlap_frame = ttk.Frame(parent)
        overlap_frame.pack(fill="x", padx=8)

        self.overlap_var = tk.BooleanVar(
            value=self.cfg.get("read_overlapped_windows", False)
        )
        ttk.Checkbutton(
            overlap_frame,
            text="Читать перекрытые окна (для мультибокса с наложением)",
            variable=self.overlap_var
        ).pack(padx=8, pady=(0, 0), anchor="w")
        ttk.Label(
            overlap_frame,
            text="    Позволяет мониторить окна, закрытые сверху другими окнами.\n"
                 "    Чуть больше нагрузка на процессор. Свёрнутые окна не\n"
                 "    читаются в любом случае — их нужно держать развёрнутыми.",
            foreground="#888", font=("", 8), justify="left"
        ).pack(padx=8, pady=(0, 8), anchor="w")


    def _open_templates_folder(self):
        import os, sys, subprocess
        from config_manager import get_templates_dir
        path = get_templates_dir()
        try:
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть папку:\n{path}\n\n{e}")

    def _toggle_token_visibility(self):
        shown = not self.show_token_var.get()
        self.show_token_var.set(shown)
        self.token_entry.config(show="" if shown else "●")
        self.eye_btn.config(text="🙈" if shown else "👁")

    def _show_instructions(self):
        import webbrowser
        # Немодальное окно (Toplevel, не messagebox) — чтобы можно было
        # одновременно держать инструкцию открытой И вставлять токен/ID
        # в поля. messagebox блокировал бы основное окно.
        win = tk.Toplevel(self.root)
        win.title("Как получить токен и Chat ID")
        win_w, win_h = 520, 360
        # Ставим окно сбоку от основного — справа, если там есть место,
        # иначе слева. Чтобы инструкция не перекрывала поля ввода.
        self.root.update_idletasks()
        main_x = self.root.winfo_x()
        main_y = self.root.winfo_y()
        main_w = self.root.winfo_width()
        screen_w = self.root.winfo_screenwidth()
        if main_x + main_w + win_w + 10 <= screen_w:
            pos_x = main_x + main_w + 10   # справа от основного
        else:
            pos_x = max(0, main_x - win_w - 10)  # слева
        pos_y = main_y
        win.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        win.transient(self.root)
        try:
            import theme as theme_mod
            theme_mod.set_window_titlebar_dark(win, dark=(self.theme == "dark"))
        except Exception:
            pass

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=16, pady=14)

        def add_line(text, link=None):
            if link:
                lbl = ttk.Label(frame, text=text, foreground="#3b9dff", cursor="hand2")
                lbl.pack(anchor="w", pady=2)
                lbl.bind("<Button-1>", lambda e: webbrowser.open(link))
            else:
                ttk.Label(frame, text=text, justify="left").pack(anchor="w", pady=2)

        add_line("Шаг 1. Создай своего бота:")
        add_line("   • Открой @BotFather  (нажми, чтобы перейти)", "https://t.me/BotFather")
        add_line("   • Напиши ему /newbot и придумай имя")
        add_line("   • Скопируй присланный токен в поле «Токен бота»")
        add_line("")
        add_line("Шаг 2. Узнай свой Chat ID:")
        add_line("   • Открой @userinfobot  (нажми, чтобы перейти)", "https://t.me/userinfobot")
        add_line("   • Напиши ему любое сообщение")
        add_line("   • Скопируй Chat ID в поле «Chat ID»")
        add_line("")
        add_line("Шаг 3. Важно: напиши своему новому боту /start")
        add_line("хотя бы раз — иначе он не сможет писать тебе первым.")

        ttk.Button(frame, text="Понятно", command=win.destroy).pack(pady=(14, 0))

    def _show_retrain_info(self):
        text = (
            "Шаблоны распознавания смерти и дисконнекта создаются\n"
            "автоматически — приложение делает несколько скриншотов\n"
            "в момент события и само вырезает нужную табличку.\n\n"
            "Переобучение запускается из Telegram-бота командой /retrain\n"
            "(или из меню бота → «Переобучить»). Бот по шагам попросит\n"
            "поймать нужное событие в игре для каждой версии (Main/Essence).\n\n"
            "Это удобнее делать из бота, потому что там можно нажимать\n"
            "кнопки прямо в момент, когда персонаж умер или словил дисконнект."
        )
        messagebox.showinfo("Переобучение шаблонов", text)

    def _add_character(self):
        name = self.new_name_entry.get().strip()
        version = self.new_version_combo.get()
        if not name:
            messagebox.showwarning("Ошибка", "Введи имя персонажа")
            return

        # Защита от дублей — два персонажа с одинаковым именем будут
        # путать при выборе кнопками в Telegram (непонятно какой из двух)
        for ch in self.characters_data:
            if ch["name"].lower() == name.lower():
                messagebox.showwarning(
                    "Имя уже используется",
                    f"Персонаж с именем «{name}» уже есть в списке.\n"
                    "Выбери другое имя, или отредактируй существующую запись."
                )
                return

        self.characters_data.append({"name": name, "version": version})
        self.chars_listbox.insert(tk.END, f"{name} — {version}")
        self.new_name_entry.delete(0, tk.END)
        self._update_save_state()

    def _on_char_select(self, event=None):
        """Одиночный клик — просто выделение, ничего не подставляем
        (чтобы не путать). Редактирование — по двойному клику."""
        pass

    def _on_char_edit(self, event=None):
        """Двойной клик по записи — вход в режим редактирования:
        подставляем данные в поля, кнопка 'Добавить' становится 'Сохранить'."""
        sel = self.chars_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        self._editing_index = idx
        ch = self.characters_data[idx]
        self.new_name_entry.delete(0, tk.END)
        self.new_name_entry.insert(0, ch["name"])
        self.new_version_combo.set(ch["version"])
        # Переключаем кнопку в режим сохранения
        self.add_btn.config(text="💾 Сохранить", command=self._update_character)

    def _update_character(self):
        """Сохраняет изменения отредактированной записи."""
        if self._editing_index is None:
            return self._add_character()

        name = self.new_name_entry.get().strip()
        version = self.new_version_combo.get()
        if not name:
            messagebox.showwarning("Ошибка", "Имя не может быть пустым")
            return

        for i, ch in enumerate(self.characters_data):
            if i != self._editing_index and ch["name"].lower() == name.lower():
                messagebox.showwarning(
                    "Имя уже используется",
                    f"Персонаж с именем «{name}» уже есть в списке."
                )
                return

        idx = self._editing_index
        self.characters_data[idx] = {"name": name, "version": version}
        self.chars_listbox.delete(idx)
        self.chars_listbox.insert(idx, f"{name} — {version}")
        self._exit_edit_mode()

    def _exit_edit_mode(self):
        """Возврат из режима редактирования в режим добавления."""
        self._editing_index = None
        self.new_name_entry.delete(0, tk.END)
        self.new_version_combo.current(0)
        self.add_btn.config(text="Добавить", command=self._add_character)
        self._update_save_state()

    def _remove_character(self):
        sel = self.chars_listbox.curselection()
        if sel:
            idx = sel[0]
            self.chars_listbox.delete(idx)
            del self.characters_data[idx]
            self._editing_index = None
            self.new_name_entry.delete(0, tk.END)
            self._update_save_state()

    def _update_template_status(self):
        # Считаем по РЕАЛЬНОМУ наличию файлов шаблонов в папке, а не по
        # флагам templates_ready в конфиге. Так надёжнее: если файлы
        # положили вручную или они остались с прошлого обучения — счётчик
        # это увидит. Флаги в конфиге могли остаться от старой схемы и врать.
        from template_store import TEMPLATE_FILES, has_template

        labels = {
            "main_death": "Смерть (Main)",
            "essence_death": "Смерть (Essence)",
            "main_disconnect": "Дисконнект (Main)",
            "essence_disconnect": "Дисконнект (Essence)",
        }
        present = {k: has_template(k) for k in TEMPLATE_FILES}
        done = sum(1 for v in present.values() if v)
        total = len(present)

        # Детализация: какие именно есть/нет
        details = "\n".join(
            f"   {'✅' if present.get(k) else '❌'} {labels.get(k, k)}"
            for k in TEMPLATE_FILES
        )
        suffix = " ✅" if done == total else " — недостающее обучи через /retrain в боте"
        self.tmpl_status_label.config(
            text=f"Обучено шаблонов: {done}/{total}{suffix}\n{details}"
        )

    # ── Загрузка / сохранение ──────────────────────────────────────────────
    def _load_values(self):
        self.token_entry.insert(0, self.cfg.get("token", ""))
        self.chatid_entry.insert(0, self.cfg.get("chat_id", ""))

        for ch in self.cfg.get("characters", []):
            self.characters_data.append({"name": ch["name"], "version": ch["version"]})
            self.chars_listbox.insert(tk.END, f"{ch['name']} — {ch['version']}")

        self._update_template_status()

    def _validate_time(self, value: str, default: str) -> str:
        """Проверяет формат ЧЧ:ММ. При ошибке возвращает дефолт."""
        try:
            h, m = value.strip().split(":")
            h, m = int(h), int(m)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return f"{h:02d}:{m:02d}"
        except Exception:
            pass
        return default

    def _save(self):
        token = self.token_entry.get().strip()
        chat_id = self.chatid_entry.get().strip()

        if not token or not chat_id:
            messagebox.showwarning("Не всё заполнено", "Заполни токен и Chat ID")
            return

        if not self.characters_data:
            messagebox.showwarning("Не всё заполнено", "Добавь хотя бы одного персонажа")
            return

        self.cfg["token"] = token
        self.cfg["chat_id"] = chat_id
        self.cfg["characters"] = list(self.characters_data)
        self.cfg["autostart_monitoring"] = self.autostart_monitoring_var.get()
        self.cfg["message_style"] = self._label_to_style(self.style_combo.get())
        self.cfg["char_pick_format"] = self.char_pick_format_var.get()
        self.cfg["read_overlapped_windows"] = self.overlap_var.get()

        # ── Тихий режим ──
        self.cfg["quiet_hours"] = {
            "enabled": self.quiet_enabled_var.get(),
            "start": self._validate_time(self.quiet_start_entry.get(), "02:00"),
            "end": self._validate_time(self.quiet_end_entry.get(), "10:00"),
        }


        # Применяем автозагрузку по состоянию чекбокса. Делаем это здесь же,
        # при сохранении — пишем/убираем запись в реестре. Если не удалось
        # (например нет прав или не Windows) — предупреждаем, но не валим
        # сохранение остальных настроек.
        if not autostart.set_enabled(self.autostart_var.get()):
            if self.autostart_var.get():
                messagebox.showwarning(
                    "Автозагрузка",
                    "Не удалось прописать автозагрузку в реестр.\n"
                    "Остальные настройки сохранены."
                )

        if save_config(self.cfg):
            # Без всплывающего "Сохранено" — просто закрываем окно
            # (основное приложение с треем продолжает работать).
            if self.on_save_callback:
                self.on_save_callback(self.cfg)
            self.root.destroy()
        else:
            messagebox.showerror("Ошибка", "Не удалось сохранить настройки")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    SettingsWindow().run()
