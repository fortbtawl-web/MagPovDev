import { Icon } from "../Icon";
import { useCallback, useEffect, useState } from "react";
import { call } from "../api";
import { Badge, ErrorBox, Load, Panel, Skeleton, Table, useData } from "../components";
import { useAdmin } from "../ctx";
import { errText } from "../../shared/api";
import { fmtDateTime } from "../../shared/format";
import type { AdminThread, AdminTicket, KeyLabel } from "../../shared/types";

const stTone = (s: string): "ok" | "warn" | "err" | "neutral" =>
  s === "new" ? "warn" : s === "waiting_user" ? "err" : s === "resolved" ? "ok" : "neutral";

const STATUSES: KeyLabel[] = [
  { key: "new", label: "Новое" }, { key: "in_progress", label: "В работе" }, { key: "waiting_user", label: "Ожидает ответа пользователя" },
  { key: "resolved", label: "Решено" }, { key: "closed", label: "Закрыто" },
];

export function Support({ param }: { param: string }) {
  return param ? <Thread id={param} /> : <List />;
}

function List() {
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [assigned, setAssigned] = useState("");
  const qs = `status=${encodeURIComponent(status)}&category=${encodeURIComponent(category)}&assigned=${encodeURIComponent(assigned)}`;
  const res = useData<{ tickets: AdminTicket[]; categories: KeyLabel[]; statuses: KeyLabel[] }>(`/api/admin/support/tickets?${qs}`);
  const cats = res.data?.categories ?? [];
  const sts = res.data?.statuses ?? [];
  return (
    <Panel title="Обращения в поддержку" actions={<button className="btn" onClick={() => void res.reload()}>Обновить</button>}>
      <div className="row">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Все статусы</option>
          {sts.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Все категории</option>
          {cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <select value={assigned} onChange={(e) => setAssigned(e.target.value)}>
          <option value="">Любой ответственный</option>
          <option value="me">Назначены мне</option>
          <option value="unassigned">Не назначены</option>
        </select>
      </div>
      <Load res={res}>
        {(d) => (
          <Table head={["№", "Статус", "Категория", "Пользователь", "Тема", "Ответственный", "Обновлено"]} empty={d.tickets.length === 0}>
            {d.tickets.map((t) => (
              <tr key={t.id} className="click" onClick={() => { window.location.hash = `#/support/${t.id}`; }}>
                <td><b>{t.number}</b></td>
                <td><Badge tone={stTone(t.status)}>{t.status_label}</Badge></td>
                <td>{t.category_label}</td>
                <td>{t.user_name} <span className="idtag">{t.user_public_id}</span></td>
                <td className="wrap">{t.last_message_preview ?? t.subject}</td>
                <td>{t.assigned_admin_id ?? "—"}</td>
                <td>{fmtDateTime(t.updated_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Load>
    </Panel>
  );
}

function Thread({ id }: { id: string }) {
  const { notify, me } = useAdmin();
  const [data, setData] = useState<AdminThread | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [internal, setInternal] = useState(false);
  const [newStatus, setNewStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setData(await call<AdminThread>("GET", `/api/admin/support/tickets/${id}`)); setError(null); }
    catch (e) { setError(errText(e)); }
  }, [id]);

  useEffect(() => {
    setData(null);
    void load();
    const t = window.setInterval(() => { if (!document.hidden) void load(); }, 15000);
    return () => window.clearInterval(t);
  }, [load]);

  async function act<R>(run: () => Promise<R>, ok: string, after?: (r: R) => void) {
    setBusy(true);
    try { const r = await run(); notify(ok); after?.(r); await load(); }
    catch (e) { notify(errText(e), "err"); }
    setBusy(false);
  }

  const reply = () => act(
    () => call("POST", `/api/admin/support/tickets/${id}/reply`, { text: text.trim(), internal, ...(newStatus ? { status: newStatus } : {}) }),
    internal ? "Заметка сохранена" : "Ответ отправлен пользователю",
    () => { setText(""); setNewStatus(""); },
  );
  const setStatus = (status: string) => act(() => call("POST", `/api/admin/support/tickets/${id}/status`, { status }), "Статус изменён");
  const assign = (v: string) => act(
    () => call("POST", `/api/admin/support/tickets/${id}/assign`, { admin_id: v === "" ? null : Number(v) }), "Ответственный изменён");

  return (
    <>
      <p><a className="backlink" href="#/support"><Icon name="back" size={16} />К списку</a></p>
      {error && !data && <ErrorBox text={error} onRetry={() => void load()} />}
      {!data && !error && <Skeleton />}
      {data && (
        <>
          <Panel title={`${data.ticket.number} · ${data.ticket.category_label}`}>
            <div className="kvs">
              <div><span>Пользователь</span><b><a href={`#/users/${data.ticket.user_id}`}>{data.ticket.user_name}</a></b></div>
              <div><span>SpirittVPN ID</span><b className="idtag">{data.ticket.user_public_id}</b></div>
              <div><span>Telegram ID</span><b>{data.ticket.user_id}</b></div>
              <div><span>Создано</span><b>{fmtDateTime(data.ticket.created_at)}</b></div>
              <div>
                <span>Статус</span>
                <select value={data.ticket.status} disabled={busy} onChange={(e) => void setStatus(e.target.value)}>
                  {STATUSES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
              </div>
              <div>
                <span>Ответственный</span>
                <select value={data.ticket.assigned_admin_id ?? ""} disabled={busy} onChange={(e) => void assign(e.target.value)}>
                  <option value="">Не назначено</option>
                  {me && <option value={me.admin_id}>Мне</option>}
                  {data.admins.filter((a) => a.user_id !== me?.admin_id).map((a) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}
                </select>
              </div>
            </div>
          </Panel>

          <Panel title="Переписка">
            <div className="chat">
              {data.messages.map((m) => (
                <div key={m.id} className={`bubble ${m.is_internal ? "internal" : m.author_type}`}>
                  {m.is_internal && <div><b className="bubble-tag"><Icon name="lock" size={14} />Внутренняя заметка</b></div>}
                  {m.author_type === "admin" && !m.is_internal && <div className="muted">Администратор{m.author_id ? ` ${m.author_id}` : ""}</div>}
                  {m.text}
                  {m.has_attachment && <div className="muted bubble-tag"><Icon name="clip" size={14} />вложение — откройте обращение в боте</div>}
                  <small>{fmtDateTime(m.created_at)}</small>
                </div>
              ))}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); if (text.trim() && !busy) void reply(); }}>
              <div className="row">
                <label className="row"><input type="radio" checked={!internal} onChange={() => setInternal(false)} /> Ответ пользователю</label>
                <label className="row"><input type="radio" checked={internal} onChange={() => setInternal(true)} /> Внутренняя заметка</label>
              </div>
              <textarea rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={internal ? "Заметка видна только администраторам" : "Ответ будет отправлен пользователю в бота"} />
              <div className="row">
                <select value={newStatus} onChange={(e) => setNewStatus(e.target.value)}>
                  <option value="">Статус не менять</option>
                  {STATUSES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
                <button className="btn primary" disabled={busy || !text.trim()}>{internal ? "Сохранить заметку" : "Отправить ответ"}</button>
              </div>
            </form>
          </Panel>
        </>
      )}
    </>
  );
}
