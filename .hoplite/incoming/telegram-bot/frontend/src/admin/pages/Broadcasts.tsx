import { useEffect, useState } from "react";
import { call } from "../api";
import { errText } from "../../shared/api";
import { Badge, Panel, useMutate } from "../components";
import { Icon } from "../Icon";
import { BroadcastJournal, Counter, MAX_TEXT, TgBubble, useBroadcasts, useDoneToast, type Broadcast } from "./BroadcastParts";

interface PreviewUser { id: number; name: string; username: string | null; is_banned: boolean }

export function Broadcasts() {
  const mutate = useMutate();
  const list = useBroadcasts();
  const watch = useDoneToast(list.data?.items);
  const [mode, setMode] = useState<"single" | "all">("single");
  const [idText, setIdText] = useState("");
  const [user, setUser] = useState<PreviewUser | null>(null);
  const [userErr, setUserErr] = useState("");
  const [finding, setFinding] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const recipients = list.data?.recipients_all ?? null;

  useEffect(() => { setUser(null); setUserErr(""); }, [idText]);

  async function find() {
    if (!idText || finding) return;
    setFinding(true); setUserErr(""); setUser(null);
    try {
      const r = await call<{ user: PreviewUser }>("GET", `/api/admin/broadcasts/preview-user/${idText}`);
      setUser(r.user);
    } catch (e) { setUserErr(errText(e)); }
    setFinding(false);
  }

  const textOk = text.trim().length > 0 && text.length <= MAX_TEXT;
  const canSend = textOk && !sending && (mode === "all" || !!user);

  async function send() {
    if (!canSend) return;
    setSending(true);
    try {
      const fields = mode === "single" && user
        ? [
          { name: "to", label: "Получатель", value: `${user.name}${user.username ? ` @${user.username}` : ""} (ID ${user.id})`, readOnly: true },
          { name: "text", label: "Текст сообщения", type: "textarea" as const, value: text, readOnly: true },
        ]
        : [
          { name: "to", label: "Получатели", value: `Все пользователи, примерно ${recipients ?? "?"}`, readOnly: true },
          { name: "text", label: "Текст сообщения", type: "textarea" as const, value: text, readOnly: true },
          { name: "ok", label: "Я проверил текст, отправить всем", type: "checkbox" as const, required: true },
        ];
      await mutate(
        mode === "single" ? "Подтвердите отправку сообщения" : "Подтвердите массовую отправку",
        fields,
        (v) => call<Broadcast>("POST", "/api/admin/broadcasts", {
          mode, text, ...(mode === "single" ? { target_user_id: user!.id } : {}), ...(v.reason ? { reason: v.reason } : {}),
        }),
        async (b) => { watch(b?.id); setText(""); await list.reload(); },
        "Рассылка поставлена в очередь",
        { reasonOptional: true },
      );
    } finally { setSending(false); }
  }

  return (
    <>
      <Panel title="Новое сообщение">
        <div className="seg" role="tablist" aria-label="Режим отправки">
          {(["single", "all"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => setMode(m)}>
              {m === "single" ? "Одному пользователю" : "Всем пользователям"}
            </button>
          ))}
        </div>

        <div className="cols compose">
          <div>
            {mode === "single" ? (
              <>
                <label>Telegram ID пользователя
                  <div className="row nowrap">
                    <input inputMode="numeric" autoComplete="off" placeholder="например 123456789" value={idText}
                      onChange={(e) => setIdText(e.target.value.replace(/\D/g, "").slice(0, 15))}
                      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void find(); } }} />
                    <button className="btn" disabled={!idText || finding} onClick={() => void find()}><Icon name="search" size={16} />{finding ? "Ищем…" : "Найти"}</button>
                  </div>
                </label>
                {userErr && <div className="alert" role="alert"><Icon name="alert" /><span className="grow">{userErr}</span></div>}
                {user && (
                  <div className={user.is_banned ? "warnbox" : "okbox"}>
                    <Icon name={user.is_banned ? "alert" : "check"} size={16} />
                    <span><b>{user.name}</b>{user.username ? ` · @${user.username}` : ""} · ID {user.id}
                      {user.is_banned && <> <Badge tone="err">заблокирован в сервисе</Badge></>}</span>
                  </div>
                )}
              </>
            ) : (
              <div className="infobox"><Icon name="info" size={16} />
                <span>Получателей: ~{recipients ?? "…"}. Отправка идёт в фоне с учётом лимитов Telegram, перед стартом потребуется подтверждение.</span></div>
            )}
            <label>Текст сообщения
              <textarea rows={7} maxLength={MAX_TEXT} value={text} onChange={(e) => setText(e.target.value)} placeholder="Введите текст. Переносы строк сохраняются." />
              <Counter n={text.length} />
            </label>
            <div className="row end">
              <button className="btn primary" disabled={!canSend} onClick={() => void send()}>
                <Icon name="megaphone" size={16} />{sending ? "Отправляем…" : mode === "single" ? "Отправить" : "Отправить всем"}
              </button>
            </div>
          </div>
          <div>
            <h4 className="section-title">Предпросмотр</h4>
            <TgBubble text={text} />
          </div>
        </div>
      </Panel>

      <BroadcastJournal items={list.data?.items ?? null} error={list.error} onChanged={list.reload} />
    </>
  );
}
