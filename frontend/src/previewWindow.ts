const DEPTH_MONTHS: Record<string, number> = {
  express: 6,
  serious: 6,
  very_serious: 24,
};

export type PreviewWindowResult =
  | { ok: true; date_from: string; date_to: string }
  | { ok: false; error: string };

function formatIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Map Lab depth / custom period to preview API date bounds. */
export function resolveLabPreviewWindow(
  depth: string,
  dateFrom: string,
  dateTo: string,
  dataRange: { min_date: string | null; max_date: string | null } | null
): PreviewWindowResult {
  if (depth === "custom") {
    if (!dateFrom || !dateTo) {
      return { ok: false, error: "Укажите период «с» и «до» в конструкторе Lab" };
    }
    if (dateFrom > dateTo) {
      return { ok: false, error: "Дата «с» не может быть позже даты «до»" };
    }
    return { ok: true, date_from: dateFrom, date_to: dateTo };
  }

  const months = DEPTH_MONTHS[depth] ?? DEPTH_MONTHS.express;
  const maxDate = dataRange?.max_date;
  if (!maxDate) {
    return { ok: false, error: "Диапазон данных ещё не загружен — обновите страницу" };
  }

  const end = new Date(`${maxDate}T00:00:00`);
  const start = new Date(end);
  start.setMonth(start.getMonth() - months);

  const minDate = dataRange?.min_date;
  if (minDate) {
    const min = new Date(`${minDate}T00:00:00`);
    if (start < min) {
      return { ok: true, date_from: minDate, date_to: maxDate };
    }
  }

  return { ok: true, date_from: formatIsoDate(start), date_to: maxDate };
}
