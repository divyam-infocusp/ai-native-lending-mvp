import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Application } from "../api/client";
import { Card, Pill, Spinner, ErrorNote, stateTone } from "../components/ui";
import { RESOLUTIONS, ResolveAction, isParked } from "./resolve";

const inr = (n: any) => (n === undefined || n === null || n === "" ? "—" : `₹${Number(n).toLocaleString("en-IN")}`);

// --- Document viewer modal ---------------------------------------------------

function DocViewerModal({
  appId,
  docType,
  onClose,
}: {
  appId: string;
  docType: string;
  onClose: () => void;
}) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [contentType, setContentType] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const urlRef = useRef<string | null>(null);

  useEffect(() => {
    api.getDocumentFile(appId, docType)
      .then(({ blob, contentType: ct }) => {
        const url = URL.createObjectURL(blob);
        urlRef.current = url;
        setObjectUrl(url);
        setContentType(ct);
      })
      .catch((e) => setError(e.message));
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, [appId, docType]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        style={{ width: "min(720px, 95vw)", maxHeight: "90vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-100">
          <span className="font-semibold text-slate-800 capitalize">{docType.replace(/_/g, " ")}</span>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-auto min-h-0">
          {error && <div className="p-6"><ErrorNote>{error}</ErrorNote></div>}
          {!objectUrl && !error && (
            <div className="p-8 grid place-items-center"><Spinner label="Loading document…" /></div>
          )}
          {objectUrl && contentType.startsWith("image/") && (
            <img src={objectUrl} alt={docType} className="max-w-full h-auto block mx-auto p-4" />
          )}
          {objectUrl && contentType === "application/pdf" && (
            <iframe src={objectUrl} title={docType} className="w-full h-full" style={{ minHeight: "60vh" }} />
          )}
          {objectUrl && !contentType.startsWith("image/") && contentType !== "application/pdf" && (
            <div className="p-6 text-center">
              <p className="text-slate-500 mb-4 text-sm">Preview not available for {contentType}.</p>
              <a href={objectUrl} download={docType} className="btn-primary">Download</a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
const txt = (v: any) => (v === undefined || v === null || v === "" ? "—" : String(v));

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-slate-50 last:border-0">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-800 font-medium text-right break-words">{value}</dd>
    </div>
  );
}

// Mirrors doc_compare.py — same normalisation strategy per semantic type.
// Keeps UI mismatch detection consistent with the backend cross-check logic.
type CompareType = "id" | "date" | "name" | "text";

const normId   = (v: string) => v.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
const normText = (v: string) => v.replace(/[^A-Za-z0-9 ]/g, " ").replace(/\s+/g, " ").trim().toUpperCase();
const normName = (v: string) => normText(v).split(" ").filter(Boolean).sort().join(" ");
function normDate(v: string) {
  const d = (v ?? "").match(/\d+/g) ?? [];
  if (d.length === 3) {
    const [a, b, c] = d;
    if (a.length === 4) return `${a}${b.padStart(2,"0")}${c.padStart(2,"0")}`;  // YYYY-MM-DD
    if (c.length === 4) return `${c}${b.padStart(2,"0")}${a.padStart(2,"0")}`;  // DD/MM/YYYY
  }
  return d.join("");
}
function isSameValue(a: string, b: string, type: CompareType): boolean {
  if (!a || !b) return false;
  switch (type) {
    case "id":   return normId(a)   === normId(b);
    case "date": return normDate(a) === normDate(b);
    case "name": return normName(a) === normName(b);
    default:     return normText(a) === normText(b);
  }
}

// A field where the applicant's entered value is shown alongside what the documents
// extracted. Only shows red when the values genuinely differ after type-aware
// normalisation (mirrors doc_compare.py so "14/08/1990" ≡ "1990-08-14", spaces in
// Aadhaar are ignored, case/punctuation differences in names don't false-flag).
function ClaimField({
  label, claimed, documented, fmt, compareType = "text",
}: {
  label: string; claimed: any; documented: any;
  fmt?: (v: any) => string; compareType?: CompareType;
}) {
  const format = fmt ?? txt;
  const claim = format(claimed);
  const doc = documented === undefined || documented === null || documented === "" ? null : format(documented);
  const mismatch = doc !== null && claim !== "—" &&
    !isSameValue(String(claimed ?? ""), String(documented ?? ""), compareType);
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-slate-50 last:border-0">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-right break-words">
        <span className="text-slate-800 font-medium">{claim}</span>
        {mismatch && (
          <span className="block text-[11px] text-rose-600 mt-0.5">
            ⚠ docs: {doc} <span className="text-rose-400">(mismatch)</span>
          </span>
        )}
      </dd>
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
  const [viewingDoc, setViewingDoc] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    api.getApplication(id).then(setApp).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <div className="max-w-2xl mx-auto"><ErrorNote>{error}</ErrorNote></div>;
  if (!app) return <div className="card p-8 grid place-items-center"><Spinner label="Loading case…" /></div>;

  const appId = app.application_id;

  const a = app.applicant ?? ({} as any);
  const f = app.features ?? {};
  const val = (k: string) => (a as any)[k] ?? f[k];     // field may live on applicant or features
  const docs: Record<string, any> = f.documents ?? {};
  const di: Record<string, any> = f.documented_identity ?? {};   // identity: what docs read vs claim
  const st: Record<string, any> = f.applicant_stated ?? {};      // financials: what applicant stated vs docs
  const uw = f.underwriting_summary;
  const offer = f.offer_letter;
  const decision = app.decision;
  const fc = app.kyc?.field_confidence ?? [];
  const auths = app.consent?.authorizations ?? [];

  return (
    <div className="space-y-5">
      {viewingDoc && (
        <DocViewerModal appId={appId} docType={viewingDoc} onClose={() => setViewingDoc(null)} />
      )}
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
            <p className="text-xs text-slate-400 mb-2">
              Values as entered by the applicant. Where the uploaded documents read something
              different, the document value is shown below in red — these route the case to KYC review.
            </p>
            <dl>
              <ClaimField label="Full name"     claimed={a.full_name}       documented={di.name}         compareType="name" />
              <ClaimField label="PAN"           claimed={a.pan}             documented={di.pan}          compareType="id" />
              <ClaimField label="Aadhaar"       claimed={a.aadhaar}         documented={di.aadhaar}      compareType="id" />
              <ClaimField label="Date of birth" claimed={a.date_of_birth}   documented={di.date_of_birth} compareType="date" />
              <Field label="Mobile" value={txt(a.mobile)} />
              <Field label="Email"  value={txt(a.email)} />
              <ClaimField label="Address"       claimed={a.current_address} documented={di.address}      compareType="text" />
            </dl>
          </Card>

          <Card title="Employment & loan request">
            <p className="text-xs text-slate-400 mb-2">
              Values as entered by the applicant. Where documents extracted a different
              value, the document reading is shown in red below.
            </p>
            <dl>
              <ClaimField label="Employment type"  claimed={st.employment_type ?? val("employment_type")} documented={f.employment_type !== st.employment_type ? f.employment_type : undefined} compareType="text" />
              <ClaimField label="Employer"         claimed={st.employer_name ?? val("employer_name")} documented={f.employer_name !== st.employer_name ? f.employer_name : undefined} compareType="name" />
              <ClaimField label="Tenure (months)"  claimed={st.employment_tenure_months ?? val("employment_tenure_months")} documented={f.employment_tenure_months !== st.employment_tenure_months ? f.employment_tenure_months : undefined} compareType="text" />
              <ClaimField label="Monthly income"   claimed={st.monthly_income ?? val("monthly_income")} documented={f.monthly_income !== st.monthly_income ? f.monthly_income : undefined} fmt={inr} compareType="text" />
              <Field label="Loan requested"        value={inr(val("loan_amount_requested"))} />
              <Field label="Loan tenure (months)"  value={txt(val("loan_tenure_months"))} />
              <Field label="Purpose"               value={txt(val("loan_purpose"))} />
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
                    <li key={name} className="flex items-center justify-between text-sm gap-2">
                      <span className="text-slate-700 capitalize">{name.replace(/_/g, " ")}</span>
                      <span className="flex items-center gap-2">
                        {rec?.uploaded && rec?.reference?.startsWith("file://") && (
                          <button
                            onClick={() => setViewingDoc(name)}
                            className="text-brand hover:underline text-xs font-medium"
                          >
                            View
                          </button>
                        )}
                        <Pill tone={t.tone}>{rec?.uploaded ? t.text : "Not uploaded"}</Pill>
                      </span>
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
