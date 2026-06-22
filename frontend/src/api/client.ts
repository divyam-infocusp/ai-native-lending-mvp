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

export const REQUIRED_DOCUMENTS = [
  "identity_proof",
  "address_proof",
  "salary_slips",
  "bank_statement",
  "form16",
] as const;

export const BUREAU_PULL_PURPOSE = "bureau_pull";

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

  // applications
  listApplications: () =>
    req<{ applications: ApplicationSummary[] }>("/applications").then((r) => r.applications),
  getApplication: (id: string) => req<Application>(`/applications/${id}`),
  createApplication: (fullName: string) =>
    req<Application>("/applications", {
      method: "POST",
      body: JSON.stringify({ applicant: { full_name: fullName } }),
    }),
  onboardingMessage: (id: string, message: string | null) =>
    req<OnboardingTurn>(`/applications/${id}/onboarding/message`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  captureConsent: (id: string, purpose: string) =>
    req<unknown>(`/applications/${id}/consent`, { method: "POST", body: JSON.stringify({ purpose }) }),
  uploadDocument: (id: string, docType: string, reference?: string) =>
    req<unknown>(`/applications/${id}/documents`, {
      method: "POST",
      body: JSON.stringify({ doc_type: docType, reference }),
    }),
  startWorkflow: (id: string) =>
    req<{ workflow_run: string; status: string }>(`/applications/${id}/start`, { method: "POST" }),
  getAudit: (id: string) =>
    req<{ events: AuditEvent[] }>(`/applications/${id}/audit`).then((r) => r.events),
  getExplanation: (id: string) =>
    req<{ reason_codes: string[]; text: string }>(`/applications/${id}/explanation`),

  // ---- Ops actions (#15) — seam for the exception/override console -------
  // Not wired yet (resolveException / applyOverride land here when #15 ships).
};
