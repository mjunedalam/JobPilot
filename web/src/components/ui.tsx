import {
  cloneElement,
  isValidElement,
  useId,
  type ButtonHTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

type Tone = "neutral" | "amber" | "go" | "sky" | "stop";

export function Button({
  variant = "secondary",
  busy = false,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      className={`btn btn-${variant} ${props.className ?? ""}`}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Panel({
  title,
  eyebrow,
  actions,
  className = "",
  children,
}: {
  title?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || eyebrow || actions) && (
        <header className="panel-head">
          <div>
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            {title && <h2 className="panel-title">{title}</h2>}
          </div>
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** Labels its single input explicitly (htmlFor/id), and links hint and error through
 *  aria-describedby so screen readers announce the label alone as the field's name. */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean }>;
}) {
  const id = useId();
  const noteId = `${id}-note`;
  const control = isValidElement(children)
    ? cloneElement(children, {
        id: children.props.id ?? id,
        "aria-describedby": hint || error ? noteId : undefined,
        "aria-invalid": error ? true : undefined,
      })
    : children;
  const controlId = (isValidElement(children) && children.props.id) || id;
  return (
    <div className="field">
      <label className="field-label" htmlFor={controlId}>
        {label}
      </label>
      {control}
      {hint && !error && (
        <span className="field-hint" id={noteId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="field-error" id={noteId} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export function Lamp({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className={`lamp lamp-${tone}`}>
      <span className="lamp-dot" aria-hidden />
      {label}
    </span>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error || (error instanceof Error && error.name === "Cancelled")) return null; // undone on purpose
  const message = error instanceof Error ? error.message : String(error);
  return (
    <p className="error-note" role="alert">
      {message}
    </p>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}
