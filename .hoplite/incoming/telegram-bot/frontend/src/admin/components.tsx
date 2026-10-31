import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { call } from "./api";
import { errText } from "../shared/api";
import { useAdmin } from "./ctx";
import { Icon, type IconName } from "./Icon";

export type Tone = "ok" | "warn" | "err" | "neutral";

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function tone(status: string): Tone {
  if (["active", "paid", "done", "succeeded", "confirmed", "applied", "true"].includes(status)) return "ok";
  if (["pending", "creating", "revoking", "waiting", "processing", "new", "retry"].includes(status)) return "warn";
  if (["failed", "revoked", "expired", "suspended", "canceled", "cancelled", "error", "reversed"].includes(status)) return "err";
  return "neutral";
}
export function Panel({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      {(title || actions) && <div className="panel-head"><h3>{title}</h3><div className="row">{actions}</div></div>}
      {children}
    </section>
  );
}
export function Stat({ label, value, tone: t, hint }: { label: string; value: ReactNode; tone?: Tone; hint?: string }) {
  return <div className={`stat${t && t !== "neutral" ? ` stat-${t}` : ""}`} title={hint}><span>{label}</span><b>{value}</b></div>;
}
export function Section({ title, icon, children }: { title: string; icon?: IconName; children: ReactNode }) {
  return (
    <div className="section">
      <h4 className="section-title">{icon && <Icon name={icon} size={16} />}{title}</h4>
      {children}
    </div>
  );
}
export function Empty({ text = "Нет данных", icon = "info" }: { text?: string; icon?: IconName }) {
  return <div className="empty"><Icon name={icon} size={22} /><span>{text}</span></div>;
}

/** Таблица: на узких экранах превращается в карточки; подписи колонок проставляются в data-label. */
export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: boolean }) {
  const ref = useRef<HTMLTableElement>(null);
  useLayoutEffect(() => {
    ref.current?.querySelectorAll("tbody tr").forEach((tr) => {
      Array.from(tr.children).forEach((td, i) => {
        const l = head[i];
        if (l) td.setAttribute("data-label", l); else td.removeAttribute("data-label");
      });
    });
  });
  return (
    <div className="table-wrap">
      {!empty && (
        <table ref={ref}>
          <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
          <tbody>{children}</tbody>
        </table>
      )}
      {empty && <Empty />}
    </div>
  );
}

export function useData<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const load = useCallback(async () => {
    if (path === null) return;
    setLoading(true); setError(null);
    try { setData(await call<T>("GET", path)); } catch (e) { setError(errText(e)); }
    setLoading(false);
  }, [path]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, loading, reload: load };
}

export function Skeleton() {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Загрузка">
      <i /><i /><i />
    </div>
  );
}
export function ErrorBox({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="alert" role="alert">
      <Icon name="alert" /><span className="grow">{text}</span>
      {onRetry && <button className="btn" onClick={onRetry}><Icon name="refresh" size={16} />Повторить</button>}
    </div>
  );
}

export function Load<T>({ res, children }: { res: ReturnType<typeof useData<T>>; children: (d: T) => ReactNode }) {
  if (res.error) return <ErrorBox text={res.error} onRetry={() => void res.reload()} />;
  if (!res.data) return <Skeleton />;
  return <>{children(res.data)}</>;
}

let inflight = 0;
function setBusyFlag(d: number) {
  inflight += d;
  if (inflight > 0) document.documentElement.setAttribute("data-busy", "1");
  else document.documentElement.removeAttribute("data-busy");
}

/** Мутация с формой подтверждения и причиной. Пока запрос не завершён, повторный запуск блокируется. */
export function useMutate() {
  const { form, notify } = useAdmin();
  const running = useRef(false);
  return useCallback(async <R,>(
    title: string, fields: Parameters<typeof form>[1],
    run: (v: Record<string, string>) => Promise<R>, onDone?: (r: R) => void | Promise<void>, okMsg = "Готово", opts?: { reasonOptional?: boolean },
  ) => {
    if (running.current) return;
    running.current = true;
    try {
      const v = await form(title, fields, opts);
      if (!v) return;
      setBusyFlag(1);
      try {
        const r = await run(v);
        notify(okMsg);
        if (onDone) await onDone(r);
      } catch (e) { notify(errText(e), "err"); }
      finally { setBusyFlag(-1); }
    } finally { running.current = false; }
  }, [form, notify]);
}
