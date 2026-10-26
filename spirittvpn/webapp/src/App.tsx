import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Me } from "./api";
import { Ctx, Icon, Sheet, Button, errorText, type Tab } from "./ui";
import { haptic, inTelegram, tg } from "./tg";
import { play, setSoundsEnabled, unlockAudioOnFirstGesture } from "./sound";
import Home from "./screens/Home";
import Subscription from "./screens/Subscription";
import Devices from "./screens/Devices";
import Referrals from "./screens/Referrals";
import Profile from "./screens/Profile";
import Admin from "./screens/Admin";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "home", label: "Главная", icon: "home" },
  { id: "sub", label: "Подписка", icon: "shield" },
  { id: "devices", label: "Устройства", icon: "devices" },
  { id: "refs", label: "Рефералы", icon: "users" },
  { id: "profile", label: "Профиль", icon: "user" },
];

const userId = tg?.initDataUnsafe.user?.id ?? 0;
const ME_KEY = `spiritt.me.${userId}`;
const TAB_KEY = "spiritt.tab";

function initialTab(): Tab {
  if (location.hash === "#admin") return "admin";
  const saved = localStorage.getItem(TAB_KEY) as Tab | null;
  return saved && [...TABS.map((t) => t.id), "admin"].includes(saved) ? saved : "home";
}

function cachedMe(): Me | null {
  try {
    return JSON.parse(localStorage.getItem(ME_KEY) ?? "null");
  } catch {
    return null;
  }
}

type ToastItem = { id: number; text: string; kind: "ok" | "error" | "info" };
type ConfirmState = { title: string; text?: string; ok?: string; danger?: boolean; resolve: (v: boolean) => void };

export default function App() {
  const [me, setMe] = useState<Me | null>(cachedMe);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [confirmState, setConfirm] = useState<ConfirmState | null>(null);
  const [online, setOnline] = useState(navigator.onLine);
  const [splash, setSplash] = useState(true);
  const toastId = useRef(0);

  const refreshMe = useCallback(async () => {
    try {
      const data = await api<Me>("/me");
      setMe(data);
      setLoadError(null);
      setSoundsEnabled(data.settings.sounds);
      localStorage.setItem(ME_KEY, JSON.stringify(data));
    } catch (e) {
      setLoadError(errorText(e));
    }
  }, []);

  const toast = useCallback((text: string, kind: ToastItem["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-2), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  const fail = useCallback((e: unknown) => {
    haptic.error();
    play("error");
    toast(errorText(e), "error");
  }, [toast]);

  const confirm = useCallback(
    (opts: Omit<ConfirmState, "resolve">) => new Promise<boolean>((resolve) => setConfirm({ ...opts, resolve })),
    [],
  );

  const go = useCallback((t: Tab) => {
    haptic.select();
    setTab(t);
    localStorage.setItem(TAB_KEY, t);
    window.scrollTo({ top: 0 });
  }, []);

  useEffect(() => {
    unlockAudioOnFirstGesture(() => play("open"));
    void refreshMe();
    const timer = setTimeout(() => setSplash(false), 650);
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    const onFocus = () => document.visibilityState === "visible" && void refreshMe();
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [refreshMe]);

  if (!inTelegram) {
    return (
      <div className="gate">
        <img src={`${import.meta.env.BASE_URL}logo-384.webp`} alt="SpirittVPN" className="gate-logo" />
        <h1>SpirittVPN</h1>
        <p>Приложение работает внутри Telegram. Открой его кнопкой «🚀 Открыть SpirittVPN» в боте.</p>
      </div>
    );
  }

  const isStaff = Boolean(me?.staff);
  const ctx = { me, refreshMe, go, toast, confirm, fail };

  return (
    <Ctx.Provider value={ctx}>
      {splash && (
        <div className="splash" aria-hidden="true">
          <img src={`${import.meta.env.BASE_URL}logo-384.webp`} alt="" />
        </div>
      )}
      <div className="app">
        {!online && <div className="banner banner-offline">Нет подключения к интернету — показаны последние данные</div>}
        {me?.maintenance && <div className="banner banner-warn">🛠 Идут технические работы. Некоторые действия временно недоступны.</div>}
        <header className="topbar">
          <img src={`${import.meta.env.BASE_URL}logo-192.webp`} alt="SpirittVPN" className="topbar-logo" />
          <div className="topbar-title">
            <b>SpirittVPN</b>
            <span>{me ? `Привет, ${me.user.first_name || "друг"}` : "Загрузка…"}</span>
          </div>
          {isStaff && (
            <button className={`chip${tab === "admin" ? " chip-active" : ""}`} onClick={() => go(tab === "admin" ? "home" : "admin")}>
              <Icon name="admin" size={16} /> Админ
            </button>
          )}
        </header>

        <main className="content" key={tab}>
          {!me && loadError ? (
            <div className="card card-error">
              <p>{loadError}</p>
              <Button kind="soft" icon="refresh" onClick={() => void refreshMe()}>Повторить</Button>
            </div>
          ) : tab === "home" ? <Home /> : tab === "sub" ? <Subscription /> : tab === "devices" ? <Devices />
            : tab === "refs" ? <Referrals /> : tab === "profile" ? <Profile /> : isStaff ? <Admin /> : <Home />}
        </main>

        <nav className="tabbar" aria-label="Навигация">
          {TABS.map((t) => (
            <button key={t.id} className={`tab${tab === t.id ? " tab-active" : ""}`} onClick={() => go(t.id)}
              aria-current={tab === t.id ? "page" : undefined}>
              <Icon name={t.icon} size={22} />
              <span>{t.label}</span>
            </button>
          ))}
        </nav>

        <div className="toasts" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`toast toast-${t.kind}`}>
              <Icon name={t.kind === "error" ? "close" : t.kind === "ok" ? "check" : "bolt"} size={16} />
              {t.text}
            </div>
          ))}
        </div>

        <Sheet open={!!confirmState} title={confirmState?.title ?? ""}
          onClose={() => { confirmState?.resolve(false); setConfirm(null); }}>
          {confirmState?.text && <p className="muted">{confirmState.text}</p>}
          <div className="actions">
            <Button kind="ghost" sound={false} onClick={() => { confirmState?.resolve(false); setConfirm(null); }}>Отмена</Button>
            <Button kind={confirmState?.danger ? "danger" : "primary"}
              onClick={() => { confirmState?.resolve(true); setConfirm(null); }}>
              {confirmState?.ok ?? "Подтвердить"}
            </Button>
          </div>
        </Sheet>
      </div>
    </Ctx.Provider>
  );
}
