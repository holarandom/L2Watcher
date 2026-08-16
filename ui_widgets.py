# ui_widgets.py
"""
Самодельные виджеты для окна настроек: боковая навигация, прокручиваемый
контейнер, сворачиваемая секция.

Вынесены из settings_gui.py отдельным файлом: к настройкам как таковым они
отношения не имеют — это кирпичики интерфейса, которые можно переиспользовать
где угодно. В settings_gui.py осталась только сборка страниц из них.
"""
import tkinter as tk
from tkinter import ttk


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
