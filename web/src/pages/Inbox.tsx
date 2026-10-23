import { ExternalLink, MailCheck, RefreshCw, Unplug } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import {
  useApplySuggestion,
  useReplyAgain,
  useConnectMail,
  useDisconnectMail,
  useEmails,
  useMailStatus,
  usePollMail,
  type EmailRow,
  type MailStatus,
} from "../api/queries";
import { ACTION_LABEL, CATEGORY_LABEL, CATEGORY_TONE } from "../components/inbox/categories";
import { Badge, Button, Empty, ErrorNote, Lamp, Panel } from "../components/ui";
import { relativeTime } from "../lib/format";

const FILTERS = [
  ["action", "Needs you"],
  ["all", "All"],
  ["interview_invite", "Interviews"],
  ["recruiter_outreach", "Recruiters"],
  ["assessment", "Assessments"],
  ["offer", "Offers"],
  ["rejection", "Rejections"],
] as const;

const PROVIDERS = [
  { id: "gmail", label: "Gmail", env: "GOOGLE_CLIENT_ID" },
  { id: "outlook", label: "Outlook / Microsoft 365", env: "MS_CLIENT_ID" },
] as const;

function Connect({ status }: { status: MailStatus }) {
  const connect = useConnectMail();
  const [calendar, setCalendar] = useState(false);
  const url = connect.data?.authorization_url;

  return (
    <div className="mail-connect">
      <p className="muted">
        JobPilot reads your inbox and sends only replies you approve. It asks for read and send
        access, nothing more, plus free/busy (no event details) if you allow calendar checks.
      </p>
      <label className="check-inline">
        <input type="checkbox" checked={calendar} onChange={(e) => setCalendar(e.target.checked)} />
        Check my calendar before proposing interview times
      </label>
      <div className="mail-providers">
        {PROVIDERS.map((p) => (
          <div key={p.id} className="mail-provider">
            <Button variant="primary" disabled={!status.configured[p.id]} busy={connect.isPending}
                    onClick={() => connect.mutate({ provider: p.id, calendar, address: "me@jobpilot.local" })}>
              Connect {p.label}
            </Button>
            {!status.configured[p.id] && <span className="muted">Add {p.env} to .env first (see README).</span>}
          </div>
        ))}
        <div className="mail-provider">
          <Button busy={connect.isPending}
                  onClick={() => connect.mutate({ provider: "local", calendar: true, address: "me@jobpilot.local" })}>
            Use a local test mailbox
          </Button>
          <span className="muted">A folder of .eml files under data/mailbox — no account needed.</span>
        </div>
      </div>
      {url && (
        <p className="mail-consent">
          A sign-in tab should have opened. If not,{" "}
          <a href={url} target="_blank" rel="noopener noreferrer">continue to the provider <ExternalLink size={13} /></a>.
          Come back here when it says connected.
        </p>
      )}
      <ErrorNote error={connect.error} />
    </div>
  );
}

function ReadOnly({ provider, reason }: { provider: string; reason: string }) {
  const connect = useConnectMail();
  const url = connect.data?.authorization_url;
  return (
    <div className="mail-readonly" role="alert">
      <p><strong>Replies can't be sent yet.</strong> {reason}</p>
      <p className="muted">
        On the {provider === "gmail" ? "Google" : "Microsoft"} page, make sure the box for sending
        email is ticked. Reading your inbox keeps working meanwhile.
      </p>
      <Button variant="primary" busy={connect.isPending}
              onClick={() => connect.mutate({ provider: provider as "gmail" | "outlook", calendar: false, address: "me@jobpilot.local" })}>
        Reconnect with send permission
      </Button>
      {url && <p className="muted">If no tab opened, <a href={url} target="_blank" rel="noopener noreferrer">continue to the provider</a>.</p>}
      <ErrorNote error={connect.error} />
    </div>
  );
}

function Account({ status }: { status: MailStatus }) {
  const account = status.account;
  const poll = usePollMail();
  const disconnect = useDisconnectMail();
  if (!account) return null;
  const tone = account.status === "connected" ? "go" : account.status === "read_only" ? "amber" : "stop";
  const report = poll.data;

  return (
    <div className="mail-account">
      <p className="run-line">
        <Lamp tone={tone} label={account.status.replace("_", " ")} />
        <strong>{account.address || account.provider}</strong>
        <span className="muted">· {account.provider} · calendar {account.calendar_enabled ? "on" : "off"}</span>
        <span className="muted">· checked {account.last_polled_at ? relativeTime(account.last_polled_at) : "never"},
          every {status.poll_interval_minutes} min</span>
      </p>
      {account.status === "read_only" && <ReadOnly provider={account.provider} reason={account.status_reason} />}
      {account.status !== "read_only" && account.status_reason && <p className="tone-stop">{account.status_reason}</p>}
      <div className="decision-actions">
        <Button variant="ghost" busy={disconnect.isPending}
                onClick={() => window.confirm("Disconnect this mailbox? Its access token is revoked.") && disconnect.mutate()}>
          <Unplug size={15} /> Disconnect
        </Button>
        <span className="spacer" />
        <Button variant="primary" busy={poll.isPending} onClick={() => poll.mutate()}>
          <RefreshCw size={15} /> Check now
        </Button>
      </div>
      {report && (
        <p className="muted" role="status">
          {report.error ? <span className="tone-stop">{report.error}</span> : (
            <>Read {report.fetched} · kept {report.stored} job emails · ignored {report.ignored}
              {report.needs_action > 0 && <> · <strong>{report.needs_action} need you</strong></>}
              {report.status_changes.map((c) => <span key={c}> · {c}</span>)}</>
          )}
        </p>
      )}
      <ErrorNote error={poll.error ?? disconnect.error} />
    </div>
  );
}

