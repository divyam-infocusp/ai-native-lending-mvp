import { useEffect, useRef, useState } from "react";
import {
  api,
  Application,
  BUREAU_PULL_PURPOSE,
  REQUIRED_DOCUMENTS,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StateGraph } from "../components/StateGraph";

type Step = "start" | "chat" | "consent" | "status";
interface ChatMsg {
  role: "assistant" | "user";
  text: string;
}

const TERMINAL = new Set([
  "OFFER_GENERATED",
  "OFFER_ACCEPTED",
  "OFFER_EXPIRED",
  "DECLINED",
  "LEAD_DECLINED",
  "REFERRED",
  "KYC_EXCEPTION",
  "UW_EXCEPTION",
  "LEAD_EXCEPTION",
]);

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="bg-white rounded-xl border border-slate-200 p-6">{children}</div>;
}

export function ApplicantJourney() {
  const { identity } = useAuth();
  const [step, setStep] = useState<Step>("start");
  const [appId, setAppId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // chat
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const greeted = useRef(false);

  // documents (uploaded inline from the chat)
  const [attachOpen, setAttachOpen] = useState(false);
  const [uploaded, setUploaded] = useState<Set<string>>(new Set());

  // status
  const [app, setApp] = useState<Application | null>(null);

  function fail(e: any) {
    setError(e.message ?? String(e));
    setBusy(false);
  }

  // --- start ---
  async function start() {
    setBusy(true);
    try {
      const created = await api.createApplication(identity?.name || "Applicant");
      setAppId(created.application_id);
      setStep("chat");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  // --- chat greeting on entering the step ---
  useEffect(() => {
    if (step === "chat" && appId && !greeted.current) {
      greeted.current = true;
      setBusy(true);
      api
        .onboardingMessage(appId, null)
        .then((r) => setMessages([{ role: "assistant", text: r.assistant_message }]))
        .catch(fail)
        .finally(() => setBusy(false));
    }
  }, [step, appId]);

  // Single path for every outgoing message (typed or auto-sent after uploads).
  async function sendMessage(text: string) {
    if (!appId || !text.trim()) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setBusy(true);
    try {
      const r = await api.onboardingMessage(appId, text);
      setMessages((m) => [...m, { role: "assistant", text: r.assistant_message }]);
      if (r.complete) setStep("consent");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  function send() {
    const t = draft.trim();
    if (!t) return;
    setDraft("");
    sendMessage(t);
  }

  // --- inline document upload (the fix for #37) ---
  async function upload(docType: string) {
    if (!appId) return;
    try {
      await api.uploadDocument(appId, docType, `mock://${appId}/${docType}.pdf`);
      const next = new Set(uploaded).add(docType);
      setUploaded(next);
      // When the last required document is in, tell the copilot so its
      // completeness gate (which includes document presence) can clear.
      if (next.size === REQUIRED_DOCUMENTS.length) {
        await sendMessage("I've uploaded all the required documents.");
      }
    } catch (e) {
      fail(e);
    }
  }

  // --- consent → start workflow → poll status ---
  async function authorizeAndSubmit() {
    if (!appId) return;
    setBusy(true);
    try {
      await api.captureConsent(appId, BUREAU_PULL_PURPOSE);
      await api.startWorkflow(appId);
      setStep("status");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (step !== "status" || !appId) return;
    let stop = false;
    const poll = async () => {
      try {
        const a = await api.getApplication(appId);
        if (!stop) setApp(a);
      } catch {
        /* keep polling */
      }
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [step, appId]);

  // ---------------------------------------------------------------- render
  return (
    <div className="max-w-2xl mx-auto">
      <Stepper step={step} />
      {error && <p className="text-sm text-rose-600 my-3">{error}</p>}

      {step === "start" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900">Apply for a personal loan</h1>
          <p className="text-slate-500 mt-1">
            Our copilot will help you complete your application in a few minutes.
          </p>
          <button
            onClick={start}
            disabled={busy}
            className="mt-5 rounded-lg bg-brand hover:bg-brand-dark text-white px-5 py-2.5 font-medium disabled:opacity-50"
          >
            {busy ? "Starting…" : "Start application"}
          </button>
        </Panel>
      )}

      {step === "chat" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900 mb-3">Tell us about yourself</h1>
          <div className="space-y-2 max-h-[45vh] overflow-y-auto mb-3">
            {messages.map((m, i) => (
              <div
                key={i}
                className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm ${
                  m.role === "assistant"
                    ? "bg-slate-100 text-slate-700"
                    : "bg-brand text-white ml-auto"
                }`}
              >
                {m.text}
              </div>
            ))}
            {busy && <div className="text-xs text-slate-400">…</div>}
          </div>

          {/* Inline document attach (#37) */}
          <div className="mb-3">
            <button
              onClick={() => setAttachOpen((o) => !o)}
              className="text-sm text-brand hover:text-brand-dark inline-flex items-center gap-1"
            >
              📎 Attach documents ({uploaded.size}/{REQUIRED_DOCUMENTS.length})
            </button>
            {attachOpen && (
              <ul className="mt-2 space-y-1.5 rounded-lg border border-slate-200 p-3">
                {REQUIRED_DOCUMENTS.map((doc) => (
                  <li key={doc} className="flex items-center justify-between text-sm">
                    <span className="capitalize">{doc.replace(/_/g, " ")}</span>
                    {uploaded.has(doc) ? (
                      <span className="text-emerald-600 font-medium">✓ uploaded</span>
                    ) : (
                      <button onClick={() => upload(doc)} className="text-brand hover:underline">
                        Upload
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex gap-2">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder="Type your reply…"
              className="flex-1 rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand"
            />
            <button
              onClick={send}
              disabled={busy}
              className="rounded-lg bg-brand hover:bg-brand-dark text-white px-4 py-2 disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </Panel>
      )}

      {step === "consent" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900">Consent</h1>
          <p className="text-slate-500 mt-1">
            To assess your application we need your authorization to check your credit bureau record.
          </p>
          <button
            onClick={authorizeAndSubmit}
            disabled={busy}
            className="mt-5 rounded-lg bg-brand hover:bg-brand-dark text-white px-5 py-2.5 font-medium disabled:opacity-50"
          >
            {busy ? "Submitting…" : "I authorize the credit check & submit"}
          </button>
        </Panel>
      )}

      {step === "status" && (
        <Panel>
          <Outcome app={app} />
        </Panel>
      )}
    </div>
  );
}

function Stepper({ step }: { step: Step }) {
  const steps: Step[] = ["start", "chat", "consent", "status"];
  const labels: Record<Step, string> = {
    start: "Start",
    chat: "Details & documents",
    consent: "Consent",
    status: "Outcome",
  };
  const idx = steps.indexOf(step);
  return (
    <div className="flex items-center gap-2 mb-5 text-xs">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-1 font-medium ${
              i <= idx ? "bg-brand text-white" : "bg-slate-200 text-slate-500"
            }`}
          >
            {labels[s]}
          </span>
          {i < steps.length - 1 && <span className="text-slate-300">→</span>}
        </div>
      ))}
    </div>
  );
}

function Outcome({ app }: { app: Application | null }) {
  const [explanation, setExplanation] = useState<string | null>(null);
  const state = app?.workflow_state ?? null;
  const done = state ? TERMINAL.has(state) : false;
  const offer = app?.features?.offer_letter;

  useEffect(() => {
    if (app && (state === "DECLINED" || state === "LEAD_DECLINED") && !explanation) {
      api.getExplanation(app.application_id).then((r) => setExplanation(r.text)).catch(() => {});
    }
  }, [app, state]);

  if (!done) {
    return (
      <div>
        <h1 className="text-lg font-semibold text-slate-900 mb-3">Processing your application…</h1>
        <p className="text-slate-500 mb-4">This updates live as your application moves through review.</p>
        <StateGraph current={state} visited={new Set(state ? [state] : [])} />
      </div>
    );
  }

  if (offer) {
    return (
      <div>
        <h1 className="text-xl font-semibold text-emerald-700">🎉 You're approved!</h1>
        <p className="text-slate-500 mt-1 mb-4">Here are your offer terms.</p>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-slate-500">Loan amount</dt><dd className="font-medium">₹{Number(offer.sanctioned_amount).toLocaleString()}</dd>
          <dt className="text-slate-500">Interest rate</dt><dd className="font-medium">{offer.interest_rate}% p.a. ({offer.rate_type})</dd>
          <dt className="text-slate-500">Tenure</dt><dd className="font-medium">{offer.tenure_months} months</dd>
          <dt className="text-slate-500">EMI</dt><dd className="font-medium">₹{Number(offer.emi).toLocaleString()}</dd>
          <dt className="text-slate-500">Processing fee</dt><dd>₹{Number(offer.processing_fee).toLocaleString()} + GST ₹{Number(offer.gst_on_fee).toLocaleString()}</dd>
          <dt className="text-slate-500">Net disbursal</dt><dd>₹{Number(offer.net_disbursal_amount).toLocaleString()}</dd>
          <dt className="text-slate-500">Total payable</dt><dd>₹{Number(offer.total_amount_payable).toLocaleString()}</dd>
          <dt className="text-slate-500">Valid until</dt><dd>{String(offer.valid_until).slice(0, 10)}</dd>
        </dl>
      </div>
    );
  }

  if (state === "REFERRED" || state?.endsWith("EXCEPTION")) {
    return (
      <div>
        <h1 className="text-lg font-semibold text-amber-700">Your application needs a closer look</h1>
        <p className="text-slate-500 mt-1">
          A member of our team is reviewing your application and will be in touch.
        </p>
      </div>
    );
  }

  // Declined
  return (
    <div>
      <h1 className="text-lg font-semibold text-rose-700">We're unable to approve your application</h1>
      {explanation && <p className="text-slate-600 mt-3 italic">{explanation}</p>}
    </div>
  );
}
