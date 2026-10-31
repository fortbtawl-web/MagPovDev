import { useState } from "react";
import { call } from "../api";
import { fmtDateTime } from "../../shared/format";
import { Badge, Load, Panel, useData, useMutate } from "../components";
import { Icon } from "../Icon";
import { BroadcastJournal, Counter, MAX_TEXT, TgBubble, useBroadcasts, useDoneToast, type Broadcast } from "./BroadcastParts";

interface MState { active: boolean; auto_restore: boolean; restore_text: string | null; announced_at: string | null; announced_by: number | null }
interface MResp { state: MState; templates: { down: string; up: string }; recipients_all: number }

export function Maintenance() {
  const res = useData<MResp>("/api/admin/maintenance");
  return <Load res={res}>{(d) => <Body d={d} reload={res.reload} />}</Load>;
}

function Body({ d, reload }: { d: MResp; reload: () => Promise<void> }) {
  const mutate = useMutate();
  const list = useBroadcasts();
  const watch = useDoneToast(list.data?.items);
  const [down, setDown] = useState(d.templates.down);
  const [auto, setAuto] = useState(d.state.auto_restore || !d.state.active);
  const [upAuto, setUpAuto] = useState(d.state.restore_text || d.templates.up);
  const [up, setUp] = useState(d.state.restore_text || d.templates.up);
  const [busy, setBusy] = useState(false);
  const st = d.state;
  const items: Broadcast[] | null = list.data ? list.data.items.filter((b) => b.kind.startsWith("maintenance")) : null;
  const count = d.recipients_all;

  async function guard(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try { await fn(); } finally { setBusy(false); }
  }
  const refresh = async () => { await Promise.all([reload(), list.reload()]); };
  const ro = (name: string, label: string, value: string) => ({ name, label, value, type: "textarea" as const, readOnly: true });

  const announce = () => guard(() => mutate(
    "Подтвердите объявление технических работ",
    [
      { name: "to", label: "Получатели", value: `Все пользователи, примерно ${count}`, readOnly: true },
      ro("text", "Сообщение о работах", down),
      ...(auto ? [ro("rt", "Сообщение о восстановлении (отправится автоматически)", upAuto)] : []),
    ],
    (v) => call<{ broadcast?: Broadcast }>("POST", "/api/admin/maintenance/announce", { text: down, auto_restore: auto, restore_text: auto ? upAuto : null, ...(v.reason ? { reason: v.reason } : {}) }),
    async (r) => { watch(r?.broadcast?.id); setUp(upAuto); await refresh(); }, "Сообщение о технических работах поставлено в очередь", { reasonOptional: true },
  ));
  const restore = () => guard(() => mutate(
    "Подтвердите сообщение о восстановлении",
    [{ name: "to", label: "Получатели", value: `Все пользователи, примерно ${count}`, readOnly: true }, ro("text", "Сообщение", up)],
    (v) => call<{ broadcast?: Broadcast }>("POST", "/api/admin/maintenance/restore", { text: up, ...(v.reason ? { reason: v.reason } : {}) }),
    async (r) => { watch(r?.broadcast?.id); await refresh(); }, "Сообщение о восстановлении поставлено в очередь", { reasonOptional: true },
  ));
  const cancel = () => guard(() => mutate(
    "Отменить режим технических работ без сообщения",
    [{ name: "i", label: "Что произойдёт", value: "Пользователям ничего не отправится, режим будет снят", readOnly: true }],
    (v) => call("POST", "/api/admin/maintenance/cancel", v.reason ? { reason: v.reason } : {}),
    refresh, "Режим работ снят", { reasonOptional: true },
  ));

  return (
    <>
      <Panel title="Состояние бота" actions={<button className="btn" onClick={() => void refresh()}><Icon name="refresh" size={16} />Обновить</button>}>
        <div className={st.active ? "warnbox" : "okbox"}>
          <Icon name={st.active ? "wrench" : "check"} size={16} />
          <span>
            <Badge tone={st.active ? "warn" : "ok"}>{st.active ? "Технические работы объявлены" : "Бот работает в обычном режиме"}</Badge>
            {st.active && st.announced_at && <> с {fmtDateTime(st.announced_at)}</>}
            {st.active && <><br /><small className="muted">{st.auto_restore ? "После успешного запуска бот сам сообщит о восстановлении." : "Автоматическое сообщение о восстановлении выключено."}</small></>}
          </span>
        </div>
        <ol className="steps">
          <li>Перед остановкой сервера отправьте объявление о работах.</li>
          <li>Обновите и перезапустите хостинг.</li>
          <li>После успешного старта бот сам отправит сообщение о восстановлении (если включено) — или нажмите кнопку вручную.</li>
        </ol>
        <p className="muted">Полностью остановленный бот не может отправлять сообщения сам, поэтому объявление нужно отправить заранее.</p>
      </Panel>

      <div className="cols compose">
        <Panel title="Перед отключением">
          <label>Сообщение пользователям
            <textarea rows={8} maxLength={MAX_TEXT} value={down} onChange={(e) => setDown(e.target.value)} />
            <Counter n={down.length} />
          </label>
          <TgBubble text={down} />
          <label className="check"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
            Автоматически сообщить о восстановлении после успешного запуска</label>
          {auto && (
            <label>Сообщение о восстановлении
              <textarea rows={5} maxLength={MAX_TEXT} value={upAuto} onChange={(e) => setUpAuto(e.target.value)} />
              <Counter n={upAuto.length} />
            </label>
          )}
          <div className="row end">
            <button className="btn primary" disabled={busy || !down.trim() || (auto && !upAuto.trim())} onClick={() => void announce()}>
              <Icon name="wrench" size={16} />{busy ? "Подождите…" : "Сообщить о технических работах"}
            </button>
          </div>
        </Panel>

        <Panel title="После запуска">
          <label>Сообщение о восстановлении
            <textarea rows={8} maxLength={MAX_TEXT} value={up} onChange={(e) => setUp(e.target.value)} />
            <Counter n={up.length} />
          </label>
          <TgBubble text={up} />
          <div className="row end">
            {st.active && <button className="btn danger" disabled={busy} onClick={() => void cancel()}>Снять режим без сообщения</button>}
            <button className="btn primary" disabled={busy || !up.trim()} onClick={() => void restore()}>
              <Icon name="check" size={16} />{busy ? "Подождите…" : "Сообщить о восстановлении сейчас"}
            </button>
          </div>
        </Panel>
      </div>

      <BroadcastJournal title="Сообщения о технических работах" items={items} error={list.error} onChanged={refresh} />
    </>
  );
}
