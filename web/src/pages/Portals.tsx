import { usePortals } from "../api/queries";
import { PortalCard } from "../components/portals/PortalCard";
import { ErrorNote } from "../components/ui";

export function Portals() {
  const portals = usePortals();
  return (
    <div className="page">
      <header className="page-head">
        <p className="eyebrow">Engines</p>
        <h1 className="page-title">Portals</h1>
        <p className="page-lede">
          Job sites JobPilot may search as you. Every portal is off until you enable it. When a site
          asks for a CAPTCHA, a code or flags unusual activity, JobPilot stops and hands the browser to
          you; it never tries to get around those checks.
        </p>
      </header>
      <ErrorNote error={portals.error} />
      <div className="portal-grid">
        {(portals.data ?? []).map((p) => <PortalCard key={p.name} portal={p} />)}
      </div>
    </div>
  );
}
