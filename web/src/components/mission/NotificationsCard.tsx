import { BellRing, Send } from "lucide-react";
import { useState } from "react";
import {
  useNotifications,
  useTelegramLink,
  useTelegramLinkStart,
  useTelegramTest,
  useTelegramUnlink,
} from "../../api/queries";
import { desktopEnabled, disableDesktop, enableDesktop, notificationsSupported } from "../../lib/notify";
import { Button, ErrorNote } from "../ui";

function DesktopRow() {
  const [on, setOn] = useState(desktopEnabled());
  const supported = notificationsSupported();
  return (
    <div className="mc-notify-row">
      <span><BellRing size={15} /> Desktop</span>
      {!supported ? <span className="muted">not supported in this browser</span> : (
        <button type="button" className={`switch switch-sm ${on ? "switch-on" : ""}`} aria-pressed={on}
                aria-label={on ? "Turn desktop notifications off" : "Turn desktop notifications on"}
                onClick={async () => {
                  if (on) { disableDesktop(); setOn(false); } else setOn(await enableDesktop());
                }}>
          <span className="switch-knob" />
        </button>
      )}
    </div>
  );
}

function TelegramRow() {
  const n = useNotifications();
  const start = useTelegramLinkStart();
  const link = useTelegramLink();
  const test = useTelegramTest();
  const unlink = useTelegramUnlink();
  const tg = n.data?.telegram;
  if (!tg) return null;
  if (!tg.configured) {
    return (
      <div className="mc-notify-row">
        <span><Send size={15} /> Telegram</span>
        <span className="muted">add TELEGRAM_BOT_TOKEN to .env to approve from your phone</span>
      </div>
    );
  }
  const code = start.data;
  return (
    <div className="mc-notify-block">
      <div className="mc-notify-row">
        <span><Send size={15} /> Telegram approvals</span>
        <span className={tg.linked ? "tone-go" : "muted"}>{tg.linked ? `linked ${tg.chat_hint}` : "not linked"}</span>
      </div>
      {tg.linked ? (
        <div className="mc-notify-actions">
          <Button variant="ghost" busy={test.isPending} onClick={() => test.mutate()}>Send a test</Button>
          <Button variant="ghost" busy={unlink.isPending} onClick={() => unlink.mutate()}>Unlink</Button>
        </div>
      ) : code ? (
        <div className="mc-notify-pair">
          <p>1. On your phone, <a href={code.deep_link} target="_blank" rel="noopener noreferrer">open @{code.bot_username}</a> and press <strong>Start</strong> (or send <code>/start {code.code}</code>).</p>
          <p>2. Then: <Button busy={link.isPending} onClick={() => link.mutate(code.code)}>I pressed Start</Button></p>
        </div>
      ) : (
        <Button busy={start.isPending} onClick={() => start.mutate()}>Link my Telegram</Button>
      )}
      {test.isSuccess && <p className="tone-go" role="status">Test sent.</p>}
      <ErrorNote error={start.error ?? link.error ?? test.error ?? unlink.error} />
    </div>
  );
}

export function NotificationsCard() {
  const n = useNotifications();
  return (
    <section aria-labelledby="notify-title" className="mc-card mc-notify">
      <h2 id="notify-title" className="mc-card-title">Notifications</h2>
      <DesktopRow />
      <TelegramRow />
      {n.data?.quiet_hours && <p className="muted">Quiet hours {n.data.quiet_hours}: phone messages arrive silently.</p>}
      <ErrorNote error={n.error} />
    </section>
  );
}
