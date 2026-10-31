import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, Empty, ErrorBox, Skeleton, Title } from "../ui";
import { Icon } from "../Icon";
import { confirmDialog, haptic, openExternal } from "../telegram";
import { playSound } from "../sound";
import { errText } from "../../shared/api";
import { fmtDate } from "../../shared/format";
import type { ClaimResp, CoinsResp, PlansResp, TaskItem, TaskStatus } from "../../shared/types";

const fmtCoins = (n: number) => n.toLocaleString("ru-RU");

// "started" exists only on this device: for manual tasks the user opened the link but hasn't submitted yet.
type Shown = TaskStatus | "started";
const STATUS: Record<Shown, { label: string; tone: "ok" | "warn" | "err" | "neutral" }> = {
  available: { label: "Доступно", tone: "neutral" },
  started: { label: "Выполняется", tone: "warn" },
  pending: { label: "Ожидает проверки", tone: "warn" },
  rejected: { label: "Отклонено", tone: "err" },
  rewarded: { label: "Награда получена", tone: "ok" },
  unavailable: { label: "Недоступно", tone: "neutral" },
};

function RewardBurst({ amount, onDone }: { amount: number; onDone: () => void }) {
  useEffect(() => { const t = window.setTimeout(onDone, 2200); return () => window.clearTimeout(t); }, [onDone]);
  // Portal: the animated screen container has a transform, which would anchor position:fixed to it.
  return createPortal(
    <div className="reward-burst" role="status" aria-live="assertive" onClick={onDone} onAnimationEnd={(e) => { if (e.animationName === "burst-out") onDone(); }}>
      <div className="reward-card">
        <span className="reward-coin"><Icon name="sparkle" size={30} /></span>
        <b>+{fmtCoins(amount)}</b>
        <span>SpirCoin начислены</span>
      </div>
    </div>,
    document.body,
  );
}

function TaskCard({ t, started, busy, onOpen, onClaim, index }: {
  t: TaskItem; started: boolean; busy: boolean; index: number;
  onOpen: () => void; onClaim: () => void;
}) {
  const isLink = t.verify === "telegram";
  const shown: Shown = !isLink && t.status === "available" && started ? "started" : t.status;
  const st = STATUS[shown];
  const canClaim = t.status === "available" || t.status === "rejected";
  const how = isLink ? "Награда начисляется за переход по ссылке." : "Проверку выполняет модератор — награда придёт после подтверждения.";
  return (
    <Card className={`task task-${shown}`}>
      <div className="task-head" style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}>
        <div className="task-title">
          <b>{t.title}</b>
          <Badge tone={st.tone}>{st.label}</Badge>
        </div>
        <span className="task-reward">+{fmtCoins(t.reward)}<small>SpirCoin</small></span>
      </div>
      {t.description && <p className="task-desc">{t.description}</p>}
      {canClaim && <p className="hint small">{how}</p>}
      {t.note && <p className={`small ${t.status === "rejected" ? "err-text" : "hint"}`}>{t.note}</p>}
      {canClaim && (
        isLink ? (
          <button className="btn" disabled={busy} onClick={onClaim}>
            {busy ? <><span className="spinner sm" aria-hidden="true" /><span>Начисляем…</span></>
              : <><Icon name="link" size={18} /><span>Перейти и получить +{fmtCoins(t.reward)}</span></>}
          </button>
        ) : (
        <div className="stack">
          {t.url && (
            <button className="btn btn-secondary" onClick={onOpen}>
              <Icon name="link" size={18} /><span>{started ? "Открыть ещё раз" : "Выполнить"}</span>
            </button>
          )}
          <button className="btn" disabled={busy} onClick={onClaim}>
            {busy ? <><span className="spinner sm" aria-hidden="true" /><span>Отправляем…</span></>
              : <><Icon name="check" size={18} /><span>{t.status === "rejected" ? "Отправить повторно" : "Отправить на проверку"}</span></>}
          </button>
        </div>
        )
      )}
    </Card>
  );
}

