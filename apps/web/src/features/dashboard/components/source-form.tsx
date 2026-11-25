import { useEffect, useState } from "react";
import { AtSign, Info, Link2, Plus } from "lucide-react";
import { useArchive } from "../lib/archive-context";
import { validateSource } from "../lib/utils";
import type { CreateSourceInput } from "../lib/types";
import { Button, Field, Input, Modal, Select, Switch, Tabs } from "./ui";

export function SourceForm({
  open,
  onOpenChange,
  initialType = "profile",
  initialValue = "",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialType?: CreateSourceInput["type"];
  initialValue?: string;
}) {
  const { service, data, notify } = useArchive();
  const defaultForm = (): CreateSourceInput => ({
    type: initialType,
    label: "",
    value: initialValue,
    enabled: true,
    collection_size: initialType === "mutual" ? Math.min(3, data.settings.collection_batch_size) : data.settings.collection_batch_size,
    ordering: "default",
  });
  const [form, setForm] = useState<CreateSourceInput>(defaultForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) {
      setForm(defaultForm());
      setErrors({});
    }
  }, [open, initialType, initialValue]);
  const change = <K extends keyof CreateSourceInput>(key: K, value: CreateSourceInput[K]) => {
    setForm((previous) => ({ ...previous, [key]: value }));
    setErrors((previous) => ({ ...previous, [key]: "", form: "" }));
  };
  return (
    <Modal
      open={open}
      onOpenChange={(value) => {
        if (!saving) onOpenChange(value);
      }}
      title="A new starting point"
      description="Add a profile or post to discover people for your archive."
    >
      <form
        className="source-form"
        onSubmit={async (event) => {
          event.preventDefault();
          const nextErrors = validateSource(form);
          setErrors(nextErrors);
          if (Object.keys(nextErrors).length) return;
          setSaving(true);
          try {
            await service.createSource(form);
            notify("Source added to your workspace. Ready to collect when you are.");
            onOpenChange(false);
          } catch (error) {
            setErrors({
              form:
                error instanceof Error ? error.message : "Could not add this source. Try again.",
            });
          } finally {
            setSaving(false);
          }
        }}
      >
        <Tabs
          value={form.type}
          onValueChange={(value) => {
            change("type", value as CreateSourceInput["type"]);
            change("value", "");
            change("ordering", "default");
            change("collection_size", value === "mutual" ? Math.min(3, data.settings.collection_batch_size) : data.settings.collection_batch_size);
          }}
          items={[
            { value: "profile", label: "Profile" },
            ...(data.account.mode === "live" ? [{ value: "mutual", label: "Mutual following" }] : []),
            { value: "post", label: "Post or Reel" },
          ]}
        />
        <Field
          label={form.type !== "post" ? "Instagram handle" : "Post or Reel URL"}
          htmlFor="source-value"
          error={errors.value}
        >
          <div className="input-with-icon">
            {form.type !== "post" ? <AtSign size={16} /> : <Link2 size={16} />}
            <Input
              id="source-value"
              value={form.value}
              placeholder={
                form.type !== "post" ? "form.and.field" : "https://www.instagram.com/p/…"
              }
              aria-invalid={!!errors.value}
              aria-describedby={errors.value ? "source-value-error" : undefined}
              onChange={(e) => change("value", e.target.value)}
              autoComplete="off"
            />
          </div>
        </Field>
        <Field
          label="Friendly label"
          htmlFor="source-label"
          error={errors.label}
          hint="A little context for your future self."
        >
          <Input
            id="source-label"
            value={form.label}
            placeholder="e.g. The everyday edit"
            aria-invalid={!!errors.label}
            aria-describedby={`source-label-${errors.label ? "error" : "hint"}`}
            maxLength={80}
            onChange={(e) => change("label", e.target.value)}
          />
        </Field>
        <div className="form-grid">
          <Field label="Collection size" htmlFor="source-size" error={errors.collection_size}>
            <Input
              id="source-size"
              type="number"
              min={1}
              max={form.type === "mutual" ? 3 : 100}
              value={form.collection_size}
              onChange={(e) => change("collection_size", Number(e.target.value))}
            />
          </Field>
          {form.type === "profile" && (
            <Field label="Requested ordering" htmlFor="source-order">
              <Select
                id="source-order"
                label="Requested ordering"
                value={form.ordering}
                onValueChange={(value) =>
                  change("ordering", value as CreateSourceInput["ordering"])
                }
                options={[
                  { value: "default", label: "Default" },
                  { value: "newest", label: "Newest requested" },
                  { value: "oldest", label: "Oldest requested" },
                ]}
              />
            </Field>
          )}
        </div>
        <div className="form-info">
          <Info size={16} />
          <p>
            {form.type === "mutual" ? "Samples who the source follows and keeps only accounts also observed following the source." : "Instagram may return a partial list. Collection uses your configured batch and request allowances."}
          </p>
        </div>
        <div className="switch-row">
          <div>
            <label htmlFor="source-enabled">Enable source</label>
            <p>Make this source available for bounded collection.</p>
          </div>
          <Switch
            id="source-enabled"
            label="Enable source"
            checked={form.enabled}
            onCheckedChange={(value) => change("enabled", value)}
          />
        </div>
        {errors.form && (
          <p className="field-error" role="alert">
            {errors.form}
          </p>
        )}
        <div className="dialog-footer">
          <Button
            type="button"
            variant="outline"
            disabled={saving}
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" loading={saving}>
            <Plus size={16} />
            Add source
          </Button>
        </div>
      </form>
    </Modal>
  );
}
