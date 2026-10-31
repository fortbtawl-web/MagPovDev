import { useCallback, useEffect, useRef, useState } from "react";
import { call } from "../api";
import { errText } from "../../shared/api";
import { fmtDateTime } from "../../shared/format";
import { Badge, Empty, ErrorBox, Panel, useMutate, type Tone } from "../components";
import { Icon } from "../Icon";
import { useAdmin } from "../ctx";

export interface Broadcast {
  id: number; kind: "single" | "all" | "maintenance_down" | "maintenance_up"; kind_label: string;
  target_user_id: number | null; target_name: string | null; text: string;
  status: "queued" | "running" | "done" | "cancelled" | "failed"; status_label: string;
  total: number; sent: number; failed: number; blocked: number;
  created_by: number | null; created_at: string; started_at: string | null; finished_at: string | null; error: string | null;
}
export interface BroadcastList { items: Broadcast[]; recipients_all: number }

export const MAX_TEXT = 4000;
const isActive = (b: Broadcast) => b.status === "queued" || b.status === "running";

const STATUS_TONE: Record<Broadcast["status"], Tone> = { queued: "warn", running: "warn", done: "ok", cancelled: "neutral", failed: "err" };

/** Список рассылок; пока есть активные — опрашивает сервер раз в 3 секунды. */
export function useBroadcasts() {
  const [data, setData] = useState<BroadcastList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const reload = useCallback(async () => {
    try {
      const d = await call<BroadcastList>("GET", "/api/admin/broadcasts?limit=30");
      if (alive.current) { setData(d); setError(null); }
    } catch (e) { if (alive.current) setError(errText(e)); }
  }, []);
  useEffect(() => { alive.current = true; void reload(); return () => { alive.current = false; }; }, [reload]);
  const active = !!data?.items.some(isActive);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void reload(), 3000);
    return () => window.clearInterval(t);
  }, [active, reload]);
  return { data, error, reload };
}

/** Следит за созданными рассылками и один раз сообщает итог, когда рассылка завершилась. */
export function useDoneToast(items: Broadcast[] | undefined) {
  const { notify } = useAdmin();
  const watched = useRef(new Set<number>());
  useEffect(() => {
    if (!items) return;
    for (const b of items) {
      if (!watched.current.has(b.id)) continue;
      if (b.status === "done") {
        watched.current.delete(b.id);
        notify(`Рассылка завершена: отправлено ${b.sent}, ошибок ${b.failed}, заблокировали бота ${b.blocked}`);
      } else if (b.status === "failed" || b.status === "cancelled") watched.current.delete(b.id);
    }
  }, [items, notify]);
  return useCallback((id: number | undefined) => { if (id !== undefined) watched.current.add(id); }, []);
}

/** Предпросмотр сообщения так, как его увидит пользователь в Telegram (простой текст). */
export function TgBubble({ text }: { text: string }) {
  return (
    <div className="tg-preview" aria-label="Предпросмотр сообщения">
      <div className="tg-bubble">
        <div className="tg-name">SpirittVPN</div>
        {text.trim() ? <div className="tg-text">{text}</div> : <div className="tg-text tg-empty">Текст сообщения появится здесь</div>}
        <div className="tg-time">сейчас</div>
      </div>
    </div>
  );
}

export function Counter({ n }: { n: number }) {
  return <small className={n > MAX_TEXT - 200 ? "err" : "muted"}>{n} / {MAX_TEXT}</small>;
}

function Progress({ b }: { b: Broadcast }) {
  const pct = b.total > 0 ? Math.min(100, Math.round(((b.sent + b.failed + b.blocked) / b.total) * 100)) : 0;
  return (
    <div className="prog-cell">
      <span>{b.sent} / {b.total}</span>
      {isActive(b) && <div className="prog" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><i style={{ width: `${pct}%` }} /></div>}
    </div>
  );
}

export function BroadcastJournal({ items, error, onChanged, title = "Журнал рассылок" }: {
  items: Broadcast[] | null; error: string | null; onChanged: () => void | Promise<void>; title?: string;
}) {
  const mutate = useMutate();
  const [open, setOpen] = useState<number | null>(null);
  const [stopping, setStopping] = useState<number | null>(null);

  async function stop(b: Broadcast) {
    setStopping(b.id);
    try {
      await mutate("Отменить рассылку", [
        { name: "info", label: "Рассылка", type: "text", value: `#${b.id} · ${b.kind_label} · отправлено ${b.sent} из ${b.total}`, readOnly: true },
      ], (v) => call("POST", `/api/admin/broadcasts/${b.id}/cancel`, { reason: v.reason }), onChanged, "Рассылка остановлена", { reasonOptional: true });
    } finally { setStopping(null); }
  }

  const recipient = (b: Broadcast) => b.kind === "single"
    ? `${b.target_name || "Пользователь"} (${b.target_user_id})` : "Все пользователи";

  return (
    <Panel title={title} actions={<button className="btn" onClick={() => void onChanged()}><Icon name="refresh" size={16} />Обновить</button>}>
      {error && <ErrorBox text={error} onRetry={() => void onChanged()} />}
      {!error && items === null && <Empty text="Загрузка…" />}
      {items && items.length === 0 && <Empty text="Рассылок пока не было" icon="megaphone" />}
      {items && items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Дата</th><th>Тип</th><th>Получатели</th><th>Прогресс</th><th>Ошибки</th><th>Заблок. бота</th><th>Статус</th></tr></thead>
            <tbody>
              {items.map((b) => (
                <FragmentRow key={b.id} b={b} open={open === b.id} onToggle={() => setOpen(open === b.id ? null : b.id)}
                  recipient={recipient(b)} stopping={stopping === b.id} onStop={() => void stop(b)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function FragmentRow({ b, open, onToggle, recipient, stopping, onStop }: {
  b: Broadcast; open: boolean; onToggle: () => void; recipient: string; stopping: boolean; onStop: () => void;
}) {
  return (
    <>
      <tr className="click" onClick={onToggle} tabIndex={0} aria-expanded={open}
        onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onToggle(); } }}>
        <td data-label="Дата">{fmtDateTime(b.created_at)}</td>
        <td data-label="Тип">{b.kind_label}</td>
        <td data-label="Получатели">{recipient}</td>
        <td data-label="Прогресс"><Progress b={b} /></td>
        <td data-label="Ошибки">{b.failed}</td>
        <td data-label="Заблок. бота">{b.blocked}</td>
        <td data-label="Статус">
          <div className="status-cell">
            <Badge tone={STATUS_TONE[b.status]}>{b.status_label}</Badge>
            {isActive(b) && b.kind !== "single" && (
              <button className="btn danger" disabled={stopping} onClick={(e) => { e.stopPropagation(); onStop(); }}>Остановить</button>
            )}
          </div>
        </td>
      </tr>
      {open && (
        <tr className="detail-row">
          <td colSpan={7} className="wrap">
            <TgBubble text={b.text} />
            {b.error && <p className="err">Ошибка: {b.error}</p>}
          </td>
        </tr>
      )}
    </>
  );
}
