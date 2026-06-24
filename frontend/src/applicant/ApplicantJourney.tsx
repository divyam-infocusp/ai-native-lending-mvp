import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, BUREAU_PULL_PURPOSE, DOC_AI_PURPOSE, REQUIRED_DOCUMENTS } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Spinner, ErrorNote } from "../components/ui";
import { DetailsForm } from "./DetailsForm";

type Step = "start" | "chat" | "consent";
interface ChatMsg {
  role: "assistant" | "user";
  text: string;
}

// Demo-only: pick a scenario to exercise each origination path on demand. The tag
// rides on the application and the mock bureau/OCR/lead steps honor it (no rebuild).
const DEMO_SCENARIOS: { value: string; label: string }[] = [
  { value: "clean", label: "Clean — happy path → Offer" },
  { value: "high_dti", label: "High DTI → Referred (underwriter decides)" },
  { value: "low_cibil", label: "Low CIBIL → Declined (hard knockout)" },
  { value: "thin_file", label: "Thin file → UW exception (re-run assessment)" },
  { value: "doc_mismatch", label: "Document mismatch → KYC exception" },
  { value: "lead_review", label: "Lead uncertain → Lead exception" },
];

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="card p-6 animate-fade-in">{children}</div>;
}

// `resumeId` continues an existing (not-yet-submitted) application's conversation.
export function ApplicantJourney({ resumeId }: { resumeId?: string }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>(resumeId ? "chat" : "start");
  const [appId, setAppId] = useState<string | null>(resumeId ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState("");
  const greeted = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  const [attachOpen, setAttachOpen] = useState(false);
  const [blocked, setBlocked] = useState<string | null>(null);   // lead-intent gate (#21)
  const [uploaded, setUploaded] = useState<Set<string>>(new Set());
  const [formMode, setFormMode] = useState(false);   // form-fill alternative (#42)
  const [demoMode, setDemoMode] = useState(false);   // off = normal application
  const [scenario, setScenario] = useState("clean"); // demo scenario selector

  function fail(e: any) {
    setError(e.message ?? String(e));
    setBusy(false);
  }

  async function start() {
    setBusy(true);
    try {
      const created = await api.createApplication(user?.name || "Applicant", demoMode ? scenario : undefined);
      setAppId(created.application_id);
      setStep("chat");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  // Greeting / resume: the copilot's durable memory (keyed by application_id)
  // continues the conversation where it left off.
  useEffect(() => {
    if (step === "chat" && appId && !greeted.current) {
      greeted.current = true;
      setBusy(true);
      api
        .onboardingMessage(appId, null)
        .then((r) => {
          setMessages([{ role: "assistant", text: r.assistant_message }]);
          if (r.complete) setStep("consent");
        })
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
      if (r.intent === "blocked") {
        setBlocked(r.assistant_message);   // not a loan request → stop the chat
        return;
      }
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

  async function removeDoc(docType: string) {
    if (!appId) return;
    try {
      await api.deleteDocument(appId, docType);
      setUploaded((s) => {
        const n = new Set(s);
        n.delete(docType);
        return n;
      });
    } catch (e) {
      fail(e);
    }
  }

  async function authorizeAndSubmit() {
    if (!appId) return;
    setBusy(true);
    try {
      await api.captureConsent(appId, BUREAU_PULL_PURPOSE);
      await api.captureConsent(appId, DOC_AI_PURPOSE);   // authorize AI document processing (#9)
      await api.startWorkflow(appId);
      navigate(`/apply/${appId}`, { state: { submitted: true } });   // → live status page (#40)
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto">
      <Stepper step={step} />
      {error && <div className="my-3"><ErrorNote>{error}</ErrorNote></div>}

      {blocked && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900">We can't take this forward</h1>
          <p className="text-slate-500 mt-1.5">{blocked}</p>
          <button onClick={() => navigate("/apply")} className="btn-primary mt-6">
            Start a new application
          </button>
        </Panel>
      )}

      {!blocked && step === "start" && (
        <Panel>
          <h1 className="text-xl font-semibold text-slate-900">Apply for a personal loan</h1>
          <p className="text-slate-500 mt-1.5">
            Our copilot will guide you through a short conversation, verify your details, and give
            you a decision — often within minutes.
          </p>
          <label className="mt-5 flex items-center gap-2 text-sm text-slate-600 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={demoMode}
              onChange={(e) => setDemoMode(e.target.checked)}
              className="h-4 w-4 accent-brand"
            />
            🧪 Demo mode — pick a scenario &amp; prefill the form (testing)
          </label>

          {demoMode && (
            <div className="mt-3 rounded-xl border border-dashed border-amber-300 bg-amber-50/50 p-3">
              <label className="block text-xs font-semibold text-amber-700 mb-1">Scenario</label>
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
                className="field w-full"
              >
                {DEMO_SCENARIOS.map((s) => (
                  <option key={s.value} value={s.value}>{s.label}</option>
                ))}
              </select>
              <p className="text-[11px] text-amber-600/80 mt-1">
                Drives the mock bureau / documents so you can trigger each path.
              </p>
            </div>
          )}
          <button onClick={start} disabled={busy} className="btn-primary mt-6">
            {busy ? "Starting…" : "Start application →"}
          </button>
        </Panel>
      )}

      {!blocked && step === "chat" && formMode && appId && (
        <Panel>
          <DetailsForm
            appId={appId}
            prefill={true}
            onDone={() => setStep("consent")}
            onSwitchToChat={() => setFormMode(false)}
          />
        </Panel>
      )}

      {!blocked && step === "chat" && !formMode && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900 mb-1">Tell us about yourself</h1>
          <p className="text-sm text-slate-500 mb-3">
            Chat with our copilot — or{" "}
            <button onClick={() => setFormMode(true)} className="text-brand font-medium hover:underline">
              fill a quick form instead →
            </button>
          </p>
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
                      <span className="flex items-center gap-3">
                        <span className="text-emerald-600 font-medium">✓ uploaded</span>
                        <button onClick={() => removeDoc(doc)} className="text-slate-400 hover:text-rose-600">Remove</button>
                      </span>
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

      {!blocked && step === "consent" && (
        <Panel>
          <h1 className="text-lg font-semibold text-slate-900">One last thing — your consent</h1>
          <p className="text-slate-500 mt-1.5">
            To assess your application we need your authorization to (a) check your credit bureau
            record and (b) process your uploaded documents to verify your details. We mint a fresh,
            auditable consent record for each.
          </p>
          <button onClick={authorizeAndSubmit} disabled={busy} className="btn-primary mt-6">
            {busy ? "Submitting…" : "Authorize & submit application"}
          </button>
        </Panel>
      )}
    </div>
  );
}

function Stepper({ step }: { step: Step }) {
  const steps: Step[] = ["start", "chat", "consent"];
  const labels: Record<Step, string> = { start: "Start", chat: "Details & documents", consent: "Consent" };
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
