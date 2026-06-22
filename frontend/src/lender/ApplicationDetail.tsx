import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Application, AuditEvent } from "../api/client";
import { StateGraph } from "../components/StateGraph";
import { AuditTrail } from "../components/AuditTrail";

const TERMINAL = new Set([
  "OFFER_GENERATED",
  "OFFER_ACCEPTED",
  "OFFER_EXPIRED",
  "DECLINED",
  "LEAD_DECLINED",
]);

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <h2 className="text-sm font-semibold text-slate-700 mb-3">{title}</h2>
      {children}
    </div>
  );
}

export function ApplicationDetail() {
  const { id } = useParams();
  const [app, setApp] = useState<Application | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    if (!id) return;
    try {
      const [a, ev] = await Promise.all([api.getApplication(id), api.getAudit(id)]);
      setApp(a);
      setEvents(ev);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [id]);

  const visited = new Set<string>();
  events.forEach((e) => {
    if (e.event_type === "state_transition") {
      visited.add(e.payload.from);
      visited.add(e.payload.to);
    }
  });

  const offer = app?.features?.offer_letter;
  const uw = app?.features?.underwriting_summary;
  const decision = app?.decision;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <Link to="/pipeline" className="text-sm text-brand hover:underline">
            ← Pipeline
          </Link>
          <h1 className="text-xl font-semibold text-slate-900 mt-1">
            {app?.applicant.full_name ?? "Application"}
          </h1>
          <p className="text-xs text-slate-400">{id}</p>
        </div>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-sm font-medium text-slate-700">
          {app?.workflow_state ?? "—"}
        </span>
      </div>

      {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-4">
          <Card title="Workflow state">
            <StateGraph current={app?.workflow_state ?? null} visited={visited} />
          </Card>

          {decision && (
            <Card title="Decision">
              <div className="text-sm space-y-1">
                <div>
                  <span className="font-semibold">{decision.disposition.toUpperCase()}</span> · band{" "}
                  {decision.band} · score {decision.score}
                </div>
                <div className="text-slate-500">reasons: {JSON.stringify(decision.reason_codes)}</div>
                {decision.explanation && <div className="italic text-slate-600">{decision.explanation}</div>}
                <div className="text-xs text-slate-400">source: {decision.source}</div>
              </div>
            </Card>
          )}

          {uw && (
            <Card title="Underwriting summary">
              <dl className="grid grid-cols-2 gap-1 text-sm">
                <dt className="text-slate-500">Bureau score</dt><dd>{uw.bureau_score}</dd>
                <dt className="text-slate-500">Band</dt><dd>{uw.band}</dd>
                <dt className="text-slate-500">DTI</dt><dd>{uw.dti}</dd>
                <dt className="text-slate-500">Income</dt><dd>₹{Number(uw.monthly_income).toLocaleString()}</dd>
                <dt className="text-slate-500">Obligations</dt><dd>₹{Number(uw.monthly_obligations).toLocaleString()}</dd>
                <dt className="text-slate-500">Tradelines</dt><dd>{uw.tradelines_count}</dd>
              </dl>
            </Card>
          )}

          {offer && (
            <Card title="Offer letter">
              <dl className="grid grid-cols-2 gap-1 text-sm">
                <dt className="text-slate-500">Amount</dt><dd>₹{Number(offer.sanctioned_amount).toLocaleString()}</dd>
                <dt className="text-slate-500">Rate</dt><dd>{offer.interest_rate}% ({offer.rate_type})</dd>
                <dt className="text-slate-500">Tenure</dt><dd>{offer.tenure_months} months</dd>
                <dt className="text-slate-500">EMI</dt><dd>₹{Number(offer.emi).toLocaleString()}</dd>
                <dt className="text-slate-500">Processing fee</dt><dd>₹{Number(offer.processing_fee).toLocaleString()} + GST ₹{Number(offer.gst_on_fee).toLocaleString()}</dd>
                <dt className="text-slate-500">Net disbursal</dt><dd>₹{Number(offer.net_disbursal_amount).toLocaleString()}</dd>
                <dt className="text-slate-500">Total payable</dt><dd>₹{Number(offer.total_amount_payable).toLocaleString()}</dd>
                <dt className="text-slate-500">Valid until</dt><dd>{String(offer.valid_until).slice(0, 10)}</dd>
              </dl>
            </Card>
          )}

          {/* Ops actions (#15) — resolve/override land here. Read-only for now. */}
          <Card title="Actions">
            {app && app.workflow_state?.endsWith("EXCEPTION") ? (
              <p className="text-sm text-amber-700">
                This case is parked for review. Resolve / override actions arrive with the Ops
                Console (#15).
              </p>
            ) : (
              <p className="text-sm text-slate-400">No actions required.</p>
            )}
          </Card>
        </div>

        <Card title="Audit trail">
          <div className="max-h-[70vh] overflow-y-auto pr-1">
            <AuditTrail events={events} />
          </div>
        </Card>
      </div>
    </div>
  );
}
