// Small, cohesive UI primitives shared across both surfaces (#39).
import { ReactNode } from "react";

export function Brand({ className = "" }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-brand-400 to-brand-700 grid place-items-center text-white font-bold shadow-glow">
        L
      </div>
      <span className="font-semibold tracking-tight text-slate-900">LendAI</span>
    </div>
  );
}

export function Card({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`card p-5 animate-fade-in ${className}`}>
      {title && <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">{title}</h2>}
      {children}
    </div>
  );
}

export function Stat({ label, value, accent = "text-slate-900" }: { label: string; value: ReactNode; accent?: string }) {
  return (
    <div className="card px-5 py-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`text-2xl font-semibold mt-1 ${accent}`}>{value}</div>
    </div>
  );
}

const PILL_TONES: Record<string, string> = {
  green: "bg-emerald-100 text-emerald-700",
  red: "bg-rose-100 text-rose-700",
  amber: "bg-amber-100 text-amber-700",
  slate: "bg-slate-100 text-slate-600",
  brand: "bg-brand-100 text-brand-700",
};

export function Pill({ tone = "slate", children }: { tone?: keyof typeof PILL_TONES | string; children: ReactNode }) {
  return <span className={`pill ${PILL_TONES[tone] ?? PILL_TONES.slate}`}>{children}</span>;
}

// Map a workflow state to a pill tone.
export function stateTone(state: string | null | undefined): string {
  if (!state) return "slate";
  if (["OFFER_GENERATED", "OFFER_ACCEPTED", "KYC_VERIFIED"].includes(state)) return "green";
  if (["DECLINED", "LEAD_DECLINED", "OFFER_EXPIRED"].includes(state)) return "red";
  if (state.endsWith("EXCEPTION") || state === "REFERRED") return "amber";
  return "brand";
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-slate-400 text-sm">
      <span className="h-4 w-4 rounded-full border-2 border-slate-300 border-t-brand animate-spin" />
      {label}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <div className="rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-sm px-3 py-2">{children}</div>;
}
