import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { api, Application, AuditEvent } from "../api/client";
import { Spinner, ErrorNote } from "../components/ui";
import { ApplicantJourney } from "./ApplicantJourney";

const inr = (n: any) => `₹${Number(n).toLocaleString("en-IN")}`;

const TERMINAL = new Set([
  "OFFER_GENERATED", "OFFER_ACCEPTED", "OFFER_EXPIRED",
  "DECLINED", "LEAD_DECLINED", "REFERRED",
  "KYC_EXCEPTION", "UW_EXCEPTION", "LEAD_EXCEPTION",
]);

// Plain-language pending message per parked state (#15) — what's happening and
// whether the applicant needs to do anything.
const PARKED: Record<string, { title: string; body: string }> = {
  LEAD_EXCEPTION: {
    title: "We're taking a closer look at your eligibility",
    body: "A member of our team is reviewing whether we can proceed. Nothing is needed from you right now — we'll update this page.",
  },
  KYC_EXCEPTION: {
    title: "A specialist is reviewing your documents",
    body: "We need a closer look at your identity documents before continuing. We'll update you here once it's done.",
  },
  UW_EXCEPTION: {
    title: "We're reviewing a few more details",
    body: "Your credit assessment needs a manual review. Nothing is needed from you right now.",
  },
  REFERRED: {
    title: "Your application is with an underwriter",
    body: "An underwriter is making the final decision on your application. We'll update this page as soon as it's complete.",
  },
};

// ---- Journey summary -------------------------------------------------------

type StageState = "done" | "active" | "attention" | "failed" | "upcoming";

const STAGES = [
  { key: "received", label: "Application received", desc: "Your details and documents are in." },
  { key: "eligibility", label: "Eligibility check", desc: "Confirming you fit our lending criteria." },
  { key: "kyc", label: "Identity & documents", desc: "Verifying your identity and uploaded documents." },
  { key: "credit", label: "Credit & affordability", desc: "Reviewing your credit history and repayment capacity." },
  { key: "decision", label: "Decision", desc: "The final outcome of your application." },
] as const;

const STATUS_LABEL: Record<StageState, string> = {
  done: "Done", active: "In progress", attention: "Under review",
  failed: "Not approved", upcoming: "Pending",
};

// What reaching a given state implies has already been completed — so the summary
// is correct even before the audit trail finishes loading.
// DECLINED is intentionally NOT mapped here: the audit trail tells us which stages
// were actually reached, so we never pre-assume KYC/credit were done for a decline
// that happened at the KYC stage. Without audit events we show the bare minimum.
function impliedVisited(state: string): Set<string> {
  const v = new Set<string>([state]);
  const add = (...s: string[]) => s.forEach((x) => v.add(x));
  if (["REFERRED", "APPROVED", "OFFER_GENERATED", "OFFER_ACCEPTED", "OFFER_EXPIRED", "DECISION_READY"].includes(state))
    add("LEAD_QUALIFIED", "KYC_VERIFIED", "DECISION_READY");
  if (state === "UW_EXCEPTION") add("LEAD_QUALIFIED", "KYC_VERIFIED");
  if (state === "KYC_EXCEPTION") add("LEAD_QUALIFIED");
  // DECLINED: don't pre-assume anything — the audit trail's state_transitions
  // tell us exactly how far the application got before it was declined.
  return v;
}

function stageState(key: string, state: string, visited: Set<string>): StageState {
  const v = (s: string) => visited.has(s);
  switch (key) {
    case "received":
      return "done";
    case "eligibility":
      if (state === "LEAD") return "active";
      if (state === "LEAD_EXCEPTION") return "attention";
      if (state === "LEAD_DECLINED") return "failed";
      return v("LEAD_QUALIFIED") ? "done" : "upcoming";
    case "kyc":
      if (state === "KYC_EXCEPTION") return "attention";
      if (state === "APPLICATION_SUBMITTED" || state === "KYC_IN_PROGRESS") return "active";
      // Declined while parked at KYC_EXCEPTION → the KYC stage failed, not done.
      if (state === "DECLINED" && v("KYC_EXCEPTION") && !v("KYC_VERIFIED")) return "failed";
      return v("KYC_VERIFIED") ? "done" : "upcoming";
    case "credit":
      if (state === "UW_EXCEPTION") return "attention";
      if (state === "UNDERWRITING") return "active";
      // Declined before credit ran (e.g. from KYC stage) → credit is just upcoming.
      if (state === "DECLINED" && !v("DECISION_READY") && !v("UNDERWRITING")) return "upcoming";
      return v("DECISION_READY") ? "done" : "upcoming";
    case "decision":
      if (state === "DECLINED" || state === "LEAD_DECLINED") return "failed";
      if (["APPROVED", "OFFER_GENERATED", "OFFER_ACCEPTED", "OFFER_EXPIRED"].includes(state)) return "done";
      if (state === "REFERRED") return "attention";
      if (state === "DECISION_READY") return "active";
      return "upcoming";
    default:
      return "upcoming";
  }
}

