import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Application, AuditEvent } from "../api/client";
import { StateGraph } from "../components/StateGraph";
import { AuditTrail } from "../components/AuditTrail";
import { Card, Pill, Spinner, ErrorNote, stateTone } from "../components/ui";
import { isParked } from "./resolve";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt className="text-slate-500">{k}</dt>
      <dd className="text-slate-800 font-medium text-right">{v}</dd>
    </>
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
  const inr = (n: any) => `₹${Number(n).toLocaleString("en-IN")}`;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <Link to="/pipeline" className="text-sm text-brand hover:underline">← Pipeline</Link>
          <h1 className="text-2xl font-semibold text-slate-900 mt-1">{app?.applicant.full_name ?? "Application"}</h1>
          <p className="text-xs text-slate-400 font-mono">{id}</p>
        </div>
        <Pill tone={stateTone(app?.workflow_state)}>{app?.workflow_state ?? "—"}</Pill>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {!app && !error && <Spinner label="Loading application…" />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="space-y-5">
          <Card title="Workflow state">
            <StateGraph current={app?.workflow_state ?? null} visited={visited} />
          </Card>

          {decision && (
            <Card title="Decision">
              <div className="flex items-center gap-2 mb-2">
                <Pill tone={stateTone(decision.disposition === "approve" ? "OFFER_GENERATED" : decision.disposition === "decline" ? "DECLINED" : "REFERRED")}>
                  {decision.disposition.toUpperCase()}
                </Pill>
                <span className="text-sm text-slate-500">band {decision.band} · score {decision.score}</span>
              </div>
              {decision.reason_codes?.length > 0 && (
                <div className="text-sm text-slate-500">reasons: {decision.reason_codes.join(", ")}</div>
              )}
              {decision.explanation && <p className="text-sm text-slate-600 italic mt-1">{decision.explanation}</p>}
              <div className="text-xs text-slate-400 mt-2">source: {decision.source}</div>
            </Card>
          )}

          {uw && (
            <Card title="Underwriting summary">
              <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
                <Row k="Bureau score" v={uw.bureau_score} />
                <Row k="Risk band" v={uw.band} />
                <Row k="DTI" v={uw.dti} />
                <Row k="Monthly income" v={inr(uw.monthly_income)} />
                <Row k="Obligations" v={inr(uw.monthly_obligations)} />
                <Row k="Tradelines" v={uw.tradelines_count} />
              </dl>
            </Card>
          )}

          {offer && (
            <Card title="Offer letter">
              <dl className="grid grid-cols-2 gap-y-1.5 text-sm">
                <Row k="Sanctioned amount" v={inr(offer.sanctioned_amount)} />
                <Row k="Interest rate" v={`${offer.interest_rate}% (${offer.rate_type})`} />
                <Row k="Tenure" v={`${offer.tenure_months} months`} />
                <Row k="EMI" v={inr(offer.emi)} />
                <Row k="Processing fee" v={`${inr(offer.processing_fee)} + GST ${inr(offer.gst_on_fee)}`} />
                <Row k="Net disbursal" v={inr(offer.net_disbursal_amount)} />
                <Row k="Total payable" v={inr(offer.total_amount_payable)} />
                <Row k="Valid until" v={String(offer.valid_until).slice(0, 10)} />
              </dl>
            </Card>
          )}

          {/* Ops Console actions (#15) — parked cases route to a dedicated review
              screen that shows the full collected picture before resolving. */}
          <Card title="Actions">
            {isParked(app?.workflow_state) ? (
              <div>
                <p className="text-sm text-amber-700 mb-3">
                  Parked for review. Open the full case to approve or decline with a justification.
                </p>
                <Link to={`/pipeline/${id}/review`} className="btn-primary">
                  Review &amp; resolve →
                </Link>
              </div>
            ) : (
              <p className="text-sm text-slate-400">No actions required.</p>
            )}
          </Card>
        </div>

        <Card title="Audit trail">
          <div className="max-h-[72vh] overflow-y-auto pr-1">
            <AuditTrail events={events} />
          </div>
        </Card>
      </div>
    </div>
  );
}
