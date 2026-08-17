import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const MONTHS_RU = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];
const DOW_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export function localTodayIso(): string {
  const date = new Date();
  return (
    `${date.getFullYear()}-` +
    `${String(date.getMonth() + 1).padStart(2, "0")}-` +
    String(date.getDate()).padStart(2, "0")
  );
}

export default function DatePicker(props: {
  value: string;
  onChange: (value: string) => void;
  minDate?: string;
  maxDate?: string;
}) {
  const { value, onChange, minDate, maxDate } = props;
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const base = value || localTodayIso();
  const [viewYear, setViewYear] = useState(Number(base.slice(0, 4)));
  const [viewMonth, setViewMonth] = useState(Number(base.slice(5, 7)) - 1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const calendarRef = useRef<HTMLDivElement>(null);

  useEffect(() => setText(value), [value]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (wrapRef.current?.contains(target)) return;
      if (calendarRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const openCalendar = () => {
    if (wrapRef.current) setAnchor(wrapRef.current.getBoundingClientRect());
    setOpen(true);
  };

  const commitText = (raw: string) => {
    const source = raw.trim();
    let iso = "";
    let match = source.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (match) {
      iso = `${match[1]}-${match[2].padStart(2, "0")}-${match[3].padStart(2, "0")}`;
    } else {
      match = source.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})$/);
      if (match) {
        iso = `${match[3]}-${match[2].padStart(2, "0")}-${match[1].padStart(2, "0")}`;
      }
    }
    const inRange = !((minDate && iso < minDate) || (maxDate && iso > maxDate));
    if (iso && !Number.isNaN(new Date(`${iso}T00:00:00`).getTime()) && inRange) {
      onChange(iso);
      setText(iso);
      setViewYear(Number(iso.slice(0, 4)));
      setViewMonth(Number(iso.slice(5, 7)) - 1);
    } else {
      setText(value);
    }
  };

  const selectDate = (iso: string) => {
    onChange(iso);
    setText(iso);
    setOpen(false);
  };

  const first = new Date(viewYear, viewMonth, 1);
  const startDay = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const today = localTodayIso();
  const cells: Array<string | null> = [];
  for (let index = 0; index < startDay; index += 1) cells.push(null);
  for (let day = 1; day <= daysInMonth; day += 1) {
    cells.push(
      `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`
    );
  }
  const disabled = (iso: string) =>
    (minDate ? iso < minDate : false) || (maxDate ? iso > maxDate : false);

  return (
    <div ref={wrapRef} className="relative">
      <input
        value={text}
        placeholder="ДД.ММ.ГГГГ"
        onChange={(event) => setText(event.target.value)}
        onBlur={() => commitText(text)}
        onKeyDown={(event) => { if (event.key === "Enter") commitText(text); }}
        onFocus={openCalendar}
        className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 pr-7 font-mono text-xs text-slate-200 outline-none transition focus:border-amber-500"
      />
      <button
        type="button"
        tabIndex={-1}
        onClick={() => (open ? setOpen(false) : openCalendar())}
        aria-label="Календарь"
        className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-500 transition hover:text-amber-300"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      </button>
      {open && anchor && createPortal(
        <div
          ref={calendarRef}
          className="w-[240px] rounded-lg border border-slate-700 bg-slate-900 p-2 shadow-2xl"
          style={{
            position: "fixed",
            top: anchor.bottom + 4,
            left: Math.min(Math.max(anchor.left, 8), window.innerWidth - 248),
            zIndex: 50,
          }}
        >
          <div className="mb-1.5 flex items-center justify-between">
            <button type="button" aria-label="Предыдущий месяц"
              onClick={() => {
                if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1); }
                else setViewMonth(viewMonth - 1);
              }}
              className="rounded p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-100">
              ‹
            </button>
            <div className="font-display text-[11px] font-semibold text-slate-200">
              {MONTHS_RU[viewMonth]} <span className="font-mono text-slate-400">{viewYear}</span>
            </div>
            <button type="button" aria-label="Следующий месяц"
              onClick={() => {
                if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1); }
                else setViewMonth(viewMonth + 1);
              }}
              className="rounded p-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-100">
              ›
            </button>
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {DOW_RU.map((day) => (
              <div key={day} className="py-0.5 text-center font-mono text-[9px] uppercase text-slate-500">
                {day}
              </div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((iso, index) => iso === null ? (
              <div key={`empty-${index}`} />
            ) : (
              <button
                key={iso}
                type="button"
                disabled={disabled(iso)}
                onClick={() => selectDate(iso)}
                className={
                  "rounded py-1 font-mono text-[10px] tabular-nums transition-all duration-100 " +
                  (iso === value
                    ? "bg-amber-500/25 font-semibold text-amber-200"
                    : iso === today
                      ? "text-sky-300 ring-1 ring-inset ring-sky-600/50"
                      : "text-slate-300") +
                  (disabled(iso)
                    ? " cursor-not-allowed opacity-25"
                    : " hover:bg-slate-800 hover:text-slate-100 active:scale-90")
                }
              >
                {Number(iso.slice(8, 10))}
              </button>
            ))}
          </div>
          <div className="mt-1.5 flex items-center justify-between border-t border-slate-800 pt-1.5">
            <button type="button" onClick={() => selectDate(today)} disabled={disabled(today)}
              className="rounded px-1.5 py-0.5 font-mono text-[9px] text-sky-300 transition hover:bg-sky-500/10 disabled:opacity-30">
              сегодня
            </button>
            {(minDate || maxDate) && (
              <span className="font-mono text-[8px] text-slate-600">
                {(minDate ?? "") + "…" + (maxDate ?? "")}
              </span>
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}
