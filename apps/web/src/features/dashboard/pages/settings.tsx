import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CalendarClock,
  FileStack,
  FlaskConical,
  Globe2,
  Info,
  Save,
  ShieldCheck,
  Sprout,
  TimerReset,
} from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { useRouter } from "../lib/router";
import type { SessionState, Settings } from "../lib/types";
import { validateSettings } from "../lib/utils";
import { Avatar, PageHeader, RecoveryDialog } from "../components/shared";
import { WorkflowControls } from "../components/workflow-controls";
import { Badge, Button, Card, Field, Input, Select, Modal } from "../components/ui";
import { AccountConnection } from "../components/account-connection";

const followPresets = [
  { id: "under-one", label: "Under 1 month", daily: [20, 50], hourly: [5, 10] },
  { id: "one-to-three", label: "1–3 months", daily: [50, 100], hourly: [10, 15] },
  { id: "three-to-six", label: "3–6 months", daily: [100, 150], hourly: [10, 15] },
  { id: "six-plus", label: "6+ months", daily: [150, 200], hourly: [15, 20] },
];

type FollowPreset = typeof followPresets[number];

function hourlyPreset(preset: FollowPreset, daily: number) {
  const progress = (daily - preset.daily[0]) / (preset.daily[1] - preset.daily[0]);
  return Math.round(preset.hourly[0] + progress * (preset.hourly[1] - preset.hourly[0]));
}

