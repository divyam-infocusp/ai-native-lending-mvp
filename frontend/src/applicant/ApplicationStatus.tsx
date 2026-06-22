import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Application } from "../api/client";
import { StateGraph } from "../components/StateGraph";
import { Spinner, ErrorNote } from "../components/ui";
import { ApplicantJourney } from "./ApplicantJourney";

const TERMINAL = new Set([
  "OFFER_GENERATED", "OFFER_ACCEPTED", "OFFER_EXPIRED",
  "DECLINED", "LEAD_DECLINED", "REFERRED",
  "KYC_EXCEPTION", "UW_EXCEPTION", "LEAD_EXCEPTION",
]);

const inr = (n: any) => `₹${Number(n).toLocaleString("en-IN")}`;

export function ApplicationStatus() {
  const { id } = useParams();
  const [app, setApp] = useState<Application | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let stop = false;
    const poll = async () => {
      try {
        const a = await api.getApplication(id);
        if (!stop) {
          setApp(a);
          setError(null);
        }
      } catch (e: any) {
        if (!stop) setError(e.message);
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [id]);

  const state = app?.workflow_state ?? null;

  useEffect(() => {
    if (app && (state === "DECLINED" || state === "LEAD_DECLINED") && !explanation) {
      api.getExplanation(app.application_id).then((r) => setExplanation(r.text)).catch(() => {});
    }
  }, [app, state]);

  if (error) return <div className="max-w-2xl mx-auto"><ErrorNote>{error}</ErrorNote></div>;
  if (!app) return <div className="max-w-2xl mx-auto card p-8 grid place-items-center"><Spinner label="Loading…" /></div>;

  // A draft that was never submitted → resume the conversation.
  if (!state) {
    return (
      <div>
        <BackLink />
        <div className="max-w-2xl mx-auto mb-2 text-sm text-slate-500">Continue your application</div>
        <ApplicantJourney resumeId={app.application_id} />
      </div>
    );
  }

  const done = TERMINAL.has(state);
  const offer = app.features?.offer_letter;

  return (
    <div className="max-w-2xl mx-auto">
      <BackLink />
      <div className="card p-6 animate-fade-in">
        {!done && (
          <>
            <h1 className="text-lg font-semibold text-slate-900 mb-1">Your application is being reviewed…</h1>
            <p className="text-slate-500 mb-5">This updates live as it moves through our checks.</p>
            <StateGraph current={state} visited={new Set([state])} />
          </>
        )}

        {done && offer && (
          <>
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
          </>
        )}

        {done && !offer && (state === "REFERRED" || state.endsWith("EXCEPTION")) && (
          <>
            <h1 className="text-lg font-semibold text-amber-700">Your application needs a closer look</h1>
            <p className="text-slate-500 mt-1.5">A member of our team is reviewing it and will be in touch shortly.</p>
          </>
        )}

        {done && !offer && (state === "DECLINED" || state === "LEAD_DECLINED") && (
          <>
            <h1 className="text-lg font-semibold text-rose-700">We're unable to approve your application</h1>
            {explanation && <p className="text-slate-600 mt-3 italic">{explanation}</p>}
          </>
        )}
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <div className="max-w-2xl mx-auto mb-3">
      <Link to="/apply" className="text-sm text-brand hover:underline">← Your applications</Link>
    </div>
  );
}
