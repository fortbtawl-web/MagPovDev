import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Card, ErrorBox, Spinner, Title } from "../ui";
import { errText } from "../../shared/api";
import { haptic } from "../telegram";
import type { TicketThread, TicketsResp } from "../../shared/types";

export function SupportNew() {
  const { open, toast, reloadMe } = useApp();
  const cats = useLoad<TicketsResp>("/api/support/tickets");
  const [category, setCategory] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  if (cats.loading && !cats.data) return <Spinner />;
  if (cats.error || !cats.data) return <ErrorBox text={cats.error ?? "Ошибка"} onRetry={() => void cats.reload()} />;
  const categories = cats.data.categories;
  const cat = category || categories[0]?.key || "";
  const len = text.trim().length;
  const ok = len >= 10 && len <= 4000 && cat !== "";

  async function submit() {
    setBusy(true);
    try {
      const r = await api<TicketThread>("POST", "/api/support/tickets", { category: cat, text: text.trim() });
      haptic("success");
      toast(`Обращение ${r.ticket.number} создано`);
      void reloadMe();
      open({ type: "ticket", id: r.ticket.id });
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  return (
    <>
      <Title>Новое обращение</Title>
      <Card>
        <label className="field">Категория
          <select value={cat} onChange={(e) => setCategory(e.target.value)}>
            {categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </label>
        <label className="field">Опишите проблему
          <textarea rows={7} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} placeholder="Что случилось? Что вы уже пробовали?" />
        </label>
        <p className={len > 0 && len < 10 ? "err-text small" : "hint small"}>{len} / 4000 (минимум 10 символов)</p>
        <button className="btn" disabled={!ok || busy} onClick={() => void submit()}>{busy ? "Отправляем…" : "Отправить"}</button>
        <p className="hint small">Скриншоты и файлы можно отправить в чате с ботом: /support</p>
      </Card>
    </>
  );
}
