import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router";
import { useDecisions, useMissing, useProfile, useVersions } from "../api/queries";
import { AutopilotCard } from "../components/mission/AutopilotCard";
import { LiveFeed } from "../components/mission/LiveFeed";
import { NotificationsCard } from "../components/mission/NotificationsCard";
import { Pipeline } from "../components/mission/Pipeline";
import { WaitingList } from "../components/mission/WaitingList";

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export function MissionControl() {
  const profile = useProfile();
  const versions = useVersions();
  const missing = useMissing();
  const decisions = useDecisions(false).data ?? [];
  const name = profile.data?.full_name?.split(" ")[0];
  const setupLeft = (versions.data?.length ? 0 : 1) + (missing.data?.length ? 1 : 0);
  const now = new Date();

  return (
    <div className="page mc">
      <header className="mc-hero">
        <p className="eyebrow">{now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}</p>
        <h1 className="mc-title">
          {greeting()}{name ? `, ${name}` : ""}.{" "}
          {decisions.length > 0
            ? <span className="mc-title-accent">{decisions.length} {decisions.length === 1 ? "decision waits" : "decisions wait"} for you.</span>
            : <span className="mc-title-calm">Nothing needs you right now.</span>}
        </h1>
        <p className="mc-lede">
          Agents search, rank, fill forms and draft replies on their own. Nothing is submitted or
          sent until you approve it, here or from your phone.
        </p>
        {setupLeft > 0 && (
          <Link to="/setup" className="mc-setup">
            Finish setting up: {setupLeft === 2 ? "import your CV and fill the missing details" : versions.data?.length ? "fill the missing details" : "import your CV"}
            <ArrowUpRight size={16} />
          </Link>
        )}
      </header>

      <Pipeline />

      <div className="mc-grid">
        <div className="mc-main">
          <WaitingList />
          <LiveFeed />
        </div>
        <aside className="mc-side">
          <AutopilotCard />
          <NotificationsCard />
        </aside>
      </div>
    </div>
  );
}
