import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Application } from "../api/client";
import { Card, Pill, Spinner, ErrorNote, stateTone } from "../components/ui";
import { RESOLUTIONS, ResolveAction, isParked } from "./resolve";

const inr = (n: any) => (n === undefined || n === null || n === "" ? "—" : `₹${Number(n).toLocaleString("en-IN")}`);
const txt = (v: any) => (v === undefined || v === null || v === "" ? "—" : String(v));

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-slate-50 last:border-0">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-800 font-medium text-right break-words">{value}</dd>
    </div>
  );
}

function docTone(verified: boolean | null | undefined): { tone: string; text: string } {
  if (verified === true) return { tone: "green", text: "Verified" };
  if (verified === false) return { tone: "red", text: "Failed" };
  return { tone: "amber", text: "Pending" };
}

// The reviewer must type a justification (§16.10): the binding reason stays the
// structured code; this note is recorded in the audit trail.
function ResolvePanel({ app }: { app: Application }) {
  const navigate = useNavigate();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = RESOLUTIONS[app.workflow_state ?? ""] ?? [];
  const ready = note.trim().length > 0;

  async function run(a: ResolveAction) {
    if (!ready) return;
    setBusy(a.to_state);
    setError(null);
    try {
      await api.resolve(app.application_id, a.to_state, a.reason_code, note.trim());
      navigate(`/pipeline/${app.application_id}`);
    } catch (e: any) {
      setError(e.message);
      setBusy(null);
    }
  }

  return (
    <Card title="Resolve this case">
      <p className="text-sm text-amber-700 mb-3">
        Parked for review. Record a justification, then approve or decline — the action
        is audited against your identity.
      </p>
      <label className="block text-xs font-medium text-slate-500 mb-1">
        Justification <span className="text-rose-500">*</span>
      </label>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={3}
        placeholder="e.g. Stable 4-year salary history; the DTI breach is a one-off and well within affordability on a conservative view."
        className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:border-brand focus:ring-1 focus:ring-brand outline-none resize-none"
      />
      <div className="flex flex-wrap gap-2 mt-3">
        {actions.map((a) => (
          <button
            key={a.to_state}
            onClick={() => run(a)}
            disabled={!ready || busy !== null}
            title={!ready ? "Add a justification first" : undefined}
            className={`${a.danger ? "btn bg-rose-600 text-white hover:bg-rose-700" : "btn-primary"} disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {busy === a.to_state ? "Resolving…" : a.label}
          </button>
        ))}
      </div>
      {!ready && <p className="text-xs text-slate-400 mt-2">A justification is required to resolve.</p>}
      {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
    </Card>
  );
}

export function ApplicationReview() {
  const { id } = useParams();
  const [app, setApp] = useState<Application | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getApplication(id).then(setApp).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <div className="max-w-2xl mx-auto"><ErrorNote>{error}</ErrorNote></div>;
  if (!app) return <div className="card p-8 grid place-items-center"><Spinner label="Loading case…" /></div>;

  const a = app.applicant ?? ({} as any);
  const f = app.features ?? {};
  const val = (k: string) => (a as any)[k] ?? f[k];     // field may live on applicant or features
  const docs: Record<string, any> = f.documents ?? {};
  const uw = f.underwriting_summary;
  const offer = f.offer_letter;
  const decision = app.decision;
  const fc = app.kyc?.field_confidence ?? [];
  const auths = app.consent?.authorizations ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between">
        <div>
          <Link to={`/pipeline/${id}`} className="text-sm text-brand hover:underline">← Case detail</Link>
          <h1 className="text-2xl font-semibold text-slate-900 mt-1">Review: {a.full_name ?? "Application"}</h1>
          <p className="text-xs text-slate-400 font-mono">{id}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Pill tone={stateTone(app.workflow_state)}>{app.workflow_state ?? "—"}</Pill>
          <Link to="/pipeline/policy" className="text-xs text-brand hover:underline">📋 Lending policy</Link>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Left: who they are + what they asked for */}
        <div className="space-y-5">
          <Card title="Applicant">
            <dl>
              <Field label="Full name" value={txt(a.full_name)} />
              <Field label="PAN" value={txt(a.pan)} />
              <Field label="Aadhaar" value={txt(a.aadhaar)} />
              <Field label="Date of birth" value={txt(a.date_of_birth)} />
              <Field label="Mobile" value={txt(a.mobile)} />
              <Field label="Email" value={txt(a.email)} />
              <Field label="Address" value={txt(a.current_address)} />
            </dl>
          </Card>

          <Card title="Employment & loan request">
            <dl>
              <Field label="Employment type" value={txt(val("employment_type"))} />
              <Field label="Employer" value={txt(val("employer_name"))} />
              <Field label="Tenure (months)" value={txt(val("employment_tenure_months"))} />
              <Field label="Monthly income" value={inr(val("monthly_income"))} />
              <Field label="Loan requested" value={inr(val("loan_amount_requested"))} />
              <Field label="Loan tenure (months)" value={txt(val("loan_tenure_months"))} />
              <Field label="Purpose" value={txt(val("loan_purpose"))} />
            </dl>
          </Card>

          <Card title="Documents">
            {Object.keys(docs).length === 0 ? (
              <p className="text-sm text-slate-400">No documents on file.</p>
            ) : (
              <ul className="space-y-2">
                {Object.entries(docs).map(([name, rec]) => {
                  const t = docTone(rec?.verified);
                  return (
                    <li key={name} className="flex items-center justify-between text-sm">
                      <span className="text-slate-700">{name.replace(/_/g, " ")}</span>
                      <Pill tone={t.tone}>{rec?.uploaded ? t.text : "Not uploaded"}</Pill>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>

          <Card title="Consent">
            {auths.length === 0 ? (
              <p className="text-sm text-slate-400">No authorizations captured.</p>
            ) : (
              <ul className="space-y-1.5">
                {auths.map((c, i) => (
                  <li key={i} className="flex items-center justify-between text-sm">
                    <span className="text-slate-700">{c.purpose}</span>
                    <Pill tone={c.status === "active" ? "green" : "slate"}>{c.status}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        {/* Right: what the checks found */}
        <div className="space-y-5">
          <Card title="Identity & document checks (KYC)">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-sm text-slate-500">Status</span>
              <Pill tone={app.kyc?.status === "verified" ? "green" : "amber"}>{txt(app.kyc?.status)}</Pill>
            </div>
            {fc.length === 0 ? (
              <p className="text-sm text-slate-400">No field-level confidence recorded.</p>
            ) : (
              <ul className="space-y-1.5">
                {fc.map((c) => (
                  <li key={c.field_name} className="flex items-center justify-between gap-2 text-sm">
                    <span className="text-slate-700">{c.field_name.replace(/_/g, " ")}</span>
                    <span className="flex items-center gap-2">
                      {c.risk_flags?.length > 0 && (
                        <span className="text-[11px] text-rose-600">{c.risk_flags.join(", ")}</span>
                      )}
                      <span className={`font-medium ${c.confidence >= 0.7 ? "text-emerald-600" : "text-amber-600"}`}>
                        {Math.round(c.confidence * 100)}%
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {app.kyc?.risk_flags?.length > 0 && (
              <div className="mt-3 text-xs text-rose-600">Flags: {app.kyc.risk_flags.join(", ")}</div>
            )}
          </Card>

          {uw && (
            <Card title="Underwriting summary">
              <dl>
                <Field label="Bureau score" value={txt(uw.bureau_score)} />
                <Field label="Risk band" value={txt(uw.band)} />
                <Field label="DTI" value={txt(uw.dti)} />
                <Field label="Monthly income" value={inr(uw.monthly_income)} />
                <Field label="Obligations" value={inr(uw.monthly_obligations)} />
                <Field label="Tradelines" value={txt(uw.tradelines_count)} />
              </dl>
            </Card>
          )}

          {decision && (
            <Card title="Engine decision">
              <div className="flex items-center gap-2 mb-2">
                <Pill tone={stateTone(decision.disposition === "approve" ? "OFFER_GENERATED" : decision.disposition === "decline" ? "DECLINED" : "REFERRED")}>
                  {decision.disposition.toUpperCase()}
                </Pill>
                <span className="text-sm text-slate-500">band {txt(decision.band)} · score {txt(decision.score)}</span>
              </div>
              {decision.reason_codes?.length > 0 && (
                <div className="text-sm text-slate-500">reasons: {decision.reason_codes.join(", ")}</div>
              )}
              {decision.explanation && <p className="text-sm text-slate-600 italic mt-1">{decision.explanation}</p>}
              <div className="text-xs text-slate-400 mt-2">source: {txt(decision.source)}</div>
            </Card>
          )}

          {offer && (
            <Card title="Indicative offer">
              <dl>
                <Field label="Sanctioned" value={inr(offer.sanctioned_amount)} />
                <Field label="Rate" value={`${offer.interest_rate}% (${offer.rate_type})`} />
                <Field label="EMI" value={inr(offer.emi)} />
              </dl>
            </Card>
          )}
        </div>
      </div>

      {/* Resolve — only when the case is actually parked for a human. */}
      {isParked(app.workflow_state) ? (
        <ResolvePanel app={app} />
      ) : (
        <Card title="Resolve this case">
          <p className="text-sm text-slate-400">This case isn't awaiting a decision — no action required.</p>
        </Card>
      )}
    </div>
  );
}
