"use client";
import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Instagram, LoaderCircle, LockKeyhole, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { ConnectionLog } from "./connection-log";
import { Badge, Button, Card, Field, Input } from "./ui";

type Progress = components["schemas"]["ConnectionProgress"];
const active = new Set(["queued", "connecting", "awaiting_code", "awaiting_approval", "verifying"]);

export function CircularLoader({ label = "Loading", full = false }: { label?: string; full?: boolean }) {
  return <div className={full ? "workspace-loader" : "inline-loader"} role="status" aria-label={label}>
    <LoaderCircle className="animate-spin" size={full ? 32 : 19} strokeWidth={1.8} aria-hidden="true" />
    <span className="sr-only">{label}</span>
  </div>;
}

export function AccountConnection({ accountId, username = "", restricted = false, connected = false, onStarted, onConnected }: {
  accountId?: string; username?: string; restricted?: boolean; connected?: boolean;
  onStarted?: (id: string) => void; onConnected?: () => void;
}) {
  const client = useQueryClient();
  const fieldId = useId();
  const [handle, setHandle] = useState(username);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [budget, setBudget] = useState(20);
  const [editing, setEditing] = useState(!connected);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const completed = useRef(false);
  const capability = useQuery({ queryKey: ["onboarding-capability"], queryFn: () => api<components["schemas"]["OnboardingCapability"]>("/onboarding-capability"), refetchInterval: 15000 });
  const status = useQuery({ queryKey: ["onboarding", accountId], queryFn: () => api<Progress | null>(`/accounts/${accountId}/onboarding`), enabled: !!accountId,
    refetchInterval: query => query.state.data && active.has(query.state.data.state) ? 1500 : false });
  const progress = status.data;
  const working = !!progress && active.has(progress.state);
  const awaiting = progress?.state === "awaiting_code";
  const approving = progress?.state === "awaiting_approval";
  useEffect(() => {
    if (progress?.state === "connected" && !completed.current) {
      completed.current = true; setEditing(false); onConnected?.();
      void client.invalidateQueries({ queryKey: ["workspace"] });
    }
  }, [progress?.state, client, onConnected]);
  useEffect(() => { if (!awaiting) setCode(""); }, [awaiting]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await api<Progress>("/account-connections", { method: "POST", body: JSON.stringify({ username: handle, password, request_budget: budget,
        recovery_acknowledged: recovery, idempotency_key: crypto.randomUUID() }) });
      setPassword(""); completed.current = false;
      client.setQueryData(["onboarding", result.account_id], result);
      void client.invalidateQueries({ queryKey: ["workspace"] });
      onStarted?.(result.account_id);
      if (accountId) void status.refetch();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not start the connection."); }
    finally { setPassword(""); setBusy(false); }
  };
  const verify = async (event: React.FormEvent) => {
    event.preventDefault(); if (!progress?.challenge_id) return;
    setBusy(true); setError("");
    try {
      const result = await api<Progress>(`/account-connections/${progress.id}/code`, { method: "POST", body: JSON.stringify({ code, challenge_id: progress.challenge_id }) });
      client.setQueryData(["onboarding", accountId], result);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not submit the code."); }
    finally { setCode(""); setBusy(false); }
  };
  const cancel = async () => {
    if (!progress) return; setBusy(true); setError("");
    try { const result = await api<Progress>(`/account-connections/${progress.id}/cancel`, { method: "POST", body: "{}" }); client.setQueryData(["onboarding", accountId], result); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Could not cancel the connection."); }
    finally { setBusy(false); }
  };
  const approve = async (choice: components["schemas"]["ApprovalInput"]["choice"]) => {
    if (!progress?.challenge_id) return;
    setBusy(true); setError("");
    try {
      const result = await api<Progress>(`/account-connections/${progress.id}/approval`, { method: "POST", body: JSON.stringify({ challenge_id: progress.challenge_id, choice }) });
      client.setQueryData(["onboarding", accountId], result);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not continue after approval."); }
    finally { setBusy(false); }
  };
  return <Card className="connection-card">
    <div className="connection-heading"><span className="connection-icon"><Instagram size={23} /></span><div><h2>{connected && !editing ? `@${username}` : accountId ? "Connect your Instagram" : "Add your Instagram account"}</h2><p>A private connection for your archive.</p></div>
      <Badge tone={connected ? "green" : "neutral"}>{connected ? "Connected" : "Not connected"}</Badge></div>
    {capability.isLoading && <CircularLoader label="Checking connection availability" />}
    {capability.isError && <p className="connection-error" role="alert">Could not check the connection worker. <button className="text-link" onClick={() => void capability.refetch()}>Try again</button></p>}
    {capability.data && !capability.data.available && <p className="connection-notice">{capability.data.reason}</p>}
    {status.isError && <p className="connection-error" role="alert">Could not refresh connection progress. <button className="text-link" onClick={() => void status.refetch()}>Refresh</button></p>}
    {working ? <div className="connection-progress">
      <div className="connection-step"><Badge>1 · Account</Badge><span /><Badge tone={awaiting || approving ? "green" : "neutral"}>2 · Verification</Badge></div>
      <div className="connection-progress-heading">{awaiting || approving ? <LockKeyhole size={21} /> : <CircularLoader label="Connecting to Instagram" />}<h3>{approving ? "Approve the login in Instagram" : awaiting ? "Enter your verification code" : progress.state === "verifying" ? progress.challenge_kind === "approval" ? "Checking Instagram approval" : progress.challenge_kind === "approval_code" ? "Opening Instagram’s code screen" : "Verifying your code" : "Connecting to Instagram"}</h3></div>
      <p role="status">{progress.message}</p>
      {approving && <><p>Wait for Instagram to finish the approval on your phone before continuing here. If the phone screen is still loading, leave this connection open or cancel it. Each choice checks the next step once; neither confirms that Instagram accepted the login.</p><div className="connection-choices">
        <Button loading={busy} disabled={!capability.data?.available} onClick={() => void approve("approved")}>I approved on my phone · Continue</Button>
        <Button variant="outline" loading={busy} disabled={!capability.data?.available} onClick={() => void approve("code")}>Instagram is asking me for a code</Button>
      </div></>}
      {awaiting && <form onSubmit={verify} className="connection-form"><Field label="Verification code" htmlFor={`${fieldId}-code`} hint="Use the code requested by Instagram. Keep this page open while you check your phone or authenticator.">
        <Input id={`${fieldId}-code`} name="verification-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6,8}" maxLength={8} autoFocus required value={code} onChange={event => setCode(event.target.value.replace(/\D/g, ""))} className="verification-input" />
      </Field><Button type="submit" loading={busy} disabled={code.length < 6 || !capability.data?.available}>Confirm code</Button></form>}
      <Button variant="ghost" disabled={busy} onClick={() => void cancel()}>Cancel connection</Button>
      <ConnectionLog connectionId={progress.id} live />
    </div> : connected && !editing ? <div className="connection-success"><p><Check size={17} /> Instagram is connected. Automations remain under your control.</p><Button variant="outline" onClick={() => { setEditing(true); completed.current = false; }}>Reconnect account<RefreshCw size={14} /></Button></div> : <>
      {progress && ["failed", "expired", "cancelled"].includes(progress.state) && <div className="connection-notice" role="status">{progress.state === "failed" && <strong>{progress.error_code === "PROFILE_CONTINUATION_UNAVAILABLE" ? "Phone approval acknowledged; connection incomplete" : "Connection incomplete"}</strong>}<p>{progress.message}</p>{progress.error_code && <details><summary>Connection details</summary><p>Support code: {progress.error_code}</p></details>}<ConnectionLog connectionId={progress.id} live={false} /></div>}
      <form onSubmit={submit} className="connection-form">
        <Field label="Instagram username" htmlFor={`${fieldId}-username`}><Input id={`${fieldId}-username`} name="username" autoComplete="username" placeholder="your.username" required maxLength={30} value={handle} onChange={event => setHandle(event.target.value)} disabled={!!accountId} /></Field>
        <Field label="Password" htmlFor={`${fieldId}-password`} hint="Your password is sent once. If Instagram requests a code, you’ll enter it in the next step."><Input id={`${fieldId}-password`} name="password" autoComplete="current-password" type="password" required maxLength={1024} value={password} onChange={event => setPassword(event.target.value)} /></Field>
        {!accountId && <details className="connection-options"><summary>Connection preferences</summary><Field label="Daily request allowance" htmlFor={`${fieldId}-budget`} hint="Counts login, reads and verification. New accounts start with actions disabled."><Input id={`${fieldId}-budget`} type="number" min={12} max={10000} value={budget} onChange={event => setBudget(Number(event.target.value))} /></Field></details>}
        {(restricted || progress?.state === "failed") && <label className="connection-ack"><input type="checkbox" checked={recovery} onChange={event => setRecovery(event.target.checked)} /><span>I’ve reviewed any login or verification prompts in Instagram and am ready to reconnect.</span></label>}
        <Button type="submit" loading={busy} disabled={!capability.data?.available || !handle || !password || ((restricted || progress?.state === "failed") && !recovery)}>{accountId ? "Connect Instagram" : "Add account and continue"}<Plus size={15} /></Button>
      </form>
    </>}
    {error && <p className="connection-error" role="alert">{error}</p>}
    <p className="connection-privacy"><LockKeyhole size={13} /> Passwords and codes are never shown in your ledger or activity. Connecting does not enable follows or cleanup.</p>
  </Card>;
}
