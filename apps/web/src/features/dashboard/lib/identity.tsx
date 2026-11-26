"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Button, Card } from "../components/ui";
import { CircularLoader } from "../components/account-connection";

type Identity = { auth_mode: "legacy" | "google"; email: string | null; workspace_id: string | null };
const IdentityContext = createContext<Identity | null>(null);

export function useIdentity() {
  return useContext(IdentityContext);
}

function SignIn() {
  const query = useSearchParams();
  const next = query.get("next") || "/";
  const error = query.get("error");
  return (
    <main className="page" style={{ maxWidth: 540, marginInline: "auto", paddingBlock: "12vh" }}>
      <Card className="paused-page">
        <p className="paused-label">ARCHIVE DESK</p>
        <h1>Sign in to your workspace</h1>
        <p>Your Instagram accounts, drafts, and activity are private to your workspace.</p>
        {error && <p role="alert">{error === "signup_closed" ? "New workspace signup is closed." : "Google sign-in did not complete. Try again."}</p>}
        <a className="button button-primary" href={`/auth/google/start?return_to=${encodeURIComponent(next)}`}>Continue with Google</a>
      </Card>
    </main>
  );
}

export function IdentityGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [failed, setFailed] = useState(false);
  const login = pathname === "/login";
  useEffect(() => {
    if (login) return;
    let cancelled = false;
    fetch("/api/v1/me", { cache: "no-store", credentials: "same-origin" })
      .then(async response => {
        if (cancelled) return;
        if (response.status === 401) {
          const next = window.location.pathname + window.location.search;
          window.location.replace(`/login?next=${encodeURIComponent(next)}`);
          return;
        }
        if (!response.ok) throw new Error();
        const value: Identity = await response.json();
        if (!cancelled) setIdentity(value);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [login]);
  if (login) return <SignIn />;
  if (failed) return (
    <main className="page"><Card className="paused-page">
      <h1>Workspace temporarily unavailable</h1>
      <Button onClick={() => window.location.reload()}>Try again</Button>
    </Card></main>
  );
  if (!identity) return <CircularLoader full label="Opening your workspace" />;
  return <IdentityContext.Provider value={identity}>{children}</IdentityContext.Provider>;
}

export function WorkspaceSignOut() {
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  return <>
    <Button disabled={busy} onClick={async () => {
      setBusy(true);
      setFailed(false);
      try {
        const result = await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
        if (!result.ok) throw new Error();
        window.location.replace("/login");
      } catch {
        setFailed(true);
        setBusy(false);
      }
    }}>Sign out</Button>
    {failed && <p role="alert">Sign-out failed. Try again.</p>}
  </>;
}
