import { useCallback, useEffect, useRef, useState } from "react";
import { api, authenticate, session, setGateListener } from "./api";
import { applyBrand, applySafeArea, tg } from "./telegram";
import { AppCtx, type Tab, type View } from "./ctx";
import type { Me } from "../shared/types";
import { errText } from "../shared/api";
import { Skeleton, Splash } from "./ui";
import { Icon, Logo, type IconName } from "./Icon";
import { playSound, unlockAudio } from "./sound";
import { Gate } from "./screens/Gate";
import { Home } from "./screens/Home";
import { Devices } from "./screens/Devices";
import { DeviceDetail } from "./screens/DeviceDetail";
import { Plans } from "./screens/Plans";
import { OrderStatus } from "./screens/OrderStatus";
import { Bonus } from "./screens/Bonus";
import { Cabinet } from "./screens/Cabinet";
import { Support } from "./screens/Support";
import { SupportNew } from "./screens/SupportNew";
import { SupportThread } from "./screens/SupportThread";
import { Settings } from "./screens/Settings";
import { Tasks } from "./screens/Tasks";

const TABS: { id: Tab; icon: IconName; label: string }[] = [
  { id: "home", icon: "home", label: "Главная" },
  { id: "devices", icon: "device", label: "Устройства" },
  { id: "plans", icon: "plans", label: "Тарифы" },
  { id: "bonus", icon: "gift", label: "Бонусы" },
  { id: "profile", icon: "user", label: "Кабинет" },
];

const MIN_SPLASH_MS = 700;

function applyTheme() {
  if (!tg) return;
  document.documentElement.dataset.scheme = "dark";
  applySafeArea();
  applyBrand();
}

type ToastMsg = { msg: string; kind: "ok" | "err"; n: number };

function Toast({ t, onClose }: { t: ToastMsg | null; onClose: () => void }) {
  return (
    <div className="toast-wrap" aria-live="polite" aria-atomic="true">
      {t && (
        <div key={t.n} className={`toast toast-${t.kind}`} role={t.kind === "err" ? "alert" : "status"} onClick={onClose}>
          <Icon name={t.kind === "ok" ? "check" : "alert"} size={18} />
          <span>{t.msg}</span>
        </div>
      )}
    </div>
  );
}

