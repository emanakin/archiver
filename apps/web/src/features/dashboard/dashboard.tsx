"use client";
import { Suspense, useState } from "react";
import { ArchiveProviderRoot, useArchive } from "./lib/archive-context";
import { useRouter } from "./lib/router";
import { AppShell } from "./components/app-shell";
import { SourceForm } from "./components/source-form";
import { OverviewPage } from "./pages/overview";
import { SourcesPage } from "./pages/sources";
import { CandidatesPage } from "./pages/candidates";
import { QueuePage } from "./pages/queue";
import { LedgerPage } from "./pages/ledger";
import { ActivityPage } from "./pages/activity";
import { SettingsPage } from "./pages/settings";
import { InsightsPage } from "./pages/insights";
import { ContentPage } from "./pages/content";
import { PAGE_SIZE } from "./lib/service";
import { AccountConnection, CircularLoader } from "./components/account-connection";
import { Button, Card } from "./components/ui";
import { PageHeader } from "./components/shared";

function Application() {
  const { pathname, search, navigate } = useRouter();
  const { data, loading } = useArchive();
  const [sourceForm, setSourceForm] = useState(false);
  const [sourceType, setSourceType] = useState<"profile" | "mutual" | "post">("profile");
  const addSource = (type: typeof sourceType = "profile") => { setSourceType(type); setSourceForm(true); };
  const pages: Record<string, React.ReactNode> = {
    "/": <OverviewPage onAddSource={() => addSource()} />, "/sources": <SourcesPage onAddSource={addSource} />,
    "/candidates": <CandidatesPage />, "/queue": <QueuePage />, "/ledger": <LedgerPage />,
    "/activity": <ActivityPage />, "/settings": <SettingsPage />,
    "/insights": <InsightsPage />,
    "/content": <ContentPage />,
  };
  const connected = data.account.session_state === "connected" && !data.account.restricted;
  const pausedReads = !connected && ["/sources", "/candidates"].includes(pathname);
  if (loading) return <AppShell><CircularLoader full label="Loading page" /></AppShell>;
  return <AppShell>
    {pathname === "/" && !connected ? <div className="page"><PageHeader title="Welcome to your desk" eyebrow="YOUR INSTAGRAM ARCHIVE" description="Connect your account to start using your archive. Your sources, history and preferences stay in this workspace." /><AccountConnection accountId={data.account.id} username={data.account.username} restricted={data.account.restricted} onStarted={id => navigate(`/settings?account=${id}`)} /></div>
      : pausedReads ? <PausedPage title={pathname === "/sources" ? "Sources" : "Candidates"} description="This page is paused until Instagram is connected. Add or reconnect your account in Settings to collect real account data." action={() => navigate("/settings")} />
      : pages[pathname] ?? <PausedPage title="Page not found" description="This page is not available. Return to Overview to continue." action={() => navigate("/")} />}
    <SourceForm open={sourceForm} onOpenChange={setSourceForm} initialType={sourceType} />
  </AppShell>;
}
export function Dashboard() {
  return <Suspense fallback={<CircularLoader full label="Opening Archive Desk" />}><ArchiveProviderRoot><Application /></ArchiveProviderRoot></Suspense>;
}

function PausedPage({ title, description, action }: { title: string; description: string; action?: () => void }) {
  return <div className="page"><PageHeader title={title} eyebrow="WORKSPACE" description="Everything has its place." /><Card className="paused-page"><span className="paused-icon">Ⅱ</span><span className="paused-label">Currently unavailable</span><h2>This page is paused</h2><p>{description}</p>{action && <Button onClick={action}>Open account settings</Button>}</Card></div>;
}
