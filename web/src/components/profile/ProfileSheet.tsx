import { Pencil } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useProfileAnswers, type Profile } from "../../api/queries";
import { EDITABLE_FIELDS, formatValue } from "../../lib/profileFields";
import { Badge, Button, ErrorNote, Field } from "../ui";

function money(value: Profile["expected_ctc"]): string {
  if (!value) return "—";
  // Indian digit grouping (24,00,000) for INR; the viewer's locale otherwise.
  const locale = value.currency === "INR" ? "en-IN" : undefined;
  try {
    const amount = new Intl.NumberFormat(locale, {
      style: "currency",
      currency: value.currency,
      maximumFractionDigits: 0,
    }).format(Number(value.amount));
    return `${amount} / ${value.period}`;
  } catch {
    return `${value.currency} ${Number(value.amount).toLocaleString(locale)} / ${value.period}`;
  }
}

export function ProfileSheet({ profile }: { profile: Profile }) {
  const [editing, setEditing] = useState(false);
  if (editing) return <ProfileEditor profile={profile} onDone={() => setEditing(false)} />;

  const skills = profile.skills ?? [];
  return (
    <article className="sheet">
      <header className="sheet-head">
        <div>
          <h2 className="sheet-name">{profile.full_name || "Unnamed pilot"}</h2>
          <p className="sheet-role">
            {[profile.current_role, profile.current_company].filter(Boolean).join(" · ") || "Role not set"}
          </p>
        </div>
        <Button variant="ghost" onClick={() => setEditing(true)}>
          <Pencil size={15} /> Edit
        </Button>
      </header>
      <dl className="sheet-facts">
        <div>
          <dt>Experience</dt>
          <dd className="mono">{profile.total_experience_years ?? "—"} yrs</dd>
        </div>
        <div>
          <dt>Notice</dt>
          <dd className="mono">{profile.notice_period_days ?? "—"} days</dd>
        </div>
        <div>
          <dt>Current CTC</dt>
          <dd className="mono">{money(profile.current_ctc)}</dd>
        </div>
        <div>
          <dt>Expected CTC</dt>
          <dd className="mono">{money(profile.expected_ctc)}</dd>
        </div>
        <div>
          <dt>Locations</dt>
          <dd>{(profile.preferred_locations ?? []).join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Work modes</dt>
          <dd>{(profile.work_modes ?? []).join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Target roles</dt>
          <dd>{(profile.target_roles ?? []).join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>Contact</dt>
          <dd className="mono">{[profile.email, profile.phone].filter(Boolean).join(" · ") || "—"}</dd>
        </div>
      </dl>
      <div className="sheet-skills">
        <p className="eyebrow">Skills</p>
        <div className="tags">
          {skills.length === 0 && <span className="muted">None yet</span>}
          {skills.map((s) => (
            <Badge key={s.name}>
              {s.name}
              {s.years != null && <span className="mono"> · {s.years}y</span>}
            </Badge>
          ))}
        </div>
      </div>
    </article>
  );
}

function ProfileEditor({ profile, onDone }: { profile: Profile; onDone: () => void }) {
  const initial: Record<string, string> = Object.fromEntries(
    EDITABLE_FIELDS.map((f) => [f.name, formatValue(profile[f.name as keyof Profile])]),
  );
  const [values, setValues] = useState<Record<string, string>>(initial);
  const save = useProfileAnswers();

  function submit(event: FormEvent) {
    event.preventDefault();
    const changed = Object.fromEntries(
      Object.entries(values).filter(([name, value]) => value !== initial[name] && value.trim()),
    );
    if (!Object.keys(changed).length) return onDone();
    save.mutate(changed, { onSuccess: onDone });
  }

  return (
    <form className="sheet sheet-edit" onSubmit={submit}>
      <div className="form-grid">
        {EDITABLE_FIELDS.map((f) => (
          <Field key={f.name} label={f.label} hint={f.hint}>
            <input
              className="input"
              value={values[f.name] ?? ""}
              onChange={(e) => setValues({ ...values, [f.name]: e.target.value })}
            />
          </Field>
        ))}
      </div>
      <ErrorNote error={save.error} />
      <div className="form-actions">
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button variant="primary" type="submit" busy={save.isPending}>
          Save as new version
        </Button>
      </div>
    </form>
  );
}