export function App() {
  const [boot, setBoot] = useState<"nontg" | "loading" | "error" | "ready">(tg?.initData ? "loading" : "nontg");
  const [bootErr, setBootErr] = useState("");
  const [me, setMe] = useState<Me | null>(null);
  const [tab, setTab] = useState<Tab>("home");
  const [view, setView] = useState<View | null>(null);
  const [toastMsg, setToastMsg] = useState<ToastMsg | null>(null);
  const [minDone, setMinDone] = useState(false);
  const [splashGone, setSplashGone] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const seq = useRef(0);

  const toast = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    setToastMsg({ msg, kind, n: ++seq.current });
    playSound(kind === "ok" ? "ok" : "err");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToastMsg(null), 4500);
  }, []);

  const reloadMe = useCallback(async () => {
    try { setMe(await api<Me>("GET", "/api/me")); } catch (e) { toast(errText(e), "err"); }
  }, [toast]);

  useEffect(() => {
    setGateListener(() => { void reloadMe(); });
    return () => setGateListener(null);
  }, [reloadMe]);

  const start = useCallback(async () => {
    setBoot("loading");
    try {
      await authenticate();
      setMe(await api<Me>("GET", "/api/me"));
      setBoot("ready");
    } catch (e) {
      setBootErr(errText(e));
      setBoot("error");
    }
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => setMinDone(true), MIN_SPLASH_MS);
    // Звук разблокируется только первым жестом пользователя
    const onGesture = (e: Event) => {
      unlockAudio();
      if ((e.target as HTMLElement | null)?.closest?.("button, .clickable, .ticket")) playSound("tap");
    };
    document.addEventListener("pointerdown", onGesture, { passive: true });
    return () => { window.clearTimeout(t); document.removeEventListener("pointerdown", onGesture); };
  }, []);

  useEffect(() => {
    if (!tg) return;
    tg.ready();
    tg.expand();
    applyTheme();
    tg.onEvent("themeChanged", applyTheme);
    tg.onEvent("safeAreaChanged", applySafeArea);
    tg.onEvent("contentSafeAreaChanged", applySafeArea);
    if (tg.initData) void start();
  }, [start]);

  useEffect(() => {
    if (!tg) return;
    const bb = tg.BackButton;
    const back = () => setView((v) => (v?.type === "ticket" || v?.type === "support_new" ? { type: "support" } : null));
    if (view) { bb.show(); bb.onClick(back); }
    else bb.hide();
    return () => bb.offClick(back);
  }, [view]);

  useEffect(() => { window.scrollTo(0, 0); }, [tab, view]);

  const showSplash = boot === "loading" || !minDone;
  useEffect(() => {
    if (showSplash) return;
    playSound("appear"); // no-op until audio is unlocked by a gesture
    const t = window.setTimeout(() => setSplashGone(true), 550);
    return () => window.clearTimeout(t);
  }, [showSplash]);
  const splash = splashGone ? null : <Splash leaving={!showSplash} />;

  if (boot === "nontg" && splashGone === false && !minDone) return <Splash leaving={false} />;
  if (boot === "nontg") {
    return (
      <div className="center-screen">
        <Logo size={72} />
        <h1>SpirittVPN</h1>
        <p className="hint">Твоя свобода в сети</p>
        <p>Откройте SpirittVPN через кнопку «Открыть SpirittVPN» в Telegram-боте.</p>
      </div>
    );
  }
  if (boot === "loading") return splash;
  if (boot === "error") {
    return (
      <>
        <div className="center-screen">
          <div className="state-ic"><Icon name="alert" size={22} /></div>
          <h1>Не удалось войти</h1>
          <p className="hint" role="alert">{bootErr}</p>
          <button className="btn" onClick={() => void start()}><Icon name="refresh" size={18} /><span>Повторить</span></button>
        </div>
        {splash}
      </>
    );
  }

  if (me && me.gate && !me.gate.allowed) {
    return (
      <>
        <Gate gate={me.gate} onGate={(g) => { setMe({ ...me, gate: g }); if (g.allowed) void reloadMe(); }} toast={toast} />
        <Toast t={toastMsg} onClose={() => setToastMsg(null)} />
        {splash}
      </>
    );
  }

  const goTab = (t: Tab) => { setView(null); setTab(t); };
  let screen;
  let key: string;
  if (view?.type === "device") { key = `d${view.id}`; screen = <DeviceDetail id={view.id} onBack={() => setView(null)} />; }
  else if (view?.type === "order") { key = `o${view.id}`; screen = <OrderStatus id={view.id} />; }
  else if (view?.type === "support") { key = "support"; screen = <Support />; }
  else if (view?.type === "settings") { key = "settings"; screen = <Settings />; }
  else if (view?.type === "tasks") { key = "tasks"; screen = <Tasks />; }
  else if (view?.type === "support_new") { key = "support_new"; screen = <SupportNew />; }
  else if (view?.type === "ticket") { key = `t${view.id}`; screen = <SupportThread key={view.id} id={view.id} />; }
  else {
    key = tab;
    if (tab === "home") screen = <Home />;
    else if (tab === "devices") screen = <Devices />;
    else if (tab === "plans") screen = <Plans />;
    else if (tab === "bonus") screen = <Bonus />;
    else screen = <Cabinet />;
  }

  return (
    <AppCtx.Provider value={{ me, isAdmin: !!session?.is_admin, reloadMe, toast, goTab, open: setView }}>
      <div className="app">
        <main className="content" key={key}>
          {me ? screen : <Skeleton />}
          {view && !tg && (
            <button className="chip" onClick={() => setView(null)}><Icon name="chevron" size={16} className="flip" /><span>Назад</span></button>
          )}
        </main>
        <nav className="tabbar" aria-label="Разделы">
          <div className="tabbar-in">
            {TABS.map((t) => (
              <button key={t.id} className={!view && tab === t.id ? "tab active" : "tab"} aria-current={!view && tab === t.id ? "page" : undefined} onClick={() => goTab(t.id)}>
                <span className="tab-ic"><Icon name={t.icon} size={20} /></span>
                <span>{t.label}</span>
              </button>
            ))}
          </div>
        </nav>
        <Toast t={toastMsg} onClose={() => setToastMsg(null)} />
      </div>
      {splash}
    </AppCtx.Provider>
  );
}
