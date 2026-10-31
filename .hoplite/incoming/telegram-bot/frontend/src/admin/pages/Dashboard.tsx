import { Load, Panel, Section, Stat, useData } from "../components";
import { fmtMoney } from "../../shared/format";
import type { AdminDashboard } from "../../shared/types";
import { AuditTable, NodesTable, OpsTable } from "./parts";

export function Dashboard() {
  const res = useData<AdminDashboard>("/api/admin/stats");
  return (
    <Load res={res}>
      {({ stats: s, nodes, recent_problems, recent_audit }) => (
        <>
          <div className="hero">
            <div><span>Пользователей</span><b>{s.users_total}</b><small>+{s.users_new_24h} за 24 ч · +{s.users_new_7d} за 7 дн</small></div>
            <div><span>Активных подписок</span><b>{s.subs_active}</b><small>платных: {s.subs_paid_active} · lifetime: {s.subs_lifetime}</small></div>
            <div><span>Подтверждённый доход</span><b>{fmtMoney(s.revenue_minor)}</b><small>оплаченных заказов: {s.orders_paid}</small></div>
          </div>
          <div className="cols">
            <Section title="Подписки" icon="subscriptions">
              <div className="stats">
                <Stat label="Истёкших" value={s.subs_expired} />
                <Stat label="Приостановленных" value={s.subs_suspended} tone={s.subs_suspended > 0 ? "warn" : undefined} />
                <Stat label="Пробных периодов" value={s.trials_total} />
              </div>
            </Section>
            <Section title="Рефералы" icon="referrals">
              <div className="stats">
                <Stat label="Приглашений" value={s.referrals_total} />
                <Stat label="С пробным / с оплатой" value={`${s.referrals_with_trial} / ${s.referrals_with_payment}`} />
                <Stat label="Бонусных дней" value={s.bonus_days_granted} />
              </div>
            </Section>
            <Section title="VPN" icon="infra">
              <div className="stats">
                <Stat label="Активных ключей" value={s.devices_active} />
                <Stat label="Операций в очереди" value={s.vpn_ops_pending} tone={s.vpn_ops_pending > 0 ? "warn" : undefined} />
                <Stat label="Ошибок за 24 ч" value={s.vpn_ops_failed_24h} tone={s.vpn_ops_failed_24h > 0 ? "err" : undefined} />
              </div>
            </Section>
            <Section title="Поддержка и доступ" icon="support">
              <div className="stats">
                <Stat label="Новых обращений" value={s.support_new ?? 0} tone={(s.support_new ?? 0) > 0 ? "warn" : undefined} />
                <Stat label="Открытых обращений" value={s.support_open ?? 0} />
                <Stat label="Заблокированных" value={s.users_banned ?? 0} />
              </div>
            </Section>
          </div>
          <Panel title="Состояние VPN-нод"><NodesTable nodes={nodes} /></Panel>
          <Panel title="Последние проблемы"><OpsTable ops={recent_problems} /></Panel>
          <Panel title="Последние действия администраторов"><AuditTable items={recent_audit} /></Panel>
        </>
      )}
    </Load>
  );
}
