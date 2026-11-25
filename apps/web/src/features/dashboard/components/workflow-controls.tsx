"use client";
import { useState } from "react";
import { useArchive } from "../lib/archive-context";
import { Badge, Button, Card, ConfirmDialog } from "./ui";

export function WorkflowControls() {
  const { data, service, notify } = useArchive();
  const [confirm, setConfirm] = useState<"acquisition" | "cleanup" | null>(null);
  const [busy, setBusy] = useState(false);
  if (data.account.mode === "mock") return null;
  const ready = data.connection?.transport_mode === "live" && data.account.session_state === "connected" && data.connection.capabilities.bounded_collection_verified;
  const update = async (workflow: "acquisition" | "cleanup", enabled: boolean) => {
    setBusy(true);
    try {
      await service.setWorkflow(workflow, enabled, data.account.workflow_version ?? 1);
      setConfirm(null);
      notify(`${workflow === "acquisition" ? "Candidate follows" : "Managed cleanup"} ${enabled ? "enabled" : "disabled"}.`);
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not update this workflow.", "error");
    } finally { setBusy(false); }
  };
  if (data.connection?.transport_mode !== "live") return <Card className="settings-card"><div className="settings-card-heading"><div><h2>Follow and cleanup automations</h2><p>Currently unavailable</p></div><Badge>PAUSED</Badge></div><div className="settings-card-body"><p>This workspace supports real account connections and read-only collection. Following and cleanup remain paused until live action support is validated and enabled.</p></div></Card>;
  return <Card className="settings-card">
    <div className="settings-card-heading"><div><h2>Live workflow enablement</h2><p>Connection and queue resume do not enable these workflows.</p></div><Badge>{ready ? "OWNER CONTROLLED" : "VALIDATION REQUIRED"}</Badge></div>
    <div className="settings-card-body">
      {!ready && <p>Use read-only collection first, then configure live transport and positive action/request allowances. Each workflow still needs explicit enablement here.</p>}
      {(["acquisition", "cleanup"] as const).map(workflow => {
        const enabled = workflow === "acquisition" ? !!data.account.live_acquisition_enabled : !!data.account.live_cleanup_enabled;
        return <div className="flex flex-wrap items-center justify-between gap-4 py-4" key={workflow}>
          <div><strong>{workflow === "acquisition" ? "Candidate follows" : "Managed cleanup"}</strong><p>{workflow === "acquisition" ? ((data.settings.ranked_follow_mode ?? "review") === "review" ? "Only candidates you approve are queued." : "Eligible candidates are selected automatically using your saved selection and pacing settings.") : `Only due managed relationships; keeps follow-backs. Retention: ${data.settings.retention_days} days.`}</p></div>
          <Button variant={enabled ? "outline" : "default"} disabled={busy || (!enabled && !ready)} onClick={() => enabled ? void update(workflow, false) : setConfirm(workflow)}>
            {enabled ? "Disable" : "Enable"} {workflow === "acquisition" ? "follows" : "cleanup"}
          </Button>
        </div>;
      })}
      <p>Disabling prevents the next dispatch. An action already sent cannot be recalled.</p>
    </div>
    <ConfirmDialog open={!!confirm} onOpenChange={open => { if (!open) setConfirm(null); }}
      title={`Enable ${confirm === "acquisition" ? "candidate follows" : "managed cleanup"}?`}
      description={confirm === "acquisition" ? "Follow jobs may run when the queue is resumed, using your saved selection mode and pacing. Existing and previously touched accounts remain excluded." : "Due managed relationships may be unfollowed when the queue is resumed. Protected/excluded accounts and accounts that follow back are preserved; unknown outcomes block another write."}
      confirmLabel="Enable this workflow" loading={busy} onConfirm={() => { if (confirm) void update(confirm, true); }} />
  </Card>;
}