function Suggestion({ email }: { email: EmailRow }) {
  const apply = useApplySuggestion();
  if (!email.suggested_status || !email.application) return null;
  return (
    <div className="mail-suggest">
      <p>
        Suggests moving <strong>{email.application.title}</strong> to{" "}
        <Badge tone="amber">{email.suggested_status}</Badge>. The sender isn't verified as{" "}
        {email.application.company}, so this waits for you.
      </p>
      <Button busy={apply.isPending} onClick={() => apply.mutate(email.id)}>
        Apply {email.suggested_status}
      </Button>
      <ErrorNote error={apply.error} />
    </div>
  );
}

function ReplyAgain({ id }: { id: number }) {
  const again = useReplyAgain();
  return (
    <>
      <Button busy={again.isPending} onClick={() => again.mutate(id)}>Reply again</Button>
      <ErrorNote error={again.error} />
    </>
  );
}

function EmailItem({ email }: { email: EmailRow }) {
  return (
    <article className={`mail-row ${email.action_status === "needs_action" ? "mail-row-hot" : ""}`}>
      <div className="mail-row-main">
        <p className="mail-row-meta">
          <Badge tone={CATEGORY_TONE[email.category] ?? "neutral"}>{CATEGORY_LABEL[email.category] ?? email.category}</Badge>
          <span className="muted">{email.sender}</span>
          <span className="muted">· {relativeTime(email.received_at)}</span>
        </p>
        <h3 className="mail-row-subject">{email.subject}</h3>
        <p className="mail-row-summary">{email.summary}</p>
        {email.application && (
          <p className="muted">
            Linked to <strong>{email.application.title}</strong> at {email.application.company}
            {" "}({email.link_reason}) · <Badge tone="sky">{email.application.status}</Badge>
          </p>
        )}
        <Suggestion email={email} />
      </div>
      <div className="app-side">
        <span className={email.action_status === "needs_action" ? "tone-amber" : "muted"}>
          {ACTION_LABEL[email.action_status] ?? email.action_status}
        </span>
        {email.reply_open && <Link className="btn btn-secondary" to="/approvals">Open decision</Link>}
        {!email.reply_open && email.action_status !== "replied" && <ReplyAgain id={email.id} />}
        {email.classified_by === "rules" && <span className="muted" title="The model was unavailable">by rules</span>}
      </div>
    </article>
  );
}

export function Inbox() {
  const status = useMailStatus();
  const [filter, setFilter] = useState<string>("action");
  const emails = useEmails(filter);
  const data = status.data;
  const channels = data?.notifications ?? [];

  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Cockpit</p>
        <h1 className="page-title">Inbox</h1>
        <p className="page-lede">
          Job emails only: interviews, recruiters, tests, offers and rejections, linked to your
          applications. Anything that needs you becomes a decision (H6); replies are sent only
          after you approve the final text (H7).
        </p>
      </header>

      <Panel eyebrow="Mailbox" title={data?.account ? "Connected" : "Connect your mailbox"}>
        {data && (data.account ? <Account status={data} /> : <Connect status={data} />)}
        <ErrorNote error={status.error} />
        {channels.length > 0 && (
          <p className="muted mail-channels">
            <MailCheck size={14} /> Notifications:{" "}
            {channels.map((c) => (
              <span key={c.channel} title={c.reason} className={c.available ? "" : "tone-stop"}>
                {c.channel}{c.available ? "" : " (off)"}{" "}
              </span>
            ))}
          </p>
        )}
      </Panel>

      <section className="section" aria-labelledby="mail-title">
        <div className="section-head">
          <h2 id="mail-title" className="section-title">Job emails</h2>
          <div className="segmented" role="group" aria-label="Filter emails">
            {FILTERS.map(([id, label]) => (
              <button key={id} type="button" className={filter === id ? "seg-on" : ""}
                      aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>
            ))}
          </div>
        </div>
        <ErrorNote error={emails.error} />
        {emails.data?.length === 0 && (
          <Empty title={filter === "action" ? "Nothing needs you" : "No emails here yet"}>
            {data?.account ? "New job emails appear after the next check." : "Connect a mailbox to start."}
          </Empty>
        )}
        <div className="app-list">
          {(emails.data ?? []).map((e) => <EmailItem key={e.id} email={e} />)}
        </div>
      </section>
    </div>
  );
}
