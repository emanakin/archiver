import { ArrowUpRight, type LucideIcon } from "lucide-react";

interface MetricCardProps {
  label: string;
  value: number;
  icon: LucideIcon;
  note: string;
  tone: "mint" | "neutral" | "amber" | "blue";
  onClick: () => void;
}

export function MetricCard({ label, value, icon: Icon, note, tone, onClick }: MetricCardProps) {
  return (
    <button
      className={`card metric-card metric-${tone}`}
      onClick={onClick}
      aria-label={`${label}: ${value}. ${note}.`}
    >
      <div className="metric-top">
        <span>{label}</span>
        <Icon size={17} strokeWidth={1.65} />
      </div>
      <div className="metric-value">
        {String(value).padStart(2, "0")}
        <ArrowUpRight size={17} />
      </div>
      <p>{note}</p>
    </button>
  );
}
