"use client";
import { useEffect, useState } from "react";
import { useArchive } from "../lib/archive-context";
import type { Source } from "../lib/types";
import { Button, Field, Input, Modal, Switch } from "./ui";

export function SourceSchedule({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const { data, service, notify } = useArchive();
  const [enabled, setEnabled] = useState(false);
  const [time, setTime] = useState("09:00");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (source) { setEnabled(!!source.schedule_enabled); setTime(source.schedule_local_time ?? "09:00"); } }, [source]);
  return <Modal open={!!source} onOpenChange={open => { if (!open && !busy) onClose(); }} title="Daily source collection"
    description="Collect a bounded batch once per local day. Follow approval stays manual.">
    <form className="source-form" onSubmit={async event => {
      event.preventDefault();
      if (!source) return;
      setBusy(true);
      try { await service.updateSourceSchedule(source.id, enabled, time, source.schedule_version ?? 1); notify("Collection schedule saved. Follow approval stays manual."); onClose(); }
      catch (error) { notify(error instanceof Error ? error.message : "Could not save this schedule.", "error"); }
      finally { setBusy(false); }
    }}>
      <Switch label="Enable daily collection" checked={enabled} onCheckedChange={setEnabled} disabled={busy} />
      <Field label="Daily collection time" htmlFor="source-schedule-time" hint={`Uses ${data.settings.timezone}. Paused accounts retain queued work.`}>
        <Input id="source-schedule-time" type="time" required value={time} onChange={event => setTime(event.target.value)} disabled={busy} />
      </Field>
      <p>Collection continues from its saved cursor and starts a new pass when complete. A restart catches up only today's run, without a burst of missed days.</p>
      <div className="dialog-footer"><Button variant="outline" type="button" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Save schedule</Button></div>
    </form>
  </Modal>;
}
