// The reconstructed audit trail (#6) as a readable vertical timeline. Each event
// is a collapsible row: an icon + heading + concise summary is always shown, and
// drilling in reveals *every* populated field captured for that event (so nothing
// recorded is hidden — the per-state summaries are just a scannable headline).
import { Fragment, useState } from "react";
import { AuditEvent } from "../api/client";

interface Rendered {
  icon: string;
  tone: string; // marker color classes
  title: string;
  detail?: React.ReactNode;
}

const inr = (n: any) => `₹${Number(n).toLocaleString("en-IN")}`;

function render(e: AuditEvent): Rendered {
  const p = e.payload || {};
  switch (e.event_type) {
    case "state_transition":
      return {
        icon: "→",
        tone: "bg-brand-100 text-brand-700",
        title: "State transition",
        detail: (
          <span>
            {p.from} <span className="text-slate-400">→</span> <span className="font-medium text-slate-700">{p.to}</span>
          </span>
        ),
      };
    case "consent":
      return {
        icon: "🔐",
        tone: "bg-slate-100 text-slate-600",
        title: "Consent",
        detail:
          p.layer === 2
            ? `Layer-2 artifact minted for '${p.purpose}' (honors Layer-1 '${p.layer1_purpose}')`
            : `Layer-1 ${p.action} for '${p.purpose}'`,
      };
    case "decision": {
      const s = p.sensitivity;
      return {
        icon: "⚖️",
        tone: "bg-violet-100 text-violet-700",
        title: `Decision · ${String(p.disposition).toUpperCase()}`,
        detail: (
          <div>
            <div>band {p.band} · score {p.score}{p.reason_codes?.length ? ` · ${p.reason_codes.join(", ")}` : ""}</div>
            {s && (
              <div className="text-slate-500 mt-0.5">
                {s.sensitive
                  ? `Income-sensitivity: band ${s.original_band} → ${s.stressed_band} under a ${s.haircut_pct}% income drop → referred`
                  : `Stress-tested at −${s.haircut_pct}% income: band ${s.original_band} held`}
              </div>
            )}
            {p.explanation && <div className="italic text-slate-500 mt-0.5">{p.explanation}</div>}
          </div>
        ),
      };
    }
    case "agent_reasoning":
      return renderAgent(p);
    default:
      return { icon: "•", tone: "bg-slate-100 text-slate-500", title: e.event_type };
  }
}

function renderAgent(p: Record<string, any>): Rendered {
  const agent = p.agent ?? "agent";
  if (agent === "lead-qualification")
    return {
      icon: "🧭",
      tone: "bg-sky-100 text-sky-700",
      title: "Lead qualification",
      detail: (
        <div>
          <div>status {p.status} · {p.reason_code} · confidence {p.confidence}</div>
          {p.reasoning && <div className="italic text-slate-500 mt-0.5">{p.reasoning}</div>}
        </div>
      ),
    };
  if (agent === "onboarding-copilot")
    return {
      icon: "💬",
      tone: "bg-blue-100 text-blue-700",
      title: `Onboarding copilot${p.complete ? " · complete" : ""}`,
      detail: p.assistant_message ? <div className="italic text-slate-500">"{p.assistant_message}"</div> : undefined,
    };
  if (agent === "document-intelligence") {
    const fc = p.field_confidence || {};
    const xc = (p.cross_checks || []) as { matches: boolean }[];
    return {
      icon: "📄",
      tone: "bg-teal-100 text-teal-700",
      title: `Document intelligence · ${p.status}`,
      detail: (
        <div className="space-y-0.5">
          {Object.keys(fc).length > 0 && (
            <div>
              fields: {Object.entries(fc).slice(0, 6).map(([k, v]: any) => `${k} ${(v.confidence * 100).toFixed(0)}%`).join(" · ")}
            </div>
          )}
          {xc.length > 0 && <div>cross-checks: {xc.filter((c) => c.matches).length}/{xc.length} matched</div>}
          {p.exception_reasons?.length ? <div className="text-amber-600">exceptions: {p.exception_reasons.join(", ")}</div> : null}
        </div>
      ),
    };
  }
  if (agent === "underwriting") {
    const s = p.summary || {};
    return {
      icon: "📊",
      tone: "bg-indigo-100 text-indigo-700",
      title: `Underwriting · ${p.status}`,
      detail:
        p.status === "completed" ? (
          <div>bureau {s.bureau_score} · band {s.band} · DTI {s.dti} · income {inr(s.monthly_income)} · {s.tradelines_count} tradelines</div>
        ) : (
          <div className="text-amber-600">reasons: {(p.reasons || []).join(", ")}</div>
        ),
    };
  }
  if (agent === "decision-qa") {
    if (p.action === "offer_delivered") {
      const o = p.offer_letter || {};
      return {
        icon: "✅",
        tone: "bg-emerald-100 text-emerald-700",
        title: "Offer delivered",
        detail: <div>{inr(o.sanctioned_amount)} @ {o.interest_rate}% · EMI {inr(o.emi)} · {o.tenure_months}m</div>,
      };
    }
    return {
      icon: "🔎",
      tone: "bg-slate-100 text-slate-600",
      title: "Decision QA",
      detail: <div>qa {String(p.qa_ok)} · {p.disposition}{p.issues?.length ? ` · issues: ${p.issues.join(", ")}` : ""}</div>,
    };
  }
  return { icon: "•", tone: "bg-slate-100 text-slate-500", title: agent };
}

