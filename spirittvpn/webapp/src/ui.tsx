import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, type Me } from "./api";
import { clearBackButton, haptic, useBackButton } from "./tg";
import { play } from "./sound";

export type Tab = "home" | "sub" | "devices" | "refs" | "profile" | "admin";

type ToastKind = "ok" | "error" | "info";
type ConfirmOpts = { title: string; text?: string; ok?: string; danger?: boolean };

type AppCtx = {
  me: Me | null;
  refreshMe: () => Promise<void>;
  go: (tab: Tab) => void;
  toast: (text: string, kind?: ToastKind) => void;
  confirm: (opts: ConfirmOpts) => Promise<boolean>;
  fail: (e: unknown) => void;
};

export const Ctx = createContext<AppCtx>(null as unknown as AppCtx);
export const useApp = () => useContext(Ctx);

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Что-то пошло не так. Попробуй ещё раз.";
}

// ---------- data loading ----------

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    try {
      const value = await fn();
      if (id === seq.current) {
        setData(value);
        setError(null);
      }
    } catch (e) {
      if (id === seq.current) setError(errorText(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

/** Guards an async action against double taps. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const run = useCallback(async (fn: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }, []);
  return { busy, run };
}

// ---------- icons ----------

const PATHS: Record<string, string> = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  shield: "M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z",
  devices: "M4 5h12v9H4zM2 17h16M19 8h3v11h-3z",
  users: "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21v-1a6 6 0 0 1 12 0v1M16 3.5a4 4 0 0 1 0 7M22 21v-1a6 6 0 0 0-4-5.6",
  user: "M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM4 21v-1a7 7 0 0 1 14 0v1",
  admin: "M12 2 3 6v6c0 5 3.8 9.4 9 10 5.2-.6 9-5 9-10V6zM9 12l2 2 4-4",
  plus: "M12 5v14M5 12h14",
  copy: "M8 8h11v11H8zM5 16H4V4h12v1",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  swap: "M4 8h13l-3-3M20 16H7l3 3",
  check: "M5 12l5 5 9-10",
  close: "M6 6l12 12M18 6 6 18",
  chevron: "M9 6l6 6-6 6",
  gift: "M3 9h18v4H3zM5 13h14v8H5zM12 9v12M12 9S10 4 7.5 5 8 9 12 9zM12 9s2-5 4.5-4S16 9 12 9z",
  card: "M3 6h18v12H3zM3 10h18M7 15h4",
  doc: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  sound: "M4 9h4l5-4v14l-5-4H4zM16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12",
  bolt: "M13 2 4 14h7l-1 8 9-12h-7z",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18",
  share: "M4 12v7h16v-7M12 3v12M7 8l5-5 5 5",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  wrench: "M14 6a4 4 0 0 0 5 5l-9 9-3-3 9-9a4 4 0 0 0-2-2zM14 6l2-2",
  chat: "M4 5h16v11H9l-5 4z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2",
  ticket: "M3 8a2 2 0 0 0 0 4v4h18v-4a2 2 0 0 0 0-4V4H3zM14 4v12",
};

export function Icon({ name, size = 20 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.help} />
    </svg>
  );
}

// ---------- primitives ----------

export function Button(props: {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "ghost" | "danger" | "soft";
  busy?: boolean;
  disabled?: boolean;
  icon?: string;
  block?: boolean;
  sound?: boolean;
}) {
  const { kind = "primary", busy, disabled, icon, block, sound = kind === "primary" } = props;
  return (
    <button
      type="button"
      className={`btn btn-${kind}${block ? " btn-block" : ""}${busy ? " is-busy" : ""}`}
      disabled={disabled || busy}
      onClick={() => {
        haptic.tap();
        if (sound) play("tap");
        props.onClick?.();
      }}
    >
      {busy ? <span className="spinner" aria-label="Загрузка" /> : icon ? <Icon name={icon} size={18} /> : null}
      <span>{props.children}</span>
    </button>
  );
}

export function Card(props: { children: ReactNode; className?: string; glow?: boolean; delay?: number; onClick?: () => void }) {
  return (
    <section
      className={`card${props.glow ? " card-glow" : ""}${props.onClick ? " card-tap" : ""} ${props.className ?? ""}`}
      style={{ animationDelay: `${(props.delay ?? 0) * 60}ms` }}
      onClick={props.onClick}
    >
      {props.children}
    </section>
  );
}

export function Skeleton({ h = 18, w = "100%", r = 10 }: { h?: number; w?: number | string; r?: number }) {
  return <div className="skeleton" style={{ height: h, width: w, borderRadius: r }} />;
}

export function SkeletonCards({ n = 3 }: { n?: number }) {
  return (
    <div className="stack">
      {Array.from({ length: n }, (_, i) => (
        <div className="card" key={i}>
          <Skeleton h={14} w="40%" />
          <div style={{ height: 12 }} />
          <Skeleton h={26} w="70%" />
          <div style={{ height: 10 }} />
          <Skeleton h={14} />
        </div>
      ))}
    </div>
  );
}

export function Badge({ tone, children }: { tone: "ok" | "warn" | "bad" | "muted" | "accent"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Row({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="row">
      <span className="row-label">{label}</span>
      <span className={`row-value${mono ? " mono" : ""}`}>{value}</span>
    </div>
  );
}

export function ErrorBox({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <div className="card card-error">
      <p>{text}</p>
      {onRetry && (
        <Button kind="soft" icon="refresh" onClick={onRetry} sound={false}>
          Повторить
        </Button>
      )}
    </div>
  );
}

export function Empty({ icon, title, text }: { icon: string; title: string; text?: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon name={icon} size={26} />
      </div>
      <b>{title}</b>
      {text && <p>{text}</p>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="toggle-row">
      <span>
        <b>{label}</b>
        {hint && <small>{hint}</small>}
      </span>
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => { haptic.select(); onChange(e.target.checked); }} />
      <span className="toggle" aria-hidden="true" />
    </label>
  );
}

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const back = () => onClose();
    useBackButton(back);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => {
      clearBackButton(back);
      useBackButton(null);
      window.removeEventListener("keydown", esc);
    };
  }, [open, onClose]);
  if (!open) return null;
  // Portal: animated ancestors would otherwise become the containing block for position:fixed.
  return createPortal(
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="sheet-handle" />
        <div className="sheet-head">
          <h3>{title}</h3>
          <button className="icon-btn" aria-label="Закрыть" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="section-title">
      <h2>{children}</h2>
      {action}
    </div>
  );
}

export function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(() => true, () => false);
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return Promise.resolve(ok);
}
