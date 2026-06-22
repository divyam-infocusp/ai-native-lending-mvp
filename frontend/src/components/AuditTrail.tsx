// The reconstructed audit trail (#6) as a readable vertical timeline — a marker
// + icon per event type, a title, and a human-formatted detail line.
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
    case "decision":
      return {
        icon: "⚖️",
        tone: "bg-violet-100 text-violet-700",
        title: `Decision · ${String(p.disposition).toUpperCase()}`,
        detail: (
          <div>
            <div>band {p.band} · score {p.score}{p.reason_codes?.length ? ` · ${p.reason_codes.join(", ")}` : ""}</div>
            {p.explanation && <div className="italic text-slate-500 mt-0.5">{p.explanation}</div>}
          </div>
        ),
      };
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

export function AuditTrail({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-slate-400">No events yet.</p>;
  return (
    <ol className="relative">
      {events.map((e, i) => {
        const r = render(e);
        return (
          <li key={e.event_id ?? e.seq} className="flex gap-3 pb-4 last:pb-0">
            <div className="flex flex-col items-center">
              <span className={`h-7 w-7 shrink-0 rounded-full grid place-items-center text-xs ${r.tone}`}>{r.icon}</span>
              {i < events.length - 1 && <span className="w-px flex-1 bg-slate-200 mt-1" />}
            </div>
            <div className="pt-0.5 min-w-0">
              <div className="text-sm font-medium text-slate-800">{r.title}</div>
              {r.detail && <div className="text-sm text-slate-500 break-words">{r.detail}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