const MARK: Record<StageState, { dot: string; icon: string; text: string }> = {
  done: { dot: "bg-emerald-500 border-emerald-500 text-white", icon: "✓", text: "text-emerald-600" },
  active: { dot: "bg-brand border-brand text-white animate-pulse", icon: "•", text: "text-brand" },
  attention: { dot: "bg-amber-400 border-amber-400 text-white", icon: "!", text: "text-amber-600" },
  failed: { dot: "bg-rose-500 border-rose-500 text-white", icon: "✕", text: "text-rose-600" },
  upcoming: { dot: "bg-white border-slate-300 text-slate-300", icon: "", text: "text-slate-400" },
};

function JourneySummary({ state, visited }: { state: string; visited: Set<string> }) {
  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-700 mb-4">Your application so far</h2>
      <ol className="relative">
        {STAGES.map((s, i) => {
          const st = stageState(s.key, state, visited);
          const m = MARK[st];
          const last = i === STAGES.length - 1;
          const muted = st === "upcoming";
          return (
            <li key={s.key} className="flex gap-3.5 pb-5 last:pb-0 relative">
              {!last && (
                <span className={`absolute left-3 top-7 bottom-0 w-px ${st === "done" ? "bg-emerald-200" : "bg-slate-200"}`} />
              )}
              <span className={`relative z-10 grid place-items-center w-6 h-6 shrink-0 rounded-full border text-[11px] font-bold ${m.dot}`}>
                {m.icon}
              </span>
              <div className="flex-1 -mt-0.5">
                <div className="flex items-center justify-between gap-2">
                  <span className={`text-sm font-medium ${muted ? "text-slate-400" : "text-slate-800"}`}>{s.label}</span>
                  <span className={`text-[11px] font-medium ${m.text}`}>{STATUS_LABEL[st]}</span>
                </div>
                <p className={`text-xs mt-0.5 ${muted ? "text-slate-300" : "text-slate-500"}`}>{s.desc}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---- Page ------------------------------------------------------------------

export function ApplicationStatus() {
  const { id } = useParams();
  const location = useLocation();
  const justSubmitted: boolean = !!(location.state as any)?.submitted;
  const [app, setApp] = useState<Application | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let stop = false;
    const poll = async () => {
      try {
        const [a, ev] = await Promise.all([api.getApplication(id), api.getAudit(id).catch(() => [])]);
        if (!stop) {
          setApp(a);
          setEvents(ev);
          setError(null);
        }
      } catch (e: any) {
        if (!stop) setError(e.message);
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => { stop = true; clearInterval(t); };
  }, [id]);

  const state = app?.workflow_state ?? null;

  useEffect(() => {
    if (app && (state === "DECLINED" || state === "LEAD_DECLINED") && !explanation) {
      api.getExplanation(app.application_id).then((r) => setExplanation(r.text)).catch(() => {});
    }
  }, [app, state]);

  if (error) return <div className="max-w-2xl mx-auto"><ErrorNote>{error}</ErrorNote></div>;
  if (!app) return <div className="max-w-2xl mx-auto card p-8 grid place-items-center"><Spinner label="Loading…" /></div>;

  // Not yet submitted → resume the conversation.
  // But if the user just submitted (justSubmitted flag), the workflow is launching
  // and the state will appear within seconds — show a "processing" screen instead.
  if (!state && !justSubmitted) {
    return (
      <div>
        <BackLink />
        <div className="max-w-2xl mx-auto mb-2 text-sm text-slate-500">Continue your application</div>
        <ApplicantJourney resumeId={app.application_id} />
      </div>
    );
  }

  // Workflow just launched — show the stage stepper immediately (stages are
  // "upcoming") with a "Launching…" header. The 2-second poll replaces this as
  // soon as the first state_transition event lands.
  if (!state) {
    return (
      <div className="max-w-2xl mx-auto space-y-4">
        <BackLink />
        <div className="card p-6 border-l-4 border-l-brand animate-fade-in">
          <div className="flex items-center gap-3 mb-2">
            <span className="inline-block h-4 w-4 rounded-full bg-brand animate-pulse" />
            <h1 className="text-lg font-semibold text-slate-900">Launching your application…</h1>
          </div>
          <p className="text-slate-500 text-sm">
            We've received your consent and kicked off the checks. This page updates live — your
            application is moving through the stages below right now.
          </p>
        </div>
        <div className="card p-6">
          <JourneySummary state="APPLICATION_SUBMITTED" visited={new Set(["APPLICATION_SUBMITTED"])} />
          <p className="text-xs text-slate-400 mt-4 pt-4 border-t border-slate-100">
            No action needed from you. We'll update each stage as it completes.
          </p>
        </div>
      </div>
    );
  }

  // Build the "visited" set from real transitions, backfilled by what the current
  // state implies (robust before the audit trail loads).
  const visited = impliedVisited(state);
  events.forEach((e) => {
    if (e.event_type === "state_transition") {
      visited.add(e.payload.from);
      visited.add(e.payload.to);
    }
  });

  const offer = app.features?.offer_letter;
  const parked = PARKED[state];
  const declined = state === "DECLINED" || state === "LEAD_DECLINED";
  const inProgress = !TERMINAL.has(state);
  const declineReason =
    app.decision?.explanation || explanation ||
    "After a detailed review, we're unable to approve your application at this time.";

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <BackLink />

      {/* Headline status block */}
      {offer ? (
        <div className="card p-6 animate-fade-in">
          <div className="rounded-2xl bg-gradient-to-br from-emerald-500 to-emerald-700 text-white p-6 mb-5">
            <div className="text-emerald-100 text-sm">Congratulations 🎉</div>
            <div className="text-2xl font-semibold">You're approved</div>
            <div className="mt-3 text-4xl font-bold">{inr(offer.sanctioned_amount)}</div>
            <div className="text-emerald-100">at {offer.interest_rate}% p.a. · {offer.tenure_months} months</div>
          </div>
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-slate-500">Monthly EMI</dt><dd className="font-medium text-right">{inr(offer.emi)}</dd>
            <dt className="text-slate-500">Processing fee</dt><dd className="text-right">{inr(offer.processing_fee)} + GST {inr(offer.gst_on_fee)}</dd>
            <dt className="text-slate-500">Net disbursal</dt><dd className="text-right">{inr(offer.net_disbursal_amount)}</dd>
            <dt className="text-slate-500">Total payable</dt><dd className="text-right">{inr(offer.total_amount_payable)}</dd>
            <dt className="text-slate-500">Offer valid until</dt><dd className="text-right">{String(offer.valid_until).slice(0, 10)}</dd>
          </dl>
        </div>
      ) : parked ? (
        <div className="card p-6 border-l-4 border-l-amber-400 animate-fade-in">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="inline-grid place-items-center w-6 h-6 rounded-full bg-amber-100 text-amber-700 text-sm">⏳</span>
            <h1 className="text-lg font-semibold text-amber-700">{parked.title}</h1>
          </div>
          <p className="text-slate-600">{parked.body}</p>
        </div>
      ) : declined ? (
        <div className="card p-6 border-l-4 border-l-rose-400 animate-fade-in">
          <h1 className="text-lg font-semibold text-rose-700 mb-2">We're unable to approve your application</h1>
          <p className="text-slate-600">{declineReason}</p>
          <p className="text-xs text-slate-400 mt-3">
            If you believe this is a mistake, you can reach our support team and we'll be happy to take another look.
          </p>
        </div>
      ) : (
        <div className="card p-6 animate-fade-in">
          <h1 className="text-lg font-semibold text-slate-900 mb-1">Your application is being reviewed…</h1>
          <p className="text-slate-500">This page updates live as it moves through our checks.</p>
        </div>
      )}

      {/* Journey summary — actions so far, their status, and where it's pending */}
      <div className="card p-6">
        <JourneySummary state={state} visited={visited} />
        {(parked || inProgress) && (
          <p className="text-xs text-slate-400 mt-4 pt-4 border-t border-slate-100">
            No action is needed from you right now. We'll update this page automatically.
          </p>
        )}
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <div className="mb-1">
      <Link to="/apply" className="text-sm text-brand hover:underline">← Your applications</Link>
    </div>
  );
}
