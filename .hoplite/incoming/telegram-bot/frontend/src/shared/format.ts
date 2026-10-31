const p2 = (n: number) => String(n).padStart(2, "0");

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return `${p2(d.getDate())}.${p2(d.getMonth() + 1)}.${d.getFullYear()}`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return `${fmtDate(iso)} ${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

export function fmtMoney(minor: number): string {
  const rub = Math.floor(Math.abs(minor) / 100);
  const kop = Math.abs(minor) % 100;
  const s = String(rub).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
  return `${minor < 0 ? "-" : ""}${s}${kop ? "," + p2(kop) : ""}\u00a0₽`;
}

export function fmtJson(v: unknown): string {
  if (v === null || v === undefined) return "";
  try { return JSON.stringify(v); } catch { return String(v); }
}

export function toLocalInput(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : new Date();
  return `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}T${p2(d.getHours())}:${p2(d.getMinutes())}`;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
}

/** «N дн. M ч.» из секунд; меньше часа — «меньше часа». */
export function fmtRemaining(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || sec <= 0) return "—";
  const h = Math.floor(sec / 3600);
  if (h < 1) return "меньше часа";
  const d = Math.floor(h / 24);
  return d > 0 ? `${d} дн. ${h % 24} ч.` : `${h} ч.`;
}

/** Локальное смещение браузера, напр. «UTC+03:00». */
export function localOffset(): string {
  const m = -new Date().getTimezoneOffset();
  const a = Math.abs(m);
  return `${m < 0 ? "-" : "+"}${p2(Math.floor(a / 60))}:${p2(a % 60)}`;
}

/** Значение datetime-local → ISO 8601 с локальным смещением браузера для этой даты. */
export function localInputToIso(v: string): string {
  const d = new Date(v);
  const m = -d.getTimezoneOffset();
  const a = Math.abs(m);
  const off = `${m < 0 ? "-" : "+"}${p2(Math.floor(a / 60))}:${p2(a % 60)}`;
  return `${v.length === 16 ? v + ":00" : v}${off}`;
}
