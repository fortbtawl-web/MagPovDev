import { Icon } from "../Icon";
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api } from "../api";
import { useApp } from "../ctx";
import { useLoad } from "../useLoad";
import { Badge, Card, ErrorBox, Skeleton, statusTone } from "../ui";
import { copyText, fmtDate } from "../../shared/format";
import { errText } from "../../shared/api";
import { confirmDialog, haptic, openExternal } from "../telegram";
import type { DeviceMutation, LinkResp, NodePub } from "../../shared/types";

export function DeviceDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const { toast, reloadMe } = useApp();
  const res = useLoad<LinkResp>(`/api/devices/${id}/link`);
  const nodes = useLoad<{ nodes: NodePub[] }>("/api/nodes");
  const [qr, setQr] = useState<string | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [replaceNode, setReplaceNode] = useState("");
  const [busy, setBusy] = useState(false);

  const url = res.data?.subscription_url ?? res.data?.link ?? null;
  useEffect(() => {
    setQr(null);
    if (url) QRCode.toDataURL(url, { margin: 1, width: 280 }).then(setQr).catch(() => setQr(null));
  }, [url]);

  if (res.loading && !res.data) return <><div className="loading-line"><span className="spinner sm" role="status" aria-label="Загрузка" /><span>Получаем конфигурацию…</span></div><Skeleton /></>;
  if (res.error || !res.data) return <ErrorBox text={res.error ?? "Ошибка"} onRetry={() => void res.reload()} />;
  const d = res.data.device;
  const active = d.status === "active";

  async function run<T>(fn: () => Promise<T>) {
    setBusy(true);
    try { await fn(); } catch (e) { haptic("error"); toast(errText(e), "err"); }
    setBusy(false);
  }

  const rename = () => run(async () => {
    if (label === null || !label.trim()) return;
    await api("PATCH", `/api/devices/${id}`, { label: label.trim() });
    toast("Название сохранено");
    setLabel(null);
    await res.reload();
  });

  const replace = () => run(async () => {
    if (!(await confirmDialog("Заменить устройство? Старый ключ будет отозван, выдадим новый."))) return;
    const r = await api<DeviceMutation>("POST", `/api/devices/${id}/replace`, replaceNode ? { node_code: replaceNode } : {});
    if (r.confirmed) { haptic("success"); toast("Устройство заменено"); }
    else toast(`Сервер не подтвердил создание: ${r.error ?? "неизвестная ошибка"}. Мы повторим автоматически.`, "err");
    await reloadMe();
    onBack();
  });

  const revoke = () => run(async () => {
    if (!(await confirmDialog("Отозвать подключение? Доступ на этом устройстве перестанет работать."))) return;
    const r = await api<{ confirmed: boolean; status: string; message: string }>("DELETE", `/api/devices/${id}`);
    toast(r.message, r.confirmed ? "ok" : "err");
    if (r.confirmed) haptic("success");
    await reloadMe();
    if (r.confirmed) onBack(); else await res.reload();
  });

  const copy = async () => {
    if (url && (await copyText(url))) { haptic("success"); toast("Ссылка скопирована"); } else toast("Не удалось скопировать", "err");
  };

  return (
    <>
      <Card>
        <div className="row-between">
          <h2>{d.label}</h2>
          <Badge tone={statusTone(d.status)}>{d.status_label}</Badge>
        </div>
        <div className="hint">{d.node ? `${d.node.flag} ${d.node.name}` : "Сервер не назначен"} · {d.protocol}</div>
        <div className="hint small">Создано {fmtDate(d.created_at)}{d.expires_at ? ` · до ${fmtDate(d.expires_at)}` : ""}</div>
        {d.last_error && <div className="hint small err-text">{d.last_error}</div>}
      </Card>

      {active && url ? (
        <Card>
          <h2>Подключение</h2>
          {!res.data.live && <p className="hint err-text">Сервер сейчас недоступен — показаны сохранённые данные.</p>}
          {qr && <img className="qr" src={qr} alt="QR-код подключения" />}
          <div className="linkbox">{url}</div>
          <div className="stack">
            <button className="btn" onClick={() => void copy()}><Icon name="copy" size={18} /><span>Копировать ссылку</span></button>
            <button className="btn btn-secondary" onClick={() => openExternal(url)}><Icon name="link" size={18} /><span>Открыть ссылку</span></button>
          </div>
          <ol className="howto">
            <li>Установите приложение: Happ, Hiddify, v2RayTun или v2rayNG — пошаговый мастер есть в боте, раздел «Инструкция».</li>
            <li>Нажмите «Открыть ссылку» или отсканируйте QR-код.</li>
            <li>Импортируйте подписку и включите VPN.</li>
          </ol>
        </Card>
      ) : (
        <Card><p className="hint">{active ? "Ссылка подключения пока недоступна." : "Для этого устройства ссылка недоступна."}</p></Card>
      )}

      <Card>
        <h2>Управление</h2>
        <label className="field">Новое название
          <input value={label ?? d.label} maxLength={40} onChange={(e) => setLabel(e.target.value)} />
        </label>
        <button className="btn btn-secondary" disabled={busy || label === null || !label.trim()} onClick={() => void rename()}>Сохранить название</button>
        {active && (
          <>
            <label className="field">Сервер для замены
              <select value={replaceNode} onChange={(e) => setReplaceNode(e.target.value)}>
                <option value="">Любой (автоматически)</option>
                {nodes.data?.nodes.map((n) => <option key={n.code} value={n.code}>{n.flag} {n.name}</option>)}
              </select>
            </label>
            <button className="btn btn-secondary" disabled={busy} onClick={() => void replace()}><Icon name="refresh" size={18} /><span>Заменить устройство</span></button>
            <button className="btn btn-danger" disabled={busy} onClick={() => void revoke()}><Icon name="trash" size={18} /><span>Отозвать подключение</span></button>
          </>
        )}
      </Card>
    </>
  );
}