// ---- Full-payload drill-down ----------------------------------------------

function isEmpty(v: any): boolean {
  if (v === null || v === undefined || v === "") return true;
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.keys(v).length === 0;
  return false;
}

const humanKey = (k: string) => k.replace(/_/g, " ");

// Internal/reproducibility stamps that aren't meaningful to an underwriter — the
// LLM model + prompt version are pinned for governance but the binding decision is
// deterministic, so we hide them from the trail (at any nesting level).
const HIDDEN_KEYS = new Set(["model_id", "prompt_version"]);

function FieldValue({ value }: { value: any }) {
  if (typeof value === "boolean") return <span>{value ? "yes" : "no"}</span>;
  if (typeof value === "number" || typeof value === "string") return <span>{String(value)}</span>;
  if (Array.isArray(value)) {
    const allScalar = value.every((v) => v === null || typeof v !== "object");
    if (allScalar) return <span>{value.map(String).join(", ")}</span>;
    return (
      <div className="space-y-1.5">
        {value.map((v, i) => (
          <div key={i} className="pl-2 border-l-2 border-slate-200">
            {typeof v === "object" && v !== null ? <FieldTree obj={v} /> : <span>{String(v)}</span>}
          </div>
        ))}
      </div>
    );
  }
  if (value && typeof value === "object") return <FieldTree obj={value} />;
  return <span className="text-slate-400">—</span>;
}

// Recursive key/value view of every populated field in a payload (or sub-object).
function FieldTree({ obj }: { obj: Record<string, any> }) {
  const entries = Object.entries(obj).filter(([k, v]) => !HIDDEN_KEYS.has(k) && !isEmpty(v));
  if (entries.length === 0) return <span className="text-slate-400">—</span>;
  return (
    <dl className="grid grid-cols-[max-content,1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([k, v]) => (
        <Fragment key={k}>
          <dt className="text-slate-400 capitalize whitespace-nowrap">{humanKey(k)}</dt>
          <dd className="text-slate-700 break-words min-w-0">
            <FieldValue value={v} />
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

// ---- One collapsible timeline row ------------------------------------------

function AuditItem({ e, last }: { e: AuditEvent; last: boolean }) {
  const [open, setOpen] = useState(false);
  const r = render(e);
  const p = e.payload || {};
  const hasFields = Object.entries(p).some(([, v]) => !isEmpty(v));
  const when = e.created_at ? new Date(e.created_at).toLocaleString() : null;

  return (
    <li className="flex gap-3 pb-4 last:pb-0">
      <div className="flex flex-col items-center">
        <span className={`h-7 w-7 shrink-0 rounded-full grid place-items-center text-xs ${r.tone}`}>{r.icon}</span>
        {!last && <span className="w-px flex-1 bg-slate-200 mt-1" />}
      </div>
      <div className="pt-0.5 min-w-0 flex-1">
        <button
          type="button"
          onClick={() => hasFields && setOpen((o) => !o)}
          className={`flex items-start gap-1.5 text-left w-full group ${hasFields ? "cursor-pointer" : "cursor-default"}`}
        >
          {hasFields ? (
            <span className={`text-slate-400 mt-0.5 text-[10px] transition-transform ${open ? "rotate-90" : ""}`}>▶</span>
          ) : (
            <span className="w-[10px]" />
          )}
          <span className="flex-1 min-w-0">
            <span className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-slate-800 group-hover:text-brand">{r.title}</span>
              {when && <span className="text-[10px] text-slate-400 whitespace-nowrap">{when}</span>}
            </span>
            {r.detail && <span className="block text-sm text-slate-500 break-words">{r.detail}</span>}
          </span>
        </button>

        {open && hasFields && (
          <div className="mt-2 ml-4 rounded-lg bg-slate-50 border border-slate-100 p-3">
            <FieldTree obj={p} />
            {e.actor && (
              <div className="mt-2 pt-2 border-t border-slate-100 text-[10px] text-slate-400">
                actor: {e.actor}
              </div>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export function AuditTrail({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-slate-400">No events yet.</p>;
  return (
    <ol className="relative">
      {events.map((e, i) => (
        <AuditItem key={e.event_id ?? e.seq} e={e} last={i === events.length - 1} />
      ))}
    </ol>
  );
}
