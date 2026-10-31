import { call } from "../api";
import { Badge, Load, Panel, Stat, Table, tone, useData, useMutate } from "../components";
import { useAdmin } from "../ctx";
import { fmtDateTime } from "../../shared/format";
import type { RewardRow } from "../../shared/types";

interface Resp { stats: { referrals_total: number; referrals_with_trial: number; referrals_with_payment: number; bonus_days_granted: number }; rewards: RewardRow[] }

export function Referrals() {
  const res = useData<Resp>("/api/admin/referrals");
  const mutate = useMutate();
  const { go } = useAdmin();
  return (
    <Load res={res}>
      {({ stats, rewards }) => (
        <>
          <div className="stats">
            <Stat label="Приглашений" value={stats.referrals_total} />
            <Stat label="Активировали пробный" value={stats.referrals_with_trial} />
            <Stat label="Первая оплата" value={stats.referrals_with_payment} />
            <Stat label="Бонусных дней начислено" value={stats.bonus_days_granted} />
          </div>
          <Panel title="Настройки программы" actions={<button className="btn" onClick={() => go("settings")}>Открыть «Настройки» (referral_*)</button>}>
            <p className="muted">Размеры наград и включение событий настраиваются в разделе «Настройки».</p>
          </Panel>
          <Panel title="История начислений">
            <Table head={["ID", "Пригласивший", "Приглашённый", "Событие", "Дней", "Статус", "Создано", "Отмена", ""]} empty={rewards.length === 0}>
              {rewards.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td><td><a href={`#/users/${r.referrer_id}`}>{r.referrer_id}</a></td><td><a href={`#/users/${r.referral_id}`}>{r.referral_id}</a></td>
                  <td>{r.event_type}</td><td>{r.days}</td><td><Badge tone={tone(r.status)}>{r.status}</Badge></td>
                  <td>{fmtDateTime(r.created_at)}</td>
                  <td className="wrap">{r.reversed_at ? `${fmtDateTime(r.reversed_at)}: ${r.reverse_reason ?? ""}` : ""}</td>
                  <td>{!r.reversed_at && (
                    <button className="btn danger" onClick={() => void mutate(`Отменить начисление №${r.id} (${r.days} дн.)`, [],
                      (v) => call("POST", `/api/admin/rewards/${r.id}/reverse`, { reason: v.reason }), () => res.reload())}>Отменить</button>
                  )}</td>
                </tr>
              ))}
            </Table>
          </Panel>
        </>
      )}
    </Load>
  );
}
