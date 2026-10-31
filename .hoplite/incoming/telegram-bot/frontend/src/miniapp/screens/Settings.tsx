import { useApp } from "../ctx";
import { Card, Title } from "../ui";
import { Icon } from "../Icon";
import { setPref, usePrefs } from "../prefs";
import { playSound, unlockAudio } from "../sound";
import { openExternal, openTg } from "../telegram";

function Switch({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return (
    <div className="switch-row">
      <div><b>{label}</b><small>{hint}</small></div>
      <button className="switch" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)} />
    </div>
  );
}

export function Settings() {
  const { me, open } = useApp();
  const prefs = usePrefs();
  const url = me?.support_url;
  const n = me?.support_open_tickets ?? 0;

  return (
    <>
      <Title sub="Помощь и параметры интерфейса">Поддержка и настройки</Title>

      <Card>
        <h2>Связь с нами</h2>
        <button className="btn" onClick={() => open({ type: "support" })}>
          <Icon name="chat" size={18} /><span>Обращения в поддержку{n > 0 ? ` (${n})` : ""}</span>
        </button>
        {url ? (
          <button className="btn btn-secondary" onClick={() => (/^https?:\/\/t\.me\//.test(url) ? openTg(url) : openExternal(url))}>
            <Icon name="help" size={18} /><span>Другой способ связи</span>
          </button>
        ) : <p className="hint small">Дополнительная ссылка поддержки не настроена.</p>}
      </Card>

      <Card>
        <h2>Как подключиться</h2>
        <ol className="howto">
          <li>Откройте «Устройства» и создайте подключение — понадобится активная подписка.</li>
          <li>Установите приложение: Happ, Hiddify, v2RayTun или v2rayNG — пошаговый мастер есть в боте: «Инструкция».</li>
          <li>Откройте ссылку подключения или отсканируйте QR-код и включите VPN.</li>
        </ol>
      </Card>

      <Card>
        <h2>Интерфейс</h2>
        <Switch checked={prefs.sound} label="Звуки нажатий" hint="Тихие сигналы при действиях" onChange={(v) => {
          unlockAudio(); setPref("sound", v); if (v) playSound("ok");
        }} />
        <div className="range-row">
          <label htmlFor="vol" style={{ fontSize: 14 }}>Громкость</label>
          <input id="vol" type="range" min={0} max={100} step={5} value={Math.round(prefs.volume * 100)} disabled={!prefs.sound}
            aria-label="Громкость звуков" aria-valuetext={`${Math.round(prefs.volume * 100)}%`}
            onChange={(e) => setPref("volume", Number(e.target.value) / 100)}
            onPointerUp={() => playSound("tap")} onKeyUp={() => playSound("tap")} />
          <output htmlFor="vol">{Math.round(prefs.volume * 100)}%</output>
        </div>
        <Switch checked={prefs.reduceFx} label="Меньше эффектов" hint="Без анимаций и размытия — быстрее на слабых телефонах" onChange={(v) => setPref("reduceFx", v)} />
        <p className="hint small">Настройки хранятся только на этом устройстве.</p>
      </Card>
    </>
  );
}
