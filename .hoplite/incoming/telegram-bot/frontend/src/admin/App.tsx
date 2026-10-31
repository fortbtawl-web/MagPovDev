import { useCallback, useEffect, useRef, useState } from "react";
import { Ctx, type Field, type FormOpts, type FormResult } from "./ctx";
import { call, getToken, setLogoutHandler, setToken } from "./api";
import { API_BASE } from "../shared/api";
import type { AdminDashboard, AdminMe } from "../shared/types";
import { Login } from "./Login";
import { FormModal } from "./FormModal";
import { Dashboard } from "./pages/Dashboard";
import { Users } from "./pages/Users";
import { Plans } from "./pages/Plans";
import { Promos } from "./pages/Promos";
import { Referrals } from "./pages/Referrals";
import { Subscriptions } from "./pages/Subscriptions";
import { Orders } from "./pages/Orders";
import { Infra } from "./pages/Infra";
import { Audit } from "./pages/Audit";
import { Settings } from "./pages/Settings";
import { Admins } from "./pages/Admins";
import { Support } from "./pages/Support";
import { Broadcasts } from "./pages/Broadcasts";
import { Maintenance } from "./pages/Maintenance";
import { Tasks } from "./pages/Tasks";
import { Coins } from "./pages/Coins";
import { TrialCodes } from "./pages/TrialCodes";
import { Badge } from "./components";
import { Icon, type IconName } from "./Icon";

const PAGES: { id: string; label: string; icon: IconName; group: string; perm?: string }[] = [
  { id: "dashboard", label: "Главная", icon: "dashboard", group: "Обзор" },
  { id: "support", label: "Поддержка", icon: "support", group: "Обзор" },
  { id: "users", label: "Пользователи", icon: "users", group: "Клиенты" },
  { id: "subscriptions", label: "Подписки и устройства", icon: "subscriptions", group: "Клиенты" },
  { id: "orders", label: "Платежи", icon: "orders", group: "Клиенты" },
  { id: "plans", label: "Тарифы", icon: "plans", group: "Продукт" },
  { id: "promos", label: "Промокоды", icon: "promos", group: "Продукт" },
  { id: "referrals", label: "Реферальная программа", icon: "referrals", group: "Продукт" },
  { id: "tasks", label: "Задания", icon: "tasks", group: "Вознаграждения", perm: "tasks.review" },
  { id: "coins", label: "SpirCoin", icon: "coin", group: "Вознаграждения", perm: "users.view" },
  { id: "trialcodes", label: "Тестовые коды", icon: "key", group: "Вознаграждения", perm: "trialcodes.manage" },
  { id: "broadcasts", label: "Рассылка и сообщения", icon: "megaphone", group: "Коммуникации" },
  { id: "maintenance", label: "Статус бота и техработы", icon: "wrench", group: "Коммуникации" },
  { id: "infra", label: "VPN-инфраструктура", icon: "infra", group: "Система" },
  { id: "audit", label: "Журнал", icon: "audit", group: "Система" },
  { id: "admins", label: "Администраторы", icon: "admins", group: "Система" },
  { id: "settings", label: "Настройки", icon: "settings", group: "Система" },
];
const GROUPS = ["Обзор", "Клиенты", "Продукт", "Вознаграждения", "Коммуникации", "Система"];

const FROM_KEY = "spiritt_from_miniapp";
const tgApp = window.Telegram?.WebApp;

function backToApp() { window.location.href = `${API_BASE}/`; }

