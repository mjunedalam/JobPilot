import {
  BookOpenText,
  Cpu,
  Gauge,
  Inbox,
  ListChecks,
  Menu,
  Moon,
  Plug,
  Monitor,
  Search,
  ShieldAlert,
  Sun,
  UserRound,
  BarChart3,
  X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { useTheme } from "../lib/theme";
import { Annunciator } from "./Annunciator";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
}

const SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Cockpit",
    items: [
      { to: "/", label: "Flight deck", icon: <Gauge size={17} /> },
      { to: "/jobs", label: "Jobs", icon: <Search size={17} /> },
      { to: "/approvals", label: "Approvals", icon: <ListChecks size={17} /> },
    ],
  },
  {
    title: "Pilot",
    items: [
      { to: "/profile", label: "Profile", icon: <UserRound size={17} /> },
      { to: "/answers", label: "Answer bank", icon: <BookOpenText size={17} /> },
    ],
  },
  {
    title: "Engines",
    items: [
      { to: "/models", label: "Models", icon: <Cpu size={17} /> },
      { to: "/portals", label: "Portals", icon: <Plug size={17} /> },
    ],
  },
  { title: "Ops", items: [{ to: "/system", label: "System", icon: <ShieldAlert size={17} /> }] },
];

// Shown, not linked: these arrive with later build phases.
const UPCOMING = [
  { label: "Inbox", phase: 4, icon: <Inbox size={17} /> },
  { label: "Reports", phase: 5, icon: <BarChart3 size={17} /> },
];

export function Shell() {
  const [open, setOpen] = useState(false);
  const [theme, cycleTheme] = useTheme();
  const location = useLocation();
  const ThemeIcon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;

  return (
    <div className="shell">
      <aside className={`rail ${open ? "rail-open" : ""}`} aria-label="Main navigation">
        <div className="rail-brand">
          <span className="brand-mark" aria-hidden>
            <svg viewBox="0 0 32 32" width="28" height="28">
              <circle cx="16" cy="16" r="14" fill="var(--amber)" />
              <path d="M9 18l14-7-5 13-2-5z" fill="var(--card)" />
            </svg>
          </span>
          <span>
            <span className="brand-name">JobPilot</span>
            <span className="brand-sub">flight deck</span>
          </span>
          <button className="rail-close" onClick={() => setOpen(false)} aria-label="Close menu">
            <X size={18} />
          </button>
        </div>
        <nav>
          {SECTIONS.map((section) => (
            <div className="rail-section" key={section.title}>
              <p className="eyebrow rail-eyebrow">{section.title}</p>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className="rail-link"
                  onClick={() => setOpen(false)}
                >
                  {item.icon}
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
          <div className="rail-section">
            <p className="eyebrow rail-eyebrow">On the roadmap</p>
            {UPCOMING.map((item) => (
              <span className="rail-link rail-link-locked" key={item.label} aria-disabled>
                {item.icon}
                {item.label}
                <span className="phase-tag">P{item.phase}</span>
              </span>
            ))}
          </div>
        </nav>
        <button className="theme-toggle" onClick={cycleTheme}>
          <ThemeIcon size={15} /> Theme: {theme}
        </button>
      </aside>
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <div className="main">
        <div className="topbar">
          <button className="menu-btn" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu size={20} />
          </button>
          <Annunciator />
        </div>
        <main className="content" key={location.pathname}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
