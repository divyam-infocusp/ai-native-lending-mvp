// Form-fill alternative to the copilot (#42) — fill all details at once + attach
// documents, then continue. Faster than the multi-turn chat (great for demos).
import { useState } from "react";
import { api, REQUIRED_DOCUMENTS } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNote } from "../components/ui";

// Demo convenience: a complete, valid sample so you can run a scenario without
// re-typing every field. The scenario selector drives the actual outcome.
const DEFAULTS: Record<string, string> = {
  date_of_birth: "1990-05-10",
  pan: "ABCDE1234F",
  aadhaar: "234567890124",
  mobile: "9876543210",
  current_address: "12 MG Road, Pune 411001",
  employment_type: "salaried",
  employer_name: "Infosys",
  employment_tenure_months: "48",
  monthly_income: "85000",
  loan_amount_requested: "300000",
  loan_tenure_months: "36",
  loan_purpose: "home renovation",
};

interface FieldDef {
  key: string;
  label: string;
  type?: string;
  placeholder?: string;
  options?: string[];
}

const GROUPS: { title: string; fields: FieldDef[] }[] = [
  {
    title: "Identity",
    fields: [
      { key: "full_name", label: "Full name", placeholder: "Ravi Kumar" },
      { key: "date_of_birth", label: "Date of birth", placeholder: "1990-05-10" },
      { key: "pan", label: "PAN", placeholder: "ABCDE1234F" },
      { key: "aadhaar", label: "Aadhaar", placeholder: "234567890124" },
    ],
  },
  {
    title: "Contact",
    fields: [
      { key: "mobile", label: "Mobile", placeholder: "9876543210" },
      { key: "current_address", label: "Current address", placeholder: "1 MG Road, Pune" },
    ],
  },
  {
    title: "Employment & income",
    fields: [
      { key: "employment_type", label: "Employment type", options: ["salaried", "self_employed", "business", "unemployed"] },
      { key: "employer_name", label: "Employer", placeholder: "Infosys" },
      { key: "employment_tenure_months", label: "Tenure (months)", type: "number", placeholder: "48" },
      { key: "monthly_income", label: "Monthly income (₹)", type: "number", placeholder: "85000" },
    ],
  },
  {
    title: "Loan",
    fields: [
      { key: "loan_amount_requested", label: "Amount requested (₹)", type: "number", placeholder: "300000" },
      { key: "loan_tenure_months", label: "Tenure (months)", type: "number", placeholder: "36" },
      { key: "loan_purpose", label: "Purpose", placeholder: "home renovation" },
    ],
  },
];

export function DetailsForm({
  appId,
  prefill = false,
  onDone,
  onSwitchToChat,
}: {
  appId: string;
  prefill?: boolean;
  onDone: () => void;
  onSwitchToChat: () => void;
}) {
  const { user } = useAuth();
  // In demo mode, pre-fill with a complete valid sample so a run is one click.
  // Otherwise start blank (a real applicant fills their own details); the full
  // name carries over from the signed-in applicant either way.
  const [values, setValues] = useState<Record<string, string>>(() =>
    prefill
      ? { ...DEFAULTS, full_name: user?.name || "Ravi Kumar" }
      : { full_name: user?.name || "" },
  );
  const [uploaded, setUploaded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: string, v: string) => setValues((s) => ({ ...s, [k]: v }));

  async function upload(doc: string) {
    try {
      await api.uploadDocument(appId, doc, `mock://${appId}/${doc}.pdf`);
      setUploaded((s) => new Set(s).add(doc));
    } catch (e: any) {
      setError(e.message);
    }
  }

  // Real file upload (#9) — stores the actual bytes for the OCR/LLM extractor.
  async function uploadFile(doc: string, file: File) {
    try {
      await api.uploadDocumentFile(appId, doc, file);
      setUploaded((s) => new Set(s).add(doc));
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function uploadAll() {
    const next = new Set(uploaded);
    try {
      for (const doc of REQUIRED_DOCUMENTS) {
        if (!next.has(doc)) {
          await api.uploadDocument(appId, doc, `mock://${appId}/${doc}.pdf`);
          next.add(doc);
        }
      }
      setUploaded(next);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      // drop empty fields; the backend coerces types
      const fields = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
      const res = await api.submitDetails(appId, fields);
      const docsMissing = REQUIRED_DOCUMENTS.some((d) => !uploaded.has(d));
      if (!res.complete && docsMissing) {
        setError("Please fill all fields and attach all documents.");
        return;
      }
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-lg font-semibold text-slate-900">Your details</h1>
        <button onClick={onSwitchToChat} className="text-sm text-brand hover:underline">💬 Use the chat instead</button>
      </div>

      <div className="space-y-5">
        {GROUPS.map((g) => (
          <div key={g.title}>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-2">{g.title}</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {g.fields.map((f) => (
                <div key={f.key}>
                  <label className="text-sm text-slate-600">{f.label}</label>
                  {f.options ? (
                    <select
                      className="field mt-1"
                      value={values[f.key] ?? ""}
                      onChange={(e) => set(f.key, e.target.value)}
                    >
                      <option value="">Select…</option>
                      {f.options.map((o) => (
                        <option key={o} value={o}>{o.replace(/_/g, " ")}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="field mt-1"
                      type={f.type ?? "text"}
                      placeholder={f.placeholder}
                      value={values[f.key] ?? ""}
                      onChange={(e) => set(f.key, e.target.value)}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}

        <div>
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Documents ({uploaded.size}/{REQUIRED_DOCUMENTS.length})
            </div>
            {prefill && uploaded.size < REQUIRED_DOCUMENTS.length && (
              <button onClick={uploadAll} className="text-xs text-brand hover:underline font-medium">
                Attach samples (demo)
              </button>
            )}
          </div>
          <ul className="space-y-1.5 rounded-xl border border-slate-200 p-3 bg-slate-50/60">
            {REQUIRED_DOCUMENTS.map((doc) => (
              <li key={doc} className="flex items-center justify-between text-sm">
                <span className="capitalize text-slate-600">{doc.replace(/_/g, " ")}</span>
                {uploaded.has(doc) ? (
                  <span className="text-emerald-600 font-medium">✓ uploaded</span>
                ) : (
                  <label className="text-brand hover:underline cursor-pointer">
                    Choose file
                    <input
                      type="file"
                      accept="image/*,application/pdf"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) uploadFile(doc, f);
                      }}
                    />
                  </label>
                )}
              </li>
            ))}
          </ul>
        </div>

        {error && <ErrorNote>{error}</ErrorNote>}

        <button onClick={submit} disabled={busy} className="btn-primary">
          {busy ? "Saving…" : "Save & continue →"}
        </button>
      </div>
    </div>
  );
}
