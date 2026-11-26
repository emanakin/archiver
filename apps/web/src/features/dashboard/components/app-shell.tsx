import { useEffect, useState, type ReactNode } from "react";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  BarChart3,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  FileStack,
  FlaskConical,
  Home,
  Layers3,
  LayoutDashboard,
  ListChecks,
  MoreHorizontal,
  Plus,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useIdentity, WorkspaceSignOut } from "../lib/identity";
import { routes, useRouter } from "../lib/router";
import type { FixtureState } from "../lib/types";
import { Badge, Button, Card, Modal, Select } from "./ui";
import { Avatar, FooterNote, PauseControl } from "./shared";

const navIcons = [LayoutDashboard, Layers3, Users, ListChecks, BookOpen, Activity, Settings2, BarChart3];

function Brand({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand ${small ? "brand-small" : ""}`}>
      <svg
        className="brand-mark"
        width="29"
        height="30"
        viewBox="0 0 29 30"
        fill="none"
        aria-hidden="true"
      >
        <rect x="2" y="4" width="5" height="23" rx="1.2" fill="currentColor" />
        <rect x="10" y="4" width="5" height="23" rx="1.2" fill="currentColor" />
        <rect
          x="17.3"
          y="4.9"
          width="5"
          height="23"
          rx="1.2"
          transform="rotate(-15 17.3 4.9)"
          fill="currentColor"
        />
        <path d="M1 1H14" stroke="currentColor" strokeWidth="1.4" />
      </svg>
      <span>
        archive desk<span className="brand-period">.</span>
      </span>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname, navigate } = useRouter();
  const { data, service, perform } = useArchive();
  const identity = useIdentity();
  const [more, setMore] = useState(false);
  const [guide, setGuide] = useState(false);
  const [tools, setTools] = useState(false);
  const [workspace, setWorkspace] = useState(false);
  const currentPage = routes.find((route) => route.path === pathname)?.label ?? (pathname === "/content" ? "Content" : "Page not found");
  useEffect(() => {
    document.title = `${currentPage} — Archive Desk`;
  }, [currentPage]);
  const nav = (to: string) => {
    navigate(to);
    setMore(false);
  };
  const stateLabel =
    (data.account.mode === "live" && ["expired", "disconnected"].includes(data.account.session_state))
      ? "Disconnected"
      : data.account.restricted || data.account.session_state === "needs_verification"
        ? "Needs attention"
        : data.account.cooldown_until
          ? "Cooling down"
          : data.account.paused
            ? "Paused"
            : "Running";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="sidebar">
        <a
          href="/"
          className="brand-link"
          aria-label="Archive Desk overview"
          onClick={(event) => {
            event.preventDefault();
            navigate("/");
          }}
        >
          <Brand />
        </a>
        <div className="sidebar-workspace-label">PERSONAL WORKSPACE</div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {routes.map((route, index) => {
            const Icon = navIcons[index];
            return (
              <a
                key={route.path}
                href={route.path}
                aria-current={pathname === route.path ? "page" : undefined}
                className={`nav-link ${pathname === route.path ? "active" : ""}`}
                onClick={(event) => {
                  event.preventDefault();
                  navigate(route.path);
                }}
              >
                <Icon size={18} strokeWidth={1.7} />
                <span>{route.label}</span>
                {route.path === "/candidates" && (
                  <span className="nav-count">{data.overview.candidates_available}</span>
                )}
                {route.path === "/queue" && (
                  <span className="nav-count">
                    {data.jobs.filter((j) => ["queued", "blocked"].includes(j.state)).length}
                  </span>
                )}
              </a>
            );
          })}
          <div className="nav-divider" />
          <button className={`nav-link ${pathname === "/content" ? "active" : ""}`} onClick={() => navigate("/content")}>
            <FileStack size={18} strokeWidth={1.6} />
            <span>Content</span>
            <span className="phase-tag">DRAFTS</span>
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="little-cross">
              <Plus size={20} strokeWidth={1} />
            </span>
            <p>
              Good things,
              <br />
              <span>thoughtfully collected.</span>
            </p>
            <div className="sidebar-note-line" />
          </div>
          <button className="sidebar-utility" onClick={() => setGuide(true)}>
            <CircleHelp size={17} />
            <span>Around the desk</span>
            <ArrowUpRight size={13} />
          </button>
          
          <button className="workspace-button" onClick={() => setWorkspace(true)}>
            <Avatar username="archive desk" />
            <span>
              <strong>Personal workspace</strong>
              <small>Just you. For now.</small>
            </span>
            <ChevronDown size={14} />
          </button>
        </div>
      </aside>
      <div className="app-main">
        <header className="global-header">
          <div className="header-breadcrumb">
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{currentPage}</strong>
          </div>
          <div className="mobile-brand">
            <Brand small />
          </div>
          <div className="account-header">
            <button
              className="header-account"
              onClick={() => navigate("/settings")}
              aria-label={`Open settings for ${data.account.username}`}
            >
              <Avatar username={data.account.username} size="small" />
              <span>@{data.account.username}</span>
              <ChevronDown size={12} />
            </button>
            <Badge
              tone={
                stateLabel === "Running" ? "green" : stateLabel === "Disconnected" ? "red" : "amber"
              }
              dot
              className="account-state"
            >
              {stateLabel}
            </Badge>
            <span className="header-divider" />
            <span className="simulation-mode">
              <FlaskConical size={14} />
              <span>{data.account.mode === "mock" ? "Simulation" : data.connection?.transport_mode === "read_only" ? "Read-only" : "Instagram"}</span>
            </span>
            <PauseControl compact />
          </div>
        </header>
        <div className="mobile-account-context">
          <Avatar username={data.account.username} size="small" />
          <span>@{data.account.username}</span>
          <Badge tone={stateLabel === "Running" ? "green" : "amber"} dot>
            {stateLabel}
          </Badge>
          <span>{data.account.mode === "mock" ? "No live connection" : data.connection?.transport_mode === "read_only" ? "Read-only" : "Instagram account"}</span>
        </div>
        <main id="main-content" className="main-content" tabIndex={-1}>
          {data.fixture === "stale" && (
            <div className="stale-banner">
              <ClockIcon />
              <span>You’re viewing sample data from 3 days ago. Record updates may conflict.</span>
              <button onClick={() => service.setFixture("normal")}>
                Refresh workspace
                <ArrowUpRight size={13} />
              </button>
            </div>
          )}
          <p className="quiet-note" role="status">Worker: {data.worker_status ?? "unavailable"} · Last refreshed: {new Date(data.last_synced_at).toLocaleTimeString([], { timeZone: data.settings.timezone })} ({data.settings.timezone})</p>
          {children}
          <FooterNote />
        </main>
      </div>
      <nav className="mobile-nav" aria-label="Mobile navigation">
        {[
          { path: "/", label: "Home", icon: Home },
          { path: "/sources", label: "Sources", icon: Layers3 },
          { path: "/queue", label: "Queue", icon: ListChecks },
          { path: "/ledger", label: "Ledger", icon: BookOpen },
        ].map(({ path, label, icon: Icon }) => (
          <a
            key={path}
            href={path}
            className={pathname === path ? "active" : ""}
            aria-current={pathname === path ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault();
              nav(path);
            }}
          >
            <Icon size={20} strokeWidth={1.7} />
            <span>{label}</span>
          </a>
        ))}
        <button
          className={
            ["/candidates", "/activity", "/settings", "/content", "/insights"].includes(pathname) || more ? "active" : ""
          }
          onClick={() => setMore(true)}
        >
          <MoreHorizontal size={21} />
          <span>More</span>
        </button>
      </nav>
      <Modal
        open={more}
        onOpenChange={setMore}
        title="A little more"
        description="The rest of your workspace."
        className="mobile-more"
      >
        <div className="more-links">
          {[
            { path: "/candidates", label: "Candidates", icon: Users },
            { path: "/activity", label: "Activity", icon: Activity },
            { path: "/settings", label: "Settings", icon: Settings2 },
            { path: "/insights", label: "Insights", icon: BarChart3 },
            { path: "/content", label: "Content", icon: FileStack },
          ].map(({ path, label, icon: Icon }) => (
            <button key={path} onClick={() => nav(path)}>
              <Icon size={19} />
              <span>{label}</span>
              <ChevronRight size={16} />
            </button>
          ))}
          <button
            onClick={() => {
              setMore(false);
              setGuide(true);
            }}
          >
            <CircleHelp size={19} />
            <span>Around the desk</span>
            <ChevronRight size={16} />
          </button>
          
        </div>
      </Modal>
      <Modal
        open={guide}
        onOpenChange={setGuide}
        title="Make yourself at home"
        description="Archive Desk is a private, single-owner workspace for a more considered Instagram archive."
      >
        <div className="guide-content">
          <div className="guide-callout">
            <FlaskConical size={21} />
            <div>
              <strong>{data.account.mode === "mock" ? "A persistent simulation workspace." : "Your archive account workspace."}</strong>
              <p>
                {data.account.mode === "mock" ? "Settings, sources, jobs and activity are saved in the database. Discovery and actions are simulated; no Instagram requests are sent." : "The backend performs serialized, budgeted requests. Live actions require explicit enablement; account recovery never enables them automatically."}
              </p>
            </div>
          </div>
          <ol className="guide-steps">
            <li>
              <span>01</span>
              <div>
                <strong>Find your starting points</strong>
                <p>Add a profile or post in Sources and collect a small sample.</p>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <strong>Curate, then queue</strong>
                <p>Review eligibility in Candidates. Queueing never immediately follows anyone.</p>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <strong>Keep the relationships that matter</strong>
                <p>
                  Protection prevents acquisition and cleanup. The ledger remembers previously
                  followed accounts.
                </p>
              </div>
            </li>
          </ol>
          <Button className="w-full" onClick={() => setGuide(false)}>
            Back to the desk
            <ArrowUpRight size={15} />
          </Button>
        </div>
      </Modal>
      <Modal
        open={workspace}
        onOpenChange={setWorkspace}
        title="A desk of your own"
        description="Your private Instagram archive workspace."
      >
        <div className="workspace-modal">
          <Avatar username={data.account.username} size="large" />
          <h3>@{data.account.username}</h3>
          <Badge tone="neutral">
            <ShieldCheck size={12} />
            {stateLabel}
          </Badge>
          <p>
            Add, connect or switch Instagram accounts in Settings. Each account keeps its own sources, history and preferences.
          </p>
          {identity?.email && <p>Signed in as {identity.email}</p>}
          <Button
            variant="outline"
            onClick={() => {
              setWorkspace(false);
              navigate("/settings");
            }}
          >
            Account settings
            <Settings2 size={15} />
          </Button>
          {identity?.auth_mode === "google" && <WorkspaceSignOut />}
        </div>
      </Modal>
    </div>
  );
}

function ClockIcon() {
  return <Clock3 size={15} />;
}
