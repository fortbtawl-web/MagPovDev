export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 15) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

export const days = (n: number) => `${n} ${plural(n, "день", "дня", "дней")}`;
export const devicesWord = (n: number) => `${n} ${plural(n, "устройство", "устройства", "устройств")}`;

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "long",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function fmtShort(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

export function fmtRemaining(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  if (d === 0 && h === 0) return "менее часа";
  return [d ? days(d) : "", h ? `${h} ${plural(h, "час", "часа", "часов")}` : ""].filter(Boolean).join(" ");
}

export function term(p: { lifetime: boolean; days: number }): string {
  if (p.lifetime) return "Бессрочно";
  if (p.days % 365 === 0) return p.days === 365 ? "1 год" : `${p.days / 365} ${plural(p.days / 365, "год", "года", "лет")}`;
  if (p.days % 30 === 0 && p.days >= 30) {
    const m = p.days / 30;
    return `${m} ${plural(m, "месяц", "месяца", "месяцев")}`;
  }
  return days(p.days);
}