function parseHash(): { page: string; param: string } {
  const [p, param] = window.location.hash.replace(/^#\/?/, "").split("/");
  return { page: PAGES.some((x) => x.id === p) ? p : "dashboard", param: param ?? "" };
}

export function App() {
  // Токен из #admin_token=... (необязательный способ входа) — убираем из URL сразу.
  const [authed, setAuthed] = useState(() => {
    const hash = window.location.hash;
    const m = hash.match(/admin_token=([^&]+)/);
    if (m) setToken(decodeURIComponent(m[1]));
    if (/(^|[#&])from=miniapp/.test(hash)) sessionStorage.setItem(FROM_KEY, "1");
    if (m || /from=miniapp/.test(hash)) history.replaceState(null, "", window.location.pathname + window.location.search);
    return !!getToken();
  });
  const fromMiniapp = !!tgApp?.initData || sessionStorage.getItem(FROM_KEY) === "1";
  const [me, setMe] = useState<AdminMe | null>(null);
  const [supportNew, setSupportNew] = useState(0);
  const [route, setRoute] = useState(parseHash);
  const [notice, setNotice] = useState("");
  const [menu, setMenu] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const [formState, setFormState] = useState<{ title: string; fields: Field[]; reasonOptional: boolean; resolve: (v: FormResult | null) => void } | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    setLogoutHandler(() => { setNotice("Сессия завершена или доступ запрещён. Войдите снова."); setAuthed(false); });
    const h = () => { setRoute(parseHash()); setMenu(false); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  useEffect(() => {
    if (!tgApp) return;
    tgApp.ready();
    tgApp.expand();
    if (fromMiniapp) document.documentElement.classList.add("tg");
  }, [fromMiniapp]);

  useEffect(() => {
    const bb = tgApp?.BackButton;
    if (!bb || !fromMiniapp) return;
    bb.show();
    bb.onClick(backToApp);
    return () => { bb.offClick(backToApp); bb.hide(); };
  }, [fromMiniapp]);

  useEffect(() => {
    if (!authed) { setMe(null); return; }
    let alive = true;
    const loadStats = () => call<AdminDashboard>("GET", "/api/admin/stats")
      .then((d) => { if (alive) setSupportNew(d.stats.support_new ?? 0); }).catch(() => {});
    call<AdminMe>("GET", "/api/admin/me").then((r) => { if (alive) setMe(r); }).catch(() => {});
    void loadStats();
    const t = window.setInterval(loadStats, 60000);
    return () => { alive = false; window.clearInterval(t); };
  }, [authed]);

  const notify = useCallback((msg: string, kind: "ok" | "err" = "ok") => {
    setToast({ msg, kind });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 5000);
  }, []);
  const form = useCallback((title: string, fields: Field[] = [], opts: FormOpts = {}) =>
    new Promise<FormResult | null>((resolve) => setFormState({ title, fields, reasonOptional: !!opts.reasonOptional, resolve })), []);
  const go = useCallback((page: string, param?: string | number) => {
    window.location.hash = `#/${page}${param !== undefined ? `/${param}` : ""}`;
  }, []);

  if (!authed) return <Login notice={notice} onLogin={() => { setNotice(""); setAuthed(true); }} />;

  let body;
  switch (route.page) {
    case "support": body = <Support param={route.param} />; break;
    case "broadcasts": body = <Broadcasts />; break;
    case "maintenance": body = <Maintenance />; break;
    case "admins": body = <Admins />; break;
    case "users": body = <Users param={route.param} />; break;
    case "tasks": body = <Tasks />; break;
    case "coins": body = <Coins />; break;
    case "trialcodes": body = <TrialCodes />; break;
    case "plans": body = <Plans />; break;
    case "promos": body = <Promos />; break;
    case "referrals": body = <Referrals />; break;
    case "subscriptions": body = <Subscriptions />; break;
    case "orders": body = <Orders />; break;
    case "infra": body = <Infra />; break;
    case "audit": body = <Audit />; break;
    case "settings": body = <Settings />; break;
    default: body = <Dashboard />;
  }

  const canSee = (perm?: string) => !perm || !me?.permissions || me.is_owner || me.permissions.includes(perm);
  const current = PAGES.find((p) => p.id === route.page);
  return (
    <Ctx.Provider value={{ form, notify, go, me, isOwner: !!me?.is_owner }}>
      <div className="layout">
        {menu && <div className="scrim" onClick={() => setMenu(false)} />}
        <aside className={menu ? "sidebar open" : "sidebar"}>
          <div className="brand">
            <span className="brand-mark"><Icon name="shield" size={18} /></span>
            <span>SpirittVPN<small>Админ-панель</small></span>
          </div>
          <nav aria-label="Разделы">
            {GROUPS.map((g) => (
              <div key={g} className="nav-group">
                <div className="nav-title">{g}</div>
                {PAGES.filter((p) => p.group === g && canSee(p.perm)).map((p) => (
                  <a key={p.id} href={`#/${p.id}`} className={route.page === p.id ? "nav active" : "nav"}
                    aria-current={route.page === p.id ? "page" : undefined}>
                    <Icon name={p.icon} /><span className="grow">{p.label}</span>
                    {p.id === "support" && supportNew > 0 && <span className="count">{supportNew}</span>}
                  </a>
                ))}
              </div>
            ))}
          </nav>
          <button className="nav logout" onClick={() => { setNotice(""); setToken(null); setAuthed(false); }}><Icon name="logout" /><span>Выйти</span></button>
        </aside>
        <div className="main">
          <header className="topbar">
            <button className="icon-btn menu-btn" aria-label="Меню" aria-expanded={menu} onClick={() => setMenu((m) => !m)}><Icon name={menu ? "close" : "menu"} /></button>
            {fromMiniapp && <button className="btn" onClick={backToApp}><Icon name="back" size={16} /><span className="back-label">В приложение</span></button>}
            <h1>{current?.label}</h1>
            <span className="grow" />
            {me && <Badge tone={me.is_owner ? "ok" : "neutral"}>{me.is_owner ? "Владелец" : "Администратор"}</Badge>}
          </header>
          <div className="busy-bar" aria-hidden="true" />
          <main className="page" key={route.page}>{body}</main>
        </div>
        {formState && (
          <FormModal title={formState.title} fields={formState.fields} reasonOptional={formState.reasonOptional}
            onClose={(v) => { formState.resolve(v); setFormState(null); }} />
        )}
        <div className="toasts" aria-live="polite" role="status">
          {toast && (
            <div className={`toast toast-${toast.kind}`} onClick={() => setToast(null)}>
              <Icon name={toast.kind === "ok" ? "check" : "alert"} size={16} /><span>{toast.msg}</span>
            </div>
          )}
        </div>
      </div>
    </Ctx.Provider>
  );
}
