// Renders the reconstructed audit trail (#6) with per-event-type formatting —
// the lender's "what happened and why" panel.
import { AuditEvent } from "../api/client";

function Line({ seq, label, children }: { seq: number; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-1.5 border-b border-slate-100 last:border-0">
      <span className="text-slate-300 text-xs w-8 shrink-0 text-right tabular-nums">{seq}</span>
      <div className="text-sm">
        <span className="font-medium text-slate-700">{label}</span>
        <div className="text-slate-500">{children}</div>
      </div>
    </div>
  );
}

function renderEvent(e: AuditEvent) {
  const p = e.payload || {};
  if (e.event_type === "state_transition") {
    return (
      <Line key={e.seq} seq={e.seq} label="STATE">
        {p.from} → <span className="font-medium text-slate-700">{p.to}</span>
      </Line>
    );
  }
  if (e.event_type === "consent") {
    return (
      <Line key={e.seq} seq={e.seq} label="consent">
        {p.layer === 2
          ? `Layer-2 artifact minted for '${p.purpose}' (honors Layer-1 '${p.layer1_purpose}')`
          : `Layer-1 ${p.action} for '${p.purpose}'`}
      </Line>
    );
  }
  if (e.event_type === "decision") {
    return (
      <Line key={e.seq} seq={e.seq} label="DECISION">
        <span className="font-semibold">{String(p.disposition).toUpperCase()}</span> · band {p.band} ·
        score {p.score} · reasons {JSON.stringify(p.reason_codes)}
        {p.explanation ? <div className="mt-1 italic">{p.explanation}</div> : null}
      </Line>
    );
  }
  if (e.event_type === "agent_reasoning") {
    return renderAgent(e);
  }
  return (
    <Line key={e.seq} seq={e.seq} label={e.event_type}>
      {JSON.stringify(p)}
    </Line>
  );
}

function renderAgent(e: AuditEvent) {
  const p = e.payload || {};
  const agent = p.agent ?? "agent";
  if (agent === "lead-qualification") {
    return (
      <Line key={e.seq} seq={e.seq} label="lead-qual">
        status {p.status} · {p.reason_code} · confidence {p.confidence}
        {p.reasoning ? <div className="mt-1 italic">{p.reasoning}</div> : null}
      </Line>
    );
  }
  if (agent === "document-intelligence") {
    const fc = p.field_confidence || {};
    const xc = (p.cross_checks || []) as { matches: boolean }[];
    return (
      <Line key={e.seq} seq={e.seq} label="doc-intel">
        status {p.status}
        {Object.keys(fc).length > 0 && (
          <div>
            field confidence:{" "}
            {Object.entries(fc)
              .slice(0, 6)
              .map(([k, v]: any) => `${k}=${v.confidence?.toFixed?.(2) ?? v.confidence}`)
              .join(", ")}
          </div>
        )}
        {xc.length > 0 && (
          <div>
            cross-checks: {xc.filter((c) => c.matches).length}/{xc.length} matched
          </div>
        )}
        {p.exception_reasons?.length ? <div>exceptions: {JSON.stringify(p.exception_reasons)}</div> : null}
      </Line>
    );
  }
  if (agent === "underwriting") {
    const s = p.summary || {};
    return (
      <Line key={e.seq} seq={e.seq} label="underwriting">
        status {p.status}
        {p.status === "completed" ? (
          <div>
            bureau_score {s.bureau_score} · band {s.band} · DTI {s.dti} · income {s.monthly_income} ·
            obligations {s.monthly_obligations} · tradelines {s.tradelines_count}
          </div>
        ) : (
          <div>reasons: {JSON.stringify(p.reasons)}</div>
        )}
      </Line>
    );
  }
  if (agent === "decision-qa") {
    if (p.action === "offer_delivered") {
      const o = p.offer_letter || {};
      return (
        <Line key={e.seq} seq={e.seq} label="decision-qa">
          OFFER DELIVERED · ₹{o.sanctioned_amount?.toLocaleString?.()} @ {o.interest_rate}% ·
          EMI ₹{o.emi?.toLocaleString?.()} · {o.tenure_months}m
        </Line>
      );
    }
    return (
      <Line key={e.seq} seq={e.seq} label="decision-qa">
        qa_ok {String(p.qa_ok)} · {p.disposition}
        {p.issues?.length ? <div>issues: {JSON.stringify(p.issues)}</div> : null}
      </Line>
    );
  }
  return (
    <Line key={e.seq} seq={e.seq} label={agent}>
      {JSON.stringify(p)}
    </Line>
  );
}

export function AuditTrail({ events }: { events: AuditEvent[] }) {
  if (events.length === 0) return <p className="text-sm text-slate-400">No events yet.</p>;
  return <div>{events.map(renderEvent)}</div>;
}
