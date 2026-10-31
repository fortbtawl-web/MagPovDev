import type { ReactNode } from "react";
import { Icon, Logo, type IconName } from "./Icon";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "ok" | "warn" | "err" | "neutral" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function statusTone(status: string): "ok" | "warn" | "err" | "neutral" {
  if (["active", "paid", "done", "succeeded"].includes(status)) return "ok";
  if (["pending", "creating", "revoking", "waiting", "processing", "new"].includes(status)) return "warn";
  if (["failed", "revoked", "expired", "suspended", "canceled", "cancelled", "error"].includes(status)) return "err";
  return "neutral";
}

export function Spinner() {
  return <div className="spinner" role="status" aria-label="Загрузка" />;
}

export function ErrorBox({ text, onRetry }: { text: string; onRetry?: () => void }) {
  return (
    <Card className="err-box">
      <div className="state-ic"><Icon name="alert" size={22} /></div>
      <div role="alert">{text}</div>
      {onRetry && <button className="btn btn-secondary" onClick={onRetry}>Повторить</button>}
    </Card>
  );
}

export function Title({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="title">
      <h1>{children}</h1>
      {sub && <p className="hint">{sub}</p>}
    </div>
  );
}

export function ticketTone(status: string): "ok" | "warn" | "err" | "neutral" {
  if (status === "new") return "warn";
  if (status === "in_progress") return "neutral";
  if (status === "waiting_user") return "err";
  if (status === "resolved") return "ok";
  return "neutral";
}

export function BannedBanner() {
  return <div className="card err-box banned"><Icon name="ban" size={20} /><div><b>Аккаунт заблокирован.</b> Обратитесь в поддержку.</div></div>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="skel-wrap" role="status" aria-label="Загрузка">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skel" style={{ height: i === 0 ? 96 : 64 }} />)}
    </div>
  );
}

export function Empty({ icon = "sparkle", title, text, children }: { icon?: IconName; title: string; text?: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="state-ic"><Icon name={icon} size={22} /></div>
      <b>{title}</b>
      {text && <p className="hint">{text}</p>}
      {children}
    </div>
  );
}

export function Btn({ icon, children, className = "btn", ...rest }: { icon?: IconName; children: ReactNode; className?: string } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={className} {...rest}>{icon && <Icon name={icon} size={18} />}<span>{children}</span></button>;
}

const MERIDIANS = [0, 1, 2, 3, 4, 5];
const PARTICLES = [0, 1, 2, 3, 4, 5, 6, 7];

export function Splash({ leaving }: { leaving: boolean }) {
  return (
    <div className={leaving ? "splash leaving" : "splash"} aria-hidden={leaving}>
      <div className="splash-stage">
        <div className="splash-glow" />
        <div className="splash-orbit">
          {PARTICLES.map((i) => <i key={i} className="sp-dot" style={{ "--i": i } as React.CSSProperties} />)}
        </div>
        <div className="splash-float">
          <svg className="globe" viewBox="0 0 160 160" width="168" height="168" aria-hidden="true">
            <defs>
              <radialGradient id="gl-body" cx="35%" cy="30%" r="80%">
                <stop offset="0" stopColor="#3A6FD8" />
                <stop offset=".55" stopColor="#16296B" />
                <stop offset="1" stopColor="#080D24" />
              </radialGradient>
              <radialGradient id="gl-shine" cx="32%" cy="26%" r="45%">
                <stop offset="0" stopColor="#fff" stopOpacity=".35" />
                <stop offset="1" stopColor="#fff" stopOpacity="0" />
              </radialGradient>
              <linearGradient id="gl-line" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#4C8DFF" />
                <stop offset="1" stopColor="#22D3EE" />
              </linearGradient>
              <clipPath id="gl-clip"><circle cx="80" cy="80" r="70" /></clipPath>
            </defs>
            <circle cx="80" cy="80" r="70" fill="url(#gl-body)" />
            <g clipPath="url(#gl-clip)" fill="none" stroke="url(#gl-line)" strokeWidth="1.1">
              {[-48, -24, 0, 24, 48].map((y) => {
                const rx = Math.sqrt(70 * 70 - y * y);
                return <ellipse key={y} cx="80" cy={80 + y} rx={rx} ry={rx * 0.16} opacity=".42" />;
              })}
              {MERIDIANS.map((i) => (
                <ellipse key={i} className="gl-mer" cx="80" cy="80" rx="70" ry="70" opacity=".6"
                  style={{ "--s": Math.abs(Math.sin((i * Math.PI) / 6)).toFixed(3), "--d": `${-(i * 9) / 6}s` } as React.CSSProperties} />
              ))}
            </g>
            <circle cx="80" cy="80" r="70" fill="url(#gl-shine)" />
            <circle cx="80" cy="80" r="69.5" fill="none" stroke="#22D3EE" strokeOpacity=".5" />
          </svg>
          <div className="splash-logo"><Logo size={64} /></div>
        </div>
      </div>
      <div className="splash-name">Spiritt<span>VPN</span></div>
      <div className="splash-bar"><i /></div>
    </div>
  );
}