export function SettingsPage() {
  const { data, service, notify, perform } = useArchive();
  const { navigate } = useRouter();
  const [form, setForm] = useState<Settings>({ ...data.settings });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [recovery, setRecovery] = useState(false);
  const [addAccount, setAddAccount] = useState(false);
  const [preferredPreset, setPreferredPreset] = useState("");
  const baseline = useRef(data.settings);
  useEffect(() => {
    setForm(current => JSON.stringify(current) === JSON.stringify(baseline.current) ? { ...data.settings } : current);
    baseline.current = data.settings;
  }, [data.settings]);
  const dirty = JSON.stringify(form) !== JSON.stringify(data.settings);
  const dailyPace = form.follows_per_day;
  const matchingPresets = followPresets.filter(preset =>
    dailyPace !== null && form.follow_pacing === "hourly"
    && dailyPace >= preset.daily[0] && dailyPace <= preset.daily[1]
    && form.follows_per_hour_min === hourlyPreset(preset, dailyPace)
    && form.follows_per_hour_max === form.follows_per_hour_min
  );
  const activePreset = matchingPresets.find(preset => preset.id === preferredPreset) ?? matchingPresets[0];
  const applyPace = (preset: FollowPreset, daily: number) => {
    const hourly = hourlyPreset(preset, daily);
    setPreferredPreset(preset.id);
    setForm(previous => ({
      ...previous,
      follow_pacing: "hourly",
      follows_per_day: daily,
      follows_per_hour_min: hourly,
      follows_per_hour_max: hourly,
    }));
    setErrors(previous => ({
      ...previous, follows_per_day: "", follows_per_hour_min: "", follows_per_hour_max: "",
    }));
  };
  const change = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setForm((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: "" }));
  };
  const numericField = (
    key:
      | "follows_per_day"
      | "follows_per_hour_min"
      | "follows_per_hour_max"
      | "unfollows_per_day"
      | "jobs_per_run"
      | "collection_batch_size"
      | "retention_days",
    label: string,
    hint: string,
    max = 500,
    min = 0,
  ) => (
    <Field key={key} label={label} htmlFor={key} hint={hint} error={errors[key]}>
      <Input
        id={key}
        type="number"
        min={min}
        max={max}
        value={form[key] ?? 10}
        aria-invalid={!!errors[key]}
        aria-describedby={`${key}-${errors[key] ? "error" : "hint"}`}
        onChange={(event) => change(key, Number(event.target.value))}
      />
    </Field>
  );
  const cancel = () => {
    setForm({ ...data.settings });
    setErrors({});
  };
  const save = async () => {
    const validation = validateSettings(form);
    setErrors(validation);
    if (Object.keys(validation).length) {
      document.getElementById(Object.keys(validation)[0])?.focus();
      return;
    }
    setSaving(true);
    try {
      await service.updateSettings(form);
      setForm({ ...await service.getSettings() });
      notify("Your preferences are saved.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Could not save. Try again.", "error");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="page settings-page">
      <PageHeader
        title="Settings"
        eyebrow="YOUR ARCHIVE, YOUR TERMS"
        description="Set a pace, keep your boundaries, and make this space your own."
      >
        {dirty && (
          <Button variant="ghost" disabled={saving} onClick={cancel}>
            Cancel
          </Button>
        )}
        <Button disabled={!dirty} loading={saving} form="settings-form" type="submit">
          <Save size={15} />
          Save changes
        </Button>
      </PageHeader>
      <AccountConnection key={data.account.id} accountId={data.account.id} username={data.account.username} restricted={data.account.restricted}
        connected={data.account.session_state === "connected" && !data.account.restricted} onStarted={id => navigate(`/settings?account=${id}`)} />
      <Card className="account-settings-card">
        <div className="settings-card-heading"><div><h2>Your Instagram accounts</h2><p>Switch accounts or add another account to your desk.</p></div><Button variant="outline" onClick={() => setAddAccount(true)}>Add Instagram account</Button></div>
        <div className="account-settings-body">
          <Field label="Workspace account" htmlFor="workspace-account"><Select id="workspace-account" label="Workspace account" value={data.account.id}
            options={(data.accounts ?? [data.account]).map(account => ({ value: account.id, label: `@${account.username} · ${account.mode}` }))}
            onValueChange={id => navigate(`/settings?account=${id}`)} /></Field>
          <p>Last verified: {data.connection?.last_validated_at ? new Date(data.connection.last_validated_at).toLocaleString([], { timeZone: data.settings.timezone }) : "Not verified"}</p>
          {data.account.mode === "live" && <>
            <Button variant="outline" disabled={data.account.session_state !== "connected" || data.connection?.transport_mode === "mock"} onClick={() => void perform(async () => {
              const { api } = await import("@/lib/api");
              await api(`/accounts/${data.account.id}/import-following`, { method: "POST", body: JSON.stringify({ idempotency_key: crypto.randomUUID() }) });
              service.setFixture("normal");
            }, "Bounded following import queued. Existing relationships remain unmanaged.")}>Import existing following</Button>
            <p>Outgoing requests are recorded during relationship checks. A partial collection never proves that a relationship is absent.</p>
          </>}
        </div>
      </Card>
      <Modal open={addAccount} onOpenChange={setAddAccount} title="Add an Instagram account" description="Connect your account, then confirm verification here when Instagram requests it.">
        <AccountConnection onStarted={id => { setAddAccount(false); navigate(`/settings?account=${id}`); }} />
      </Modal>
      <WorkflowControls />
      <form
        id="settings-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="settings-form"
      >
        <fieldset disabled={saving} className="settings-fields">
          <div className="settings-grid">
            <Card className="settings-card">
              <div className="settings-card-heading">
                <span className="settings-section-icon">
                  <Sprout size={18} />
                </span>
                <div>
                  <h2>Room to grow</h2>
                  <p>Owner-configured acquisition controls.</p>
                </div>
              </div>
              <div className="settings-card-body">
                <Field label="Follow pace preset" htmlFor="follow-pace-preset">
                  <Select
                    id="follow-pace-preset"
                    label="Follow pace preset"
                    value={activePreset?.id ?? (dailyPace === 0 ? "paused" : "custom")}
                    options={[
                      { value: "paused", label: "Pause follows" },
                      ...followPresets.map(preset => ({ value: preset.id, label: preset.label })),
                      { value: "custom", label: "Custom limits", disabled: true },
                    ]}
                    onValueChange={value => {
                      if (value === "paused") change("follows_per_day", 0);
                      const preset = followPresets.find(item => item.id === value);
                      if (preset) applyPace(preset, preset.daily[0]);
                    }}
                  />
                </Field>
                {activePreset && dailyPace !== null && (
                  <Field label="Pace within preset" htmlFor="preset-follow-pace">
                    <input
                      id="preset-follow-pace"
                      className="w-full cursor-pointer accent-current"
                      type="range"
                      min={activePreset.daily[0]}
                      max={activePreset.daily[1]}
                      value={dailyPace}
                      aria-valuetext={dailyPace + " follows per day, " + form.follows_per_hour_max + " per hour"}
                      onChange={event => applyPace(activePreset, Number(event.target.value))}
                    />
                    <output htmlFor="preset-follow-pace" className="tabular-nums">
                      {dailyPace} follows / day · {form.follows_per_hour_max} / hour
                    </output>
                  </Field>
                )}
                <Field label="Candidate selection" htmlFor="ranked-follow-mode" hint="Automatic selection ranks eligible people using saved audience evidence.">
                  <Select id="ranked-follow-mode" label="Candidate selection" value={form.ranked_follow_mode ?? "review"} onValueChange={v => change("ranked_follow_mode", v as Settings["ranked_follow_mode"])} options={[
                    {value:"review",label:"Manual review"},
                    {value:"eligible",label:"Automatic · all eligible candidates"},
                    {value:"likers",label:"Automatic · active likers"},
                    {value:"overlap",label:"Automatic · two shared profiles or more"},
                  ]} />
                </Field>
                <Field label="Follow pacing" htmlFor="follow-pacing" hint="Hourly pacing varies the number and timing of follows across each hour.">
                  <Select id="follow-pacing" label="Follow pacing" value={form.follow_pacing ?? "daily"} onValueChange={value => change("follow_pacing", value as Settings["follow_pacing"])} options={[{value:"daily",label:"Spread daily allowance"},{value:"hourly",label:"Hourly range with varied timing"}]} />
                </Field>
                {form.follow_pacing === "hourly" && <div className="form-grid">
                  {numericField("follows_per_hour_min", "Minimum follows / hour", "Hourly target when candidates are available.", 1000, 1)}
                  {numericField("follows_per_hour_max", "Maximum follows / hour", "Upper end of the hourly target range.", 1000, 1)}
                </div>}
                <div className="form-grid">
                  <Field label="Configured follows / day" htmlFor="follows_per_day" hint="Leave blank for no daily cap with hourly pacing. Zero stops follows." error={errors.follows_per_day}>
                    <Input id="follows_per_day" type="number" min={0} max={500} placeholder="No daily cap" value={form.follows_per_day ?? ""} onChange={event => change("follows_per_day", event.target.value === "" ? null : Number(event.target.value))} />
                  </Field>
                  {numericField(
                    "unfollows_per_day",
                    "Configured unfollows / day",
                    "A separate cleanup allowance.",
                  )}
                </div>
                <div className="form-grid">
                  <Field
                    label="HTTP requests / day"
                    htmlFor="request_ceiling"
                    hint="Leave blank for no daily cap. Zero stops Instagram requests."
                    error={errors.request_ceiling}
                  >
                    <Input
                      id="request_ceiling"
                      type="number"
                      min={0}
                      max={10000}
                      placeholder="No daily cap"
                      value={form.request_ceiling ?? ""}
                      aria-invalid={!!errors.request_ceiling}
                      aria-describedby={`request_ceiling-${errors.request_ceiling ? "error" : "hint"}`}
                      onChange={event => change("request_ceiling", event.target.value === "" ? null : Number(event.target.value))}
                    />
                  </Field>
                  {numericField(
                    "collection_batch_size",
                    "Collection batch size",
                    "The cap for each collection.",
                    100,
                    1,
                  )}
                </div>
                {numericField("jobs_per_run", "Jobs / reviewed batch", "Maximum follow jobs accepted in one reviewed selection.", 100, 1)}
                <p className="settings-inline-note">
                  <Info size={13} />
                  Instagram can restrict activity at any pace.
                </p>
              </div>
            </Card>
            <Card className="settings-card">
              <div className="settings-card-heading">
                <span className="settings-section-icon">
                  <TimerReset size={18} />
                </span>
                <div>
                  <h2>A considered cleanup</h2>
                  <p>Make space without losing the record.</p>
                </div>
              </div>
              <div className="settings-card-body">
                {numericField(
                  "retention_days",
                  "Retention period in days",
                  "Default: 15 days. Applies to managed relationships only.",
                  365,
                  1,
                )}
                <Field
                  label="Pending-request expiry policy"
                  htmlFor="pending-policy"
                  hint="Requests are never blindly retried or automatically cancelled."
                >
                  <Select
                    id="pending-policy"
                    label="Pending-request expiry policy"
                    value={form.pending_request_policy}
                    onValueChange={(value) =>
                      change("pending_request_policy", value as Settings["pending_request_policy"])
                    }
                    options={[
                      { value: "manual_review", label: "Manual review (default)" },
                      {
                        value: "review_after_retention",
                        label: "Queue a state check after retention",
                      },
                    ]}
                  />
                </Field>
                <p className="settings-inline-note">
                  <ShieldCheck size={13} />
                  Protected, excluded, and unmanaged accounts stay out of cleanup.
                </p>
              </div>
            </Card>
          </div>
          <Card className="settings-card schedule-card">
            <div className="settings-card-heading">
              <span className="settings-section-icon">
                <CalendarClock size={18} />
              </span>
              <div>
                <h2>A time for everything</h2>
                <p>Operating windows apply to Instagram execution in your selected timezone.</p>
              </div>
              <Badge>LOCAL DISPLAY · UTC STORAGE</Badge>
            </div>
            <div className="settings-card-body schedule-fields">
              <Field
                label="Operating window starts"
                htmlFor="window_start"
                error={errors.window_start}
              >
                <Input
                  id="window_start"
                  type="time"
                  value={form.window_start}
                  onChange={(event) => change("window_start", event.target.value)}
                />
              </Field>
              <Field label="Operating window ends" htmlFor="window_end" error={errors.window_end}>
                <Input
                  id="window_end"
                  type="time"
                  value={form.window_end}
                  onChange={(event) => change("window_end", event.target.value)}
                />
              </Field>
              <Field label="Workspace timezone" htmlFor="timezone" error={errors.timezone}>
                <Select
                  id="timezone"
                  label="Workspace timezone"
                  value={form.timezone}
                  onValueChange={(value) => change("timezone", value)}
                  options={[
                    { value: "America/Toronto", label: "America/Toronto" },
                    { value: "America/New_York", label: "America/New_York" },
                    { value: "America/Los_Angeles", label: "America/Los_Angeles" },
                    { value: "Europe/London", label: "Europe/London" },
                    { value: "Europe/Paris", label: "Europe/Paris" },
                    { value: "Asia/Tokyo", label: "Asia/Tokyo" },
                    { value: "Australia/Sydney", label: "Australia/Sydney" },
                    { value: "UTC", label: "UTC" },
                  ]}
                />
              </Field>
              <p className="settings-inline-note">
                <Globe2 size={14} />
                Windows and displayed times use your selected timezone. Job payloads always use UTC
                ISO timestamps. Identical start and end times mean all day.
              </p>
            </div>
          </Card>
        </fieldset>
      </form>
      <Card className="settings-card protection-settings">
        <div className="settings-card-heading">
          <span className="settings-section-icon">
            <ShieldCheck size={18} />
          </span>
          <div>
            <h2>Keep your boundaries</h2>
            <p>People to preserve. Accounts to pass by.</p>
          </div>
        </div>
        <div className="protection-settings-links">
          <button onClick={() => navigate("/ledger?tab=protected")}>
            <div>
              <strong>Protected accounts</strong>
              <span>Preserved from automatic follows and unfollows.</span>
            </div>
            <Badge tone="blue">{data.overview.protected_accounts} protected</Badge>
            <ArrowRight size={16} />
          </button>
          <button onClick={() => navigate("/ledger")}>
            <div>
              <strong>Excluded accounts</strong>
              <span>Skipped during both acquisition and cleanup.</span>
            </div>
            <Badge>{data.relationships.filter((record) => record.excluded).length} excluded</Badge>
            <ArrowRight size={16} />
          </button>
        </div>
      </Card>
      <Card className="settings-roadmap">
        <span className="settings-section-icon">
          <FileStack size={20} />
        </span>
        <div>
          <div>
            <h2>A home for your content</h2>
            <Badge>PHOTO / STORY DRAFTS</Badge>
          </div>
          <p>
            Create photo and story drafts, review saved previews, and manage publishing from approved Instagram DMs.
          </p>
          <Button variant="outline" onClick={() => navigate("/content")}>Open content drafts</Button>
        </div>
      </Card>
      {dirty && (
        <div className="settings-save-bar">
          <div>
            <span className="unsaved-dot" />
            <span>A few things have changed.</span>
          </div>
          <div>
            <Button variant="ghost" disabled={saving} onClick={cancel}>
              Discard changes
            </Button>
            <Button
              loading={saving}
              onClick={() => {
                void save();
              }}
            >
              <Save size={15} />
              Save preferences
            </Button>
          </div>
        </div>
      )}
      <RecoveryDialog open={recovery} onOpenChange={setRecovery} />
    </div>
  );
}
