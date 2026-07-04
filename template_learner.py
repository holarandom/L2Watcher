# template_learner.py
"""
Самообучение шаблонов диалогов (смерть / дисконнект).

Идея: вместо ручных координат, ищем диалог автоматически —
1. Берём несколько кадров подряд
2. Находим области, которые НЕ меняются между кадрами
   (диалог статичен, игровой мир за ним анимирован/двигается)
3. Среди статичных зон ищем прямоугольный контур с однородным
   тёмным фоном — это и есть диалоговое окно
4. Вырезаем его и сохраняем как шаблон

Человеку нужно только: умереть в игре (или поймать дисконнект),
не указывая никаких координат руками.
"""
import cv2
import numpy as np
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def capture_diff_mask(frame1: np.ndarray, frame2: np.ndarray, threshold: int = 12) -> np.ndarray:
    """
    Возвращает маску статичных пикселей (там, где кадры ПОЧТИ одинаковы).
    255 = статичный пиксель (потенциально UI), 0 = изменился (игровой мир).
    """
    if frame1.shape != frame2.shape:
        raise ValueError("Кадры разного размера")

    diff = cv2.absdiff(frame1, frame2)
    gray_diff = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    # Статичные зоны — разница меньше порога
    _, static_mask = cv2.threshold(gray_diff, threshold, 255, cv2.THRESH_BINARY_INV)
    return static_mask


