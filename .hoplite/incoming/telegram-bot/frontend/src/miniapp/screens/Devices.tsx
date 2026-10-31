import { useState } from "react";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, Empty, ErrorBox, Skeleton, Title, statusTone } from "../ui";
import { Icon } from "../Icon";
import { fmtDate } from "../../shared/format";
import { errText } from "../../shared/api";
import { haptic } from "../telegram";
import type { DeviceMutation, DevicesResp, NodePub } from "../../shared/types";

export function Devices() {
  const { toast, open, reloadMe, goTab } = useApp();
  const list = useLoad<DevicesResp>("/api/devices");
  const nodes = useLoad<{ nodes: NodePub[] }>("/api/nodes");
  const [adding, setAdding] = useState(false);
  const [node, setNode] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  if (list.loading && !list.data) return <Skeleton />;
  if (list.error || !list.data) return <ErrorBox text={list.error ?? "Ошибка"} onRetry={() => void list.reload()} />;
  const { devices, used, limit, subscription } = list.data;
  const canAdd = !!subscription && subscription.status === "active" && used < limit;

  async function add() {
    setBusy(true);
    try {
      const r = await api<DeviceMutation>("POST", "/api/devices", {
        ...(node ? { node_code: node } : {}),
        ...(label.trim() ? { label: label.trim() } : {}),
      });
      if (r.confirmed) { haptic("success"); toast("Подключение создано"); }
      else { haptic("warning"); toast(`Сервер не подтвердил создание: ${r.error ?? "неизвестная ошибка"}. Мы повторим автоматически.`, "err"); }
      setAdding(false); setLabel("");
      await Promise.all([list.reload(), reloadMe()]);
    } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  return (
    <>
      <Title sub="Каждое устройство получает свой ключ">Устройства</Title>
      <Card>
        <div className="row-between">
          <b>{used} из {limit} устройств</b>
          <button className="btn btn-small" disabled={!canAdd} onClick={() => setAdding(!adding)}><Icon name="plus" size={18} /><span>Добавить</span></button>
        </div>
        {!subscription && <p className="hint">Нужна активная подписка. <button className="link" onClick={() => goTab("plans")}>Выбрать тариф</button></p>}
        {subscription && used >= limit && <p className="hint">Лимит тарифа исчерпан — отзовите устройство или смените тариф.</p>}
      </Card>

      {adding && (
        <Card>
          <h2>Новое подключение</h2>
          <label className="field">Название (необязательно)
            <input value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} placeholder="Например, iPhone" />
          </label>
          <label className="field">Сервер
            <select value={node} onChange={(e) => setNode(e.target.value)}>
              <option value="">Любой (автоматически)</option>
              {nodes.data?.nodes.map((n) => (
                <option key={n.code} value={n.code}>{n.flag} {n.name}{n.online === false ? " — недоступен" : ""}</option>
              ))}
            </select>
          </label>
          <button className="btn" disabled={busy} onClick={() => void add()}>{busy ? "Создаём…" : "Создать подключение"}</button>
        </Card>
      )}

      {devices.length === 0 && <Empty icon="device" title="Устройств пока нет" text={subscription ? "Добавьте первое устройство — оно получит собственный ключ." : "Сначала оформите подписку или пробный период."} />}
      {devices.map((d) => (
        <Card key={d.id} className="clickable">
          <div onClick={() => open({ type: "device", id: d.id })}>
            <div className="row-between">
              <b>{d.label}</b>
              <Badge tone={statusTone(d.status)}>{d.status_label}</Badge>
            </div>
            <div className="hint">{d.node ? `${d.node.flag} ${d.node.name}` : "Сервер не назначен"} · {d.protocol}</div>
            <div className="hint small">Создано {fmtDate(d.created_at)}{d.expires_at ? ` · до ${fmtDate(d.expires_at)}` : ""}</div>
            {d.last_error && <div className="hint small err-text">{d.last_error}</div>}
          </div>
        </Card>
      ))}
    </>
  );
}
