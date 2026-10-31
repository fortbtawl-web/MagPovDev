export function planName(code: string | null): string {
  if (!code) return "—";
  if (code === "trial") return "Пробный период";
  if (code === "bonus") return "Бонусная";
  if (code === "admin") return "Выдана администратором";
  return code;
}
