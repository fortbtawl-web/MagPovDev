import { call } from "../api";
import { Load, Panel, Table, useData, useMutate } from "../components";
import type { Integrations, SettingSchema } from "../../shared/types";

interface Resp { values: Record<string, boolean | number>; schema: SettingSchema[] }

export function Settings() {
  const res = useData<Resp>("/api/admin/settings");
  const integ = useData<Integrations>("/api/admin/integrations");
  const mutate = useMutate();

  function edit(s: SettingSchema, current: boolean | number) {
    const fields = s.type === "bool"
      ? [{ name: "value", label: s.label, type: "checkbox" as const, value: current ? "true" : "" }]
      : [{ name: "value", label: s.label, type: "number" as const, value: String(current), required: true,
          hint: s.bounds ? `Допустимо: от ${s.bounds[0]} до ${s.bounds[1]}` : undefined }];
    void mutate(`Изменить настройку «${s.label}»`, fields,
      (v) => call("PUT", `/api/admin/settings/${s.key}`, { value: s.type === "bool" ? v.value === "true" : Math.trunc(Number(v.value)), reason: v.reason }),
      () => res.reload());
  }

  return (
    <>
      <Panel title="Настройки сервиса">
        <Load res={res}>
          {({ values, schema }) => (
            <Table head={["Параметр", "Ключ", "Значение", "По умолчанию", ""]} empty={schema.length === 0}>
              {schema.map((s) => {
                const cur = values[s.key] ?? s.default;
                return (
                  <tr key={s.key}>
                    <td>{s.label}</td><td className="mono">{s.key}</td>
                    <td><b>{s.type === "bool" ? (cur ? "Включено" : "Выключено") : String(cur)}</b></td>
                    <td>{s.type === "bool" ? (s.default ? "Включено" : "Выключено") : String(s.default)}</td>
                    <td><button className="btn" onClick={() => edit(s, cur)}>Изменить</button></td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Load>
      </Panel>
      <Panel title="Интеграции (только просмотр)">
        <Load res={integ}>
          {(i) => (
            <>
              <div className="kvs">
                <div><span>Окружение</span><b>{i.app_env}</b></div>
                <div><span>Telegram-бот</span><b>{i.bot.configured ? `настроен${i.bot.username ? ` (@${i.bot.username})` : ""}` : "не настроен"}</b></div>
                <div><span>URL Mini App</span><b>{i.webapp_url ?? "не задан"}</b></div>
                <div><span>Публичный URL</span><b>{i.public_base_url ?? "не задан"}</b></div>
                <div><span>Платёжный провайдер</span><b>{i.payments.provider ?? "не подключён"}</b></div>
                <div><span>Webhook URL</span><b>{i.payments.webhook_url ?? "—"}</b></div>
                <div><span>Проверка IP вебхука</span><b>{i.payments.verify_ip ? "включена" : "выключена"}</b></div>
              </div>
              <Table head={["Нода", "Код", "API URL", "Токен"]} empty={i.vpn_nodes.length === 0}>
                {i.vpn_nodes.map((n) => (
                  <tr key={n.code}><td>{n.name}</td><td>{n.code}</td><td>{n.api_url_configured ? "задан" : "не задан"}</td><td>{n.token_configured ? "задан" : "не задан"}</td></tr>
                ))}
              </Table>
            </>
          )}
        </Load>
      </Panel>
    </>
  );
}
