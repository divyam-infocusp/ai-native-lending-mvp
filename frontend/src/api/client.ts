// Typed client for the Origination API (#36) + Auth (#38). Paths are relative to
// /api, which nginx (prod) / Vite (dev) proxies to the backend.

const BASE = "/api";
const TOKEN_KEY = "lending.token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = tokenStore.get();
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    const err = new Error(`${detail}`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Types ---------------------------------------------------------------

export type Role = "applicant" | "underwriter";

export interface AuthUser {
  user_id: string;
  email: string;
  role: Role;
  name: string;
}

export interface ApplicationSummary {
  application_id: string;
  applicant_name: string;
  status: string;
  workflow_state: string | null;
  disposition: string | null;
  updated_at: string;
}

export interface FieldConfidence {
  field_name: string;
  confidence: number;
  risk_flags: string[];
}

export interface Decision {
  disposition: string;
  reason_codes: string[];
  band?: string | null;
  score?: number | null;
  explanation?: string | null;
  source?: string;
}

export interface Application {
  application_id: string;
  status: string;
  workflow_state: string | null;
  owner_user_id?: string | null;
  applicant: { full_name: string; pan?: string; aadhaar?: string; [k: string]: unknown };
  features: Record<string, any>;
  consent: { authorizations: { purpose: string; status: string }[] };
  kyc: { status: string; field_confidence: FieldConfidence[]; risk_flags: string[] };
  decision: Decision | null;
}

export interface AuditEvent {
  seq: number;
  event_id: string;
  event_type: string;
  payload: Record<string, any>;
  actor?: string | null;
  created_at?: string;
}

export interface OnboardingTurn {
  application_id: string;
  assistant_message: string;
  complete: boolean;
  missing: string[];
  collected: Record<string, any>;
}

export interface PolicyView {
  version: string;
  rules: { reason_code: string; label: string; threshold: number | null; unit: string; description: string; type: "hard" | "soft" }[];
  bands: { band: string; min_score: number; rate_pct: number | null; max_amount: number | null }[];
  scorecard: { min_score: number; income_haircut_pct: number };
  pricing: {
    tenure_min_months: number; tenure_max_months: number; affordability_dti: number;
    processing_fee_pct: number; gst_pct: number; offer_validity_days: number;
  };
  documents: {
    min_confidence: number; min_ocr_conf: number; name_match_min_ratio: number;
    income_match_tolerance_pct: number; key_fields: string[];
  };
}

export const REQUIRED_DOCUMENTS = [
  "aadhaar_card",
  "pan_card",
  "salary_slips",
  "form16",
] as const;

export const BUREAU_PULL_PURPOSE = "bureau_pull";
// Authorization to process uploaded documents with AI/LLM (DPDP) — #9.
export const DOC_AI_PURPOSE = "document_ai_processing";

// ---- Endpoints -----------------------------------------------------------

export const api = {
  // auth (#38)
  register: (email: string, password: string, name: string, role: Role) =>
    req<{ token: string; user: AuthUser }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, name, role }),
    }),
  login: (email: string, password: string) =>
    req<{ token: string; user: AuthUser }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => req<AuthUser>("/auth/me"),

  // Lending policy (read-only) — the live thresholds the engine applies.
  getPolicy: () => req<PolicyView>("/policy"),

  // applications
  listApplications: () =>
    req<{ applications: ApplicationSummary[] }>("/applications").then((r) => r.applications),
  getApplication: (id: string) => req<Application>(`/applications/${id}`),
  createApplication: (fullName: string, demoScenario?: string) =>
    req<Application>("/applications", {
      method: "POST",
      body: JSON.stringify({
        applicant: { full_name: fullName },
        ...(demoScenario ? { features: { demo_scenario: demoScenario } } : {}),
      }),
    }),
  onboardingMessage: (id: string, message: string | null) =>
    req<OnboardingTurn>(`/applications/${id}/onboarding/message`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  submitDetails: (id: string, fields: Record<string, any>) =>
    req<{ complete: boolean; missing: string[] }>(`/applications/${id}/details`, {
      method: "POST",
      body: JSON.stringify({ fields }),
    }),
  captureConsent: (id: string, purpose: string) =>
    req<unknown>(`/applications/${id}/consent`, { method: "POST", body: JSON.stringify({ purpose }) }),
  uploadDocument: (id: string, docType: string, reference?: string) =>
    req<unknown>(`/applications/${id}/documents`, {
      method: "POST",
      body: JSON.stringify({ doc_type: docType, reference }),
    }),
  // Real file upload (#9, Phase A) — multipart, so bypass the JSON `req` helper
  // (the browser sets the multipart boundary; we must not force Content-Type).
  uploadDocumentFile: async (id: string, docType: string, file: File) => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("file", file);
    const token = tokenStore.get();
    const res = await fetch(`${BASE}/applications/${id}/documents/file`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON */ }
      throw new Error(detail);
    }
    return res.json();
  },
  // Fetch a stored document as a Blob (auth header required — can't use a plain URL).
  // Caller should URL.createObjectURL() the blob for display, and revoke when done.
  getDocumentFile: async (id: string, docType: string): Promise<{ blob: Blob; contentType: string }> => {
    const token = tokenStore.get();
    const res = await fetch(`${BASE}/applications/${id}/documents/${docType}/file`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON */ }
      throw new Error(detail);
    }
    const blob = await res.blob();
    return { blob, contentType: res.headers.get("Content-Type") ?? "application/octet-stream" };
  },
  startWorkflow: (id: string) =>
    req<{ workflow_run: string; status: string }>(`/applications/${id}/start`, { method: "POST" }),
  getAudit: (id: string) =>
    req<{ events: AuditEvent[] }>(`/applications/${id}/audit`).then((r) => r.events),
  getExplanation: (id: string) =>
    req<{ reason_codes: string[]; text: string }>(`/applications/${id}/explanation`),

  // Ops Console (#15): resolve a parked case (underwriter only). `note` is a
  // required human justification, recorded in the audit trail.
  resolve: (id: string, toState: string, reasonCode: string, note: string) =>
    req<{ resolved_to: string; status: string }>(`/applications/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ to_state: toState, reason_code: reasonCode, note }),
    }),
};