export function Tasks() {
  const { toast, reloadMe, me } = useApp();
  const tasks = useLoad<{ tasks: TaskItem[] }>("/api/tasks");
  const coins = useLoad<CoinsResp>("/api/coins");
  const plans = useLoad<PlansResp>("/api/plans");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [started, setStarted] = useState<Set<number>>(() => new Set());
  const [burst, setBurst] = useState<number | null>(null);
  const [buying, setBuying] = useState<string | null>(null);
  const inFlight = useRef(false);
  const closeBurst = useCallback(() => setBurst(null), []);

  if ((tasks.loading && !tasks.data) || (coins.loading && !coins.data)) return <Skeleton rows={4} />;
  if (tasks.error || !tasks.data) return <ErrorBox text={tasks.error ?? "Не удалось загрузить задания"} onRetry={() => { void tasks.reload(); void coins.reload(); }} />;

  const list = tasks.data.tasks;
  const balance = coins.data?.balance ?? 0;
  const open = list.filter((t) => t.status !== "rewarded");
  const done = list.filter((t) => t.status === "rewarded");
  const coinPlans = (plans.data?.plans ?? []).filter((p) => p.price_coins);

  function openTask(t: TaskItem) {
    openExternal(t.url);
    setStarted((s) => new Set(s).add(t.id));
  }

  async function claim(t: TaskItem) {
    if (inFlight.current) return;
    inFlight.current = true;
    // Open synchronously in the click handler so popup blockers / Telegram allow it.
    if (t.verify === "telegram" && t.url) openExternal(t.url);
    setBusyId(t.id);
    try {
      const r = await api<ClaimResp>("POST", `/api/tasks/${t.id}/claim`, {});
      if (r.status === "rewarded" && t.status !== "rewarded") {
        haptic("success");
        playSound("reward");
        setBurst(t.reward);
      } else if (r.status === "pending") {
        haptic("success");
        playSound("complete");
        toast(r.message);
      } else if (r.status === "rewarded") {
        toast(r.message);
      } else {
        haptic("error");
        toast(r.message, "err");
      }
      await Promise.all([tasks.reload(), coins.reload()]);
    } catch (e) {
      haptic("error");
      toast(errText(e), "err");
    } finally {
      inFlight.current = false;
      setBusyId(null);
    }
  }

  async function buy(code: string, title: string, price: number) {
    if (inFlight.current) return;
    if (!(await confirmDialog(`Оформить «${title}» за ${fmtCoins(price)} SpirCoin? Монеты спишутся сразу.`))) return;
    inFlight.current = true;
    setBuying(code);
    // One key per confirmed purchase: a retried request is recognised by the server and not charged twice.
    const key = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9-]/g, "").slice(0, 64);
    try {
      const r = await api<{ replayed: boolean; vpn: { confirmed: boolean; error: string | null } }>("POST", "/api/coins/purchase", { plan_code: code, idempotency_key: key });
      haptic("success");
      playSound("complete");
      toast(r.vpn.confirmed ? "Подписка оформлена" : "Подписка оформлена, подключение создаётся — это может занять пару минут");
      await Promise.all([coins.reload(), reloadMe()]);
    } catch (e) {
      haptic("error");
      toast(errText(e), "err");
    } finally {
      inFlight.current = false;
      setBuying(null);
    }
  }

  return (
    <>
      <Title sub="Выполняйте задания — получайте SpirCoin">Задания</Title>

      <Card className="hero coin-hero">
        <span className="hint small">Ваш баланс</span>
        <b className="coin-balance" key={balance}>{fmtCoins(balance)}<small>SpirCoin</small></b>
        <span className="hint small">Монетами можно оплатить подписку.</span>
      </Card>

      {list.length === 0 ? (
        <Empty icon="sparkle" title="Заданий пока нет" text="Новые задания появятся здесь — загляните позже." />
      ) : (
        <>
          {open.map((t, i) => (
            <TaskCard key={t.id} t={t} index={i} started={started.has(t.id)} busy={busyId === t.id}
              onOpen={() => openTask(t)} onClaim={() => void claim(t)} />
          ))}
          {done.length > 0 && (
            <Card>
              <h2>Выполнено</h2>
              {done.map((t) => (
                <div className="kv" key={t.id}><span><Icon name="check" size={16} className="ok-ic" /> {t.title}</span><b>+{fmtCoins(t.reward)}</b></div>
              ))}
            </Card>
          )}
        </>
      )}

      {coins.data?.enabled && coinPlans.length > 0 && (
        <Card>
          <h2>Оплатить подписку монетами</h2>
          {me?.subscription?.is_lifetime && <p className="hint small">У вас бессрочная подписка — покупать ещё не нужно.</p>}
          {coinPlans.map((p) => {
            const price = p.price_coins ?? 0;
            const short = balance < price;
            return (
              <div className="kv" key={p.code}>
                <span>{p.title}<br /><small>{fmtCoins(price)} SpirCoin{short ? ` · не хватает ${fmtCoins(price - balance)}` : ""}</small></span>
                <button className="chip" disabled={short || buying !== null || !!me?.subscription?.is_lifetime} onClick={() => void buy(p.code, p.title, price)}>
                  {buying === p.code ? "Оформляем…" : "Оформить"}
                </button>
              </div>
            );
          })}
        </Card>
      )}

      {coins.data && coins.data.history.length > 0 && (
        <Card>
          <h2>История SpirCoin</h2>
          {coins.data.history.slice(0, 20).map((h) => (
            <div className="kv" key={h.id}>
              <span>{h.kind_label}<br /><small>{fmtDate(h.created_at)}{h.reason ? ` · ${h.reason}` : ""}</small></span>
              <b className={h.amount > 0 ? "pos" : "neg"}>{h.amount > 0 ? "+" : ""}{fmtCoins(h.amount)}</b>
            </div>
          ))}
        </Card>
      )}

      {burst !== null && <RewardBurst amount={burst} onDone={closeBurst} />}
    </>
  );
}
