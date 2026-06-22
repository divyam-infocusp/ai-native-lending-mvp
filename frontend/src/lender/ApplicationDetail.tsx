import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Application, AuditEvent } from "../api/client";
import { StateGraph } from "../components/StateGraph";
import { AuditTrail } from "../components/AuditTrail";
import { Card, Pill, Spinner, ErrorNote, stateTone } from "../components/ui";

// Reason-coded resolve actions per parked state (match RESOLVE_REASON_CODES + the
// §4 legal transitions on the backend, #15).
interface ResolveAction {
  to_state: string;
  reason_code: string;
  label: string;
  danger?: boolean;
}
const RESOLUTIONS: Record<string, ResolveAction[]> = {
  LEAD_EXCEPTION: [
    { to_state: "LEAD_QUALIFIED", reason_code: "ELIGIBLE_ON_REVIEW", label: "Qualify lead" },
    { to_state: "LEAD_DECLINED", reason_code: "NOT_GENUINE", label: "Reject — not genuine", danger: true },
  ],
  KYC_EXCEPTION: [
    { to_state: "KYC_VERIFIED", reason_code: "DOC_REVERIFIED", label: "Mark documents verified" },
  ],
  UW_EXCEPTION: [
    { to_state: "DECISION_READY", reason_code: "DATA_SUPPLEMENTED", label: "Proceed to decision" },
  ],
  REFERRED: [
    { to_state: "APPROVED", reason_code: "MANUAL_APPROVE", label: "Approve" },
    { to_state: "DECLINED", reason_code: "MANUAL_DECLINE", label: "Decline", danger: true },
  ],
};

function ResolvePanel({ app, onResolved }: { app: Application; onResolved: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = RESOLUTIONS[app.workflow_state ?? ""] ?? [];

  if (actions.length === 0) {
    return <p className="text-sm text-slate-400">No actions required.</p>;
  }

  async function run(a: ResolveAction) {
    setBusy(a.to_state);
    setError(null);
    try {
      await api.resolve(app.application_id, a.to_state, a.reason_code);
      onResolved();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <p className="text-sm text-amber-700 mb-3">
        Parked for review. Resolve with a bounded reason — recorded against your identity.
      </p>
      <div className="flex flex-wrap gap-2">
        {actions.map((a) => (
          <button
            key={a.to_state}
            onClick={() => run(a)}
            disabled={busy !== null}
            className={a.danger
              ? "btn bg-rose-600 text-white hover:bg-rose-700"
              : "btn-primary"}
          >
            {busy === a.to_state ? "Resolving…" : a.label}
          </button>
        ))}
      </div>
      {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
    </div>
  );
}

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

          {/* Ops Console actions (#15) — reason-coded resolve for parked cases. */}
          <Card title="Actions">
            {app ? <ResolvePanel app={app} onResolved={refresh} /> : null}
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
