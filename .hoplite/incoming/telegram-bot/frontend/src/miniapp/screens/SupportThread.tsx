import { Icon } from "../Icon";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { Badge, Card, ErrorBox, Spinner, ticketTone } from "../ui";
import { fmtDateTime } from "../../shared/format";
import { errText } from "../../shared/api";
import { confirmDialog, haptic } from "../telegram";
import type { Ticket, TicketMessage, TicketThread } from "../../shared/types";

export function SupportThread({ id }: { id: number }) {
  const { toast, reloadMe } = useApp();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [messages, setMessages] = useState<TicketMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<TicketThread>("GET", `/api/support/tickets/${id}`);
      setTicket(r.ticket); setMessages(r.messages); setError(null);
    } catch (e) { setError(errText(e)); }
  }, [id]);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    return () => window.clearInterval(t);
  }, [load]);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [messages.length]);

  if (error && !ticket) return <ErrorBox text={error} onRetry={() => void load()} />;
  if (!ticket) return <Spinner />;
  const closed = ticket.status === "closed";
  const body = text.trim();

  async function send() {
    setBusy(true);
    try {
      const r = await api<{ message: TicketMessage; ticket: Ticket }>("POST", `/api/support/tickets/${id}/messages`, { text: body });
      setMessages((m) => (m.some((x) => x.id === r.message.id) ? m : [...m, r.message]));
      setTicket(r.ticket);
      setText("");
      haptic("success");
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  async function close() {
    if (!(await confirmDialog("Закрыть обращение? Отвечать в нём будет нельзя."))) return;
    setBusy(true);
    try {
      const r = await api<{ ticket: Ticket }>("POST", `/api/support/tickets/${id}/close`, {});
      setTicket(r.ticket);
      toast("Обращение закрыто");
      void reloadMe();
      void load();
    } catch (e) { toast(errText(e), "err"); }
    setBusy(false);
  }

  return (
    <>
      <Card>
        <div className="row-between">
          <h2>{ticket.number}</h2>
          <Badge tone={ticketTone(ticket.status)}>{ticket.status_label}</Badge>
        </div>
        <div className="hint">{ticket.category_label} · {fmtDateTime(ticket.created_at)}</div>
      </Card>

      <div className="chat">
        {messages.map((m) => (
          <div key={m.id} className={`bubble ${m.author}`}>
            {m.author === "support" && <b>Поддержка SpirittVPN</b>}
            {m.text}
            {m.has_attachment && <div className="small"><Icon name="attach" size={14} /> Вложение (доступно в боте)</div>}
            {m.author !== "system" && <small>{fmtDateTime(m.created_at)}</small>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {closed ? (
        <p className="hint center">Обращение закрыто. Чтобы задать новый вопрос, создайте новое обращение.</p>
      ) : (
        <Card>
          <label className="field">Ваш ответ
            <textarea rows={3} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <div className="stack">
            <button className="btn" disabled={busy || body.length < 1} onClick={() => void send()}>Отправить</button>
            <button className="btn btn-danger" disabled={busy} onClick={() => void close()}>Закрыть обращение</button>
          </div>
        </Card>
      )}
    </>
  );
}
