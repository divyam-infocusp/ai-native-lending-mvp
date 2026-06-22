// Reason-coded resolve actions per parked state (#15). Mirrors the backend's
// RESOLVE_REASON_CODES + the §4 legal transitions. Shared between the pipeline
// detail (which decides whether to offer a "Review & resolve" action) and the
// dedicated review screen (which performs the resolution).

export interface ResolveAction {
  to_state: string;
  reason_code: string;
  label: string;
  danger?: boolean;
}

export const RESOLUTIONS: Record<string, ResolveAction[]> = {
  LEAD_EXCEPTION: [
    { to_state: "LEAD_QUALIFIED", reason_code: "ELIGIBLE_ON_REVIEW", label: "Qualify lead" },
    { to_state: "LEAD_DECLINED", reason_code: "NOT_GENUINE", label: "Reject — not genuine", danger: true },
  ],
  KYC_EXCEPTION: [
    { to_state: "KYC_VERIFIED", reason_code: "DOC_REVERIFIED", label: "Mark documents verified" },
    { to_state: "DECLINED", reason_code: "DOC_NOT_GENUINE", label: "Reject — documents not genuine", danger: true },
  ],
  UW_EXCEPTION: [
    { to_state: "UNDERWRITING", reason_code: "DATA_SUPPLEMENTED", label: "Re-run assessment" },
    { to_state: "DECLINED", reason_code: "CANNOT_UNDERWRITE", label: "Reject — cannot underwrite", danger: true },
  ],
  REFERRED: [
    { to_state: "APPROVED", reason_code: "MANUAL_APPROVE", label: "Approve" },
    { to_state: "DECLINED", reason_code: "MANUAL_DECLINE", label: "Decline", danger: true },
  ],
};

export function isParked(state?: string | null): boolean {
  return !!state && state in RESOLUTIONS;
}
