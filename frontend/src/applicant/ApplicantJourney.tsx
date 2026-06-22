import { useEffect, useRef, useState } from "react";
import { api, Application, BUREAU_PULL_PURPOSE, REQUIRED_DOCUMENTS } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StateGraph } from "../components/StateGraph";
import { Spinner, ErrorNote } from "../components/ui";

type Step = "start" | "chat" | "consent" | "status";
interface ChatMsg {
  role: "assistant" | "user";
  text: string;
}

const TERMINAL = new Set([
  "OFFER_GENERATED", "OFFER_ACCEPTED", "OFFER_EXPIRED",
  "DECLINED", "LEAD_DECLINED", "REFERRED",
  "KYC_EXCEPTION", "UW_EXCEPTION", "LEAD_EXCEPTION",
]);

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="card p-6 animate-fade-in">{children}</div>;
}

export function ApplicantJourney() {
  const { user } = useAuth();
  const [step, setStep] = useState<Step>("start");
  const [appId, setAppId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const greeted = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  const [attachOpen, setAttachOpen] = useState(false);
  const [uploaded, setUploaded] = useState<Set<string>>(new Set());

  const [app, setApp] = useState<Application | null>(null);

  function fail(e: any) {
    setError(e.message ?? String(e));
    setBusy(false);
  }

  async function start() {
    setBusy(true);
    try {
      const created = await api.createApplication(user?.name || "Applicant");
      setAppId(created.application_id);
      setStep("chat");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

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

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

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

  // Inline document upload (#37) — copilot's completeness gate includes documents.
  async function upload(docType: string) {
    if (!appId) return;
    try {
      await api.uploadDocument(appId, docType, `mock://${appId}/${docType}.pdf`);
      const next = new Set(uploaded).add(docType);
      setUploaded(next);
      if (next.size === REQUIRED_DOCUMENTS.length) {
        await sendMessage("I've uploaded all the required documents.");
      }
    } catch (e) {
      fail(e);
    }
  }

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

  return (
    <div className="max-w-2xl mx-auto">
      <Stepper step={step} />
      {error && <div className="my-3"><ErrorNote>{error}</ErrorNote></div>}

      {step === "start" && (
        <Panel>
          <h1 className="text-xl font-semibold text-slate-900">Apply for a personal loan</h1>
          <p className="text-slate-500 mt-1.5">
            Our copilot will guide you through a short conversation, verify your details, and give
            you a decision — often within minutes.
          </p>
          <button onClick={start} disabled={busy} className="btn-primary mt-6">
            {busy ? "Starting…" : "Start application →"}
          </button>
        </Panel>
      )}

      {step === "chat" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900 mb-3">Tell us about yourself</h1>
          <div ref={scroller} className="space-y-3 max-h-[45vh] overflow-y-auto mb-3 pr-1">
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "gap-2"}`}>
                {m.role === "assistant" && (
                  <div className="h-7 w-7 shrink-0 rounded-full bg-gradient-to-br from-brand-400 to-brand-700 grid place-items-center text-white text-xs font-bold">
                    AI
                  </div>
                )}
                <div
                  className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm ${
                    m.role === "assistant"
                      ? "bg-slate-100 text-slate-700 rounded-tl-sm"
                      : "bg-brand text-white rounded-tr-sm"
                  }`}
                >
                  {m.text}
                </div>
              </div>
            ))}
            {busy && <div className="pl-9"><Spinner /></div>}
          </div>

          {/* Inline document attach (#37) */}
          <div className="mb-3">
            <button onClick={() => setAttachOpen((o) => !o)} className="text-sm text-brand hover:text-brand-dark font-medium">
              📎 Attach documents ({uploaded.size}/{REQUIRED_DOCUMENTS.length})
            </button>
            {attachOpen && (
              <ul className="mt-2 space-y-1.5 rounded-xl border border-slate-200 p-3 bg-slate-50/60">
                {REQUIRED_DOCUMENTS.map((doc) => (
                  <li key={doc} className="flex items-center justify-between text-sm">
                    <span className="capitalize text-slate-600">{doc.replace(/_/g, " ")}</span>
                    {uploaded.has(doc) ? (
                      <span className="text-emerald-600 font-medium">✓ uploaded</span>
                    ) : (
                      <button onClick={() => upload(doc)} className="text-brand hover:underline">Upload</button>
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
              className="field flex-1"
            />
            <button onClick={send} disabled={busy} className="btn-primary">Send</button>
          </div>
        </Panel>
      )}

      {step === "consent" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900">One last thing — your consent</h1>
          <p className="text-slate-500 mt-1.5">
            To assess your application we need your authorization to check your credit bureau record.
            We mint a fresh, auditable consent record at the moment of the check.
          </p>
          <button onClick={authorizeAndSubmit} disabled={busy} className="btn-primary mt-6">
            {busy ? "Submitting…" : "Authorize & submit application"}
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
    start: "Start", chat: "Details & documents", consent: "Consent", status: "Outcome",
  };
  const idx = steps.indexOf(step);
  return (
    <div className="flex items-center gap-2 mb-6 text-xs">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <span className={`rounded-full px-3 py-1 font-medium transition ${
            i < idx ? "bg-emerald-100 text-emerald-700"
              : i === idx ? "bg-brand text-white shadow-glow"
              : "bg-slate-200 text-slate-500"
          }`}>
            {i < idx ? "✓ " : ""}{labels[s]}
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
  const inr = (n: any) => `₹${Number(n).toLocaleString("en-IN")}`;

  useEffect(() => {
    if (app && (state === "DECLINED" || state === "LEAD_DECLINED") && !explanation) {
      api.getExplanation(app.application_id).then((r) => setExplanation(r.text)).catch(() => {});
    }
  }, [app, state]);

  if (!done) {
    return (
      <div>
        <h1 className="text-lg font-semibold text-slate-900 mb-1">Processing your application…</h1>
        <p className="text-slate-500 mb-5">This updates live as your application moves through review.</p>
        <StateGraph current={state} visited={new Set(state ? [state] : [])} />
      </div>
    );
  }

  if (offer) {
    return (
      <div>
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
    );
  }

  if (state === "REFERRED" || state?.endsWith("EXCEPTION")) {
    return (
      <div>
        <h1 className="text-lg font-semibold text-amber-700">Your application needs a closer look</h1>
        <p className="text-slate-500 mt-1.5">A member of our team is reviewing your application and will be in touch shortly.</p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-lg font-semibold text-rose-700">We're unable to approve your application</h1>
      {explanation && <p className="text-slate-600 mt-3 italic">{explanation}</p>}
    </div>
  );
}
