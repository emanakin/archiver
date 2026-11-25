"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { components } from "@/lib/api.generated";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import { Badge, Button, Card, ConfirmDialog } from "./ui";

type Check = components["schemas"]["InsightResponse"];
type Case = components["schemas"]["TestResponse"];

export function RelationshipTest({ check }: { check?: Check }) {
  const { data, notify } = useArchive();
  const { navigate } = useRouter();
  const client = useQueryClient();
  const [confirm, setConfirm] = useState<Case | null>(null);
  const [busy, setBusy] = useState(false);
  const cases = useQuery({ queryKey: ["relationship-tests", data.account.id], queryFn: () => api<{ items: Case[] }>(`/relationship-tests?account_id=${data.account.id}`), refetchInterval: 3000 });
  const eligible = check?.kind === "relationship" && check.state === "succeeded" && check.relationship?.state === "none" && check.relationship.follows_back === false && check.profile?.is_private === false;
  const ready = data.connection?.transport_mode === "live" && data.settings.follows_per_day !== 0 && data.settings.unfollows_per_day >= 1;
  return <Card className="relationship-test-card"><div className="settings-card-heading"><div><h2>Controlled follow / unfollow test</h2><p>One selected public account, with a fresh check before each action.</p></div><Badge>Reviewed target</Badge></div><div className="insight-detail-body">
    <p>Choose a relationship observation for an account you do not follow. This test follows it once, verifies the result, then removes the connection it created. Protection, existing history and new follow-backs can stop the test. Completed history stays in the ledger.</p>
    {!ready && <p className="insight-note">Execution is paused until write transport is available and you have allowed at least one follow and one unfollow in Settings. Preparing a test sends no action.</p>}
    <div className="flex flex-wrap gap-3"><Button variant="outline" disabled={busy || !eligible} onClick={async () => {
      if (!check) return; setBusy(true);
      try {
        const result = await api<Case>("/relationship-tests", { method: "POST", body: JSON.stringify({ account_id: data.account.id, check_id: check.id }) });
        await client.invalidateQueries({ queryKey: ["relationship-tests", data.account.id] });
        if (result.state === "prepared") setConfirm(result); else notify(`This target already has a test: ${result.state}.`);
      } catch (error) { notify(error instanceof Error ? error.message : "Could not prepare this test.", "error"); }
      finally { setBusy(false); }
    }}>Prepare selected target test</Button><Button variant="ghost" onClick={() => navigate("/settings")}>Account allowances</Button></div>
    {!eligible && <p className="quiet-note">Select a successful “Our relationship” observation with no existing follow/request or follow-back to prepare a target.</p>}
    {cases.isError && <p role="alert">Test history could not be loaded.</p>}
    {cases.data?.items.map(item => <article className="relationship-test-row" key={item.id}><strong>@{item.username}</strong><Badge tone={item.state === "restored" ? "green" : ["unknown", "review_required"].includes(item.state) ? "amber" : "neutral"}>{item.state === "restored" ? "Original state restored" : item.state.replaceAll("_", " ")}</Badge>{item.error_code && <p>{item.error_code} · Review the linked jobs before another action.</p>}{item.state === "prepared" && <Button variant="outline" size="sm" onClick={() => setConfirm(item)}>Review test</Button>}{(item.follow_job_id || item.unfollow_job_id) && <Button variant="ghost" size="sm" onClick={() => navigate("/queue?tab=review")}>Open queue</Button>}</article>)}
    </div><ConfirmDialog open={!!confirm} onOpenChange={open => { if (!open) setConfirm(null); }} title={`Follow then unfollow @${confirm?.username ?? ""}?`} description="This approves one follow and one immediate test unfollow for this target only. Each action is verified. It does not enable broad acquisition or cleanup. The test stops on restrictions, protection, a follow-back or an uncertain result." confirmLabel="Run this paired test" disabled={!ready} loading={busy} onConfirm={async () => {
      if (!confirm) return; setBusy(true);
      try { await api(`/relationship-tests/${confirm.id}/approve`, { method: "POST", body: "{}" }); setConfirm(null); await client.invalidateQueries({ queryKey: ["relationship-tests", data.account.id] }); await client.invalidateQueries({ queryKey: ["workspace"] }); notify(data.account.paused ? "Test approved and queued. Resume the queue to run it." : "Target test queued."); }
      catch (error) { notify(error instanceof Error ? error.message : "Could not approve this test.", "error"); }
      finally { setBusy(false); }
    }} />
  </Card>;
}