def find_dialog_candidates(frame: np.ndarray, static_mask: np.ndarray) -> list:
    """
    Ищет диалог через скользящее окно по карте плотности статичных пикселей,
    а НЕ через связные компоненты (connected components).

    Почему не связные компоненты: статичный игровой фон (земля, скалы,
    стены) — это ОГРОМНАЯ статичная область сама по себе, физически
    соединённая с диалогом через множество мелких статичных промежутков
    между движущимися объектами (мобы, эффекты). cv2.findContours на такой
    маске даёт один гигантский контур на весь экран. Нужен другой подход:
    ищем компактные прямоугольники, где ПЛОТНОСТЬ статичных пикселей
    заметно выше среднего, а внутренняя структура однородна по яркости —
    это отличает плотную сплошную плашку диалога от дырявого "кружевного"
    статичного фона с дырками от движущихся объектов.
    """
    h_frame, w_frame = frame.shape[:2]

    # Используем box filter (быстрый локальный усреднитель) чтобы получить
    # карту локальной плотности статичных пикселей в каждом окне
    mask_f = (static_mask > 0).astype(np.float32)

    # Несколько размеров окна — диалоги бывают разных размеров
    candidates = []
    window_sizes = [
        (int(w_frame * 0.08), int(h_frame * 0.12)),
        (int(w_frame * 0.12), int(h_frame * 0.18)),
        (int(w_frame * 0.16), int(h_frame * 0.24)),
    ]

    for win_w, win_h in window_sizes:
        if win_w < 20 or win_h < 20:
            continue
        density_map = cv2.boxFilter(mask_f, -1, (win_w, win_h), normalize=True)

        # Ищем локальные максимумы плотности выше порога
        density_threshold = 0.92  # окно почти полностью статично
        _, density_thresh_map = cv2.threshold(density_map, density_threshold, 1.0, cv2.THRESH_BINARY)
        density_thresh_map = (density_thresh_map * 255).astype(np.uint8)

        contours, _ = cv2.findContours(density_thresh_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            cx, cy, cw, ch = cv2.boundingRect(cnt)
            # Центр найденной зоны высокой плотности — берём окно вокруг него
            center_x = cx + cw // 2
            center_y = cy + ch // 2
            x = max(0, center_x - win_w // 2)
            y = max(0, center_y - win_h // 2)
            w = min(win_w, w_frame - x)
            h = min(win_h, h_frame - y)

            if w < 20 or h < 20:
                continue

            roi = frame[y:y+h, x:x+w]
            gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            std_dev = float(np.std(gray_roi))

            actual_density = mask_f[y:y+h, x:x+w].mean()

            center_dist = np.hypot((x + w/2) - w_frame/2, (y + h/2) - h_frame/2)
            center_score = 1.0 - min(center_dist / (w_frame/2), 1.0)
            homogeneity_score = 1.0 / (1.0 + std_dev / 30.0)

            score = (actual_density * 0.5) + (center_score * 0.25) + (homogeneity_score * 0.25)
            candidates.append((x, y, w, h, score))

    if not candidates:
        return []

    # Убираем сильно перекрывающиеся дубликаты (от разных размеров окна) —
    # оставляем лучший score среди перекрывающихся
    candidates.sort(key=lambda c: c[4], reverse=True)
    final = []
    for cand in candidates:
        x, y, w, h, score = cand
        overlaps = False
        for fx, fy, fw, fh, _ in final:
            ox = max(0, min(x+w, fx+fw) - max(x, fx))
            oy = max(0, min(y+h, fy+fh) - max(y, fy))
            overlap_area = ox * oy
            if overlap_area > 0.5 * min(w*h, fw*fh):
                overlaps = True
                break
        if not overlaps:
            final.append(cand)

    return final


def detect_dialog_region(
    frames: list,
    diff_threshold: int = 12,
    baseline_frame: Optional[np.ndarray] = None
) -> Optional[Tuple[int, int, int, int]]:
    """
    Принимает список кадров (минимум 2, желательно 3-4 с интервалом ~0.5-1с)
    СДЕЛАННЫХ ПОСЛЕ появления диалога (когда персонаж уже умер/дисконнектнулся).

    baseline_frame — опциональный кадр СДЕЛАННЫЙ ДО события (персонаж жив,
    диалога нет). Используется как МЯГКАЯ подсказка через скоринг кандидатов,
    а не как жёсткий фильтр — потому что тёмный диалог может случайно
    совпасть по цвету с тёмным игровым фоном на baseline (например ночь,
    подземелье), что дало бы ложно-низкую "разницу" именно там, где
    диалог и находится. Жёсткий AND-фильтр в такой ситуации исключал бы
    из рассмотрения именно правильную область.

    Возвращает (x, y, w, h) лучшей кандидатной области диалога, либо None.
    """
    if len(frames) < 2:
        logger.error("Нужно минимум 2 кадра для детекта диалога")
        return None

    # Комбинируем маски статичности по всем парам кадров —
    # область должна быть статичной во ВСЕХ парах, не только в одной
    combined_mask = None
    for i in range(len(frames) - 1):
        mask = capture_diff_mask(frames[i], frames[i + 1], diff_threshold)
        combined_mask = mask if combined_mask is None else cv2.bitwise_and(combined_mask, mask)

    candidates = find_dialog_candidates(frames[-1], combined_mask)

    if baseline_frame is not None and candidates:
        # Мягкий бонус к score, если область также отличается от baseline —
        # но отсутствие этого совпадения не исключает кандидата совсем
        changed_from_baseline = cv2.bitwise_not(
            capture_diff_mask(baseline_frame, frames[-1], diff_threshold)
        )
        boosted = []
        for (x, y, w, h, score) in candidates:
            region_changed = changed_from_baseline[y:y+h, x:x+w]
            changed_ratio = (region_changed > 0).mean() if region_changed.size > 0 else 0
            # Небольшой бонус (до +0.15), не штраф — если совпадает с baseline,
            # просто не получает бонуса, но и не отбрасывается
            boosted_score = score + changed_ratio * 0.15
            boosted.append((x, y, w, h, boosted_score))
        boosted.sort(key=lambda c: c[4], reverse=True)
        candidates = boosted

    if not candidates:
        logger.warning("Не найдено кандидатов на диалоговое окно")
        return None

    best = candidates[0]
    x, y, w, h, score = best
    logger.info(f"Найден диалог: x={x}, y={y}, w={w}, h={h}, score={score:.3f}")
    return (x, y, w, h)


def extract_template(frame: np.ndarray, region: Tuple[int, int, int, int]) -> np.ndarray:
    """Вырезает область из кадра по координатам."""
    x, y, w, h = region
    return frame[y:y+h, x:x+w].copy()


def validate_template_quality(template: np.ndarray, min_size: int = 60) -> Tuple[bool, str]:
    """
    Базовая проверка качества вырезанного шаблона перед сохранением.
    Возвращает (ok, причина_если_не_ok).
    """
    h, w = template.shape[:2]
    if w < min_size or h < min_size:
        return False, f"Слишком маленький шаблон ({w}x{h})"

    gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    std_dev = float(np.std(gray))
    if std_dev < 3:
        return False, "Шаблон почти однотонный — похоже, что это не диалог"

    return True, ""


def learn_template_from_window(
    capture_fn,
    baseline_frame: Optional[np.ndarray] = None,
    n_frames: int = 3,
    interval_sec: float = 0.6
) -> Optional[np.ndarray]:
    """
    Высокоуровневая функция самообучения — делает реальные скриншоты окна
    через переданную capture_fn (например GameWindow.capture_window),
    с задержками между ними, и возвращает готовый шаблон.

    capture_fn: функция без аргументов, возвращающая np.ndarray (BGR) или None
    baseline_frame: кадр "до события" — если есть, сильно повышает точность
    n_frames: сколько кадров "после события" снять (минимум 2)
    interval_sec: пауза между кадрами в секундах

    Возвращает готовый шаблон (np.ndarray) или None если не получилось.
    """
    import time

    frames = []
    for i in range(max(n_frames, 2)):
        frame = capture_fn()
        if frame is None:
            logger.error(f"Не удалось захватить кадр {i+1}/{n_frames}")
            return None
        frames.append(frame)
        if i < n_frames - 1:
            time.sleep(interval_sec)

    region = detect_dialog_region(frames, baseline_frame=baseline_frame)
    if region is None:
        return None

    template = extract_template(frames[-1], region)
    ok, reason = validate_template_quality(template)
    if not ok:
        logger.warning(f"Шаблон не прошёл проверку качества: {reason}")
        return None

    return template
