# AI-Native Lending Platform — What We Built

> Unsecured personal loans, salaried segment, India. Single-command demo stack.

---

## 1. The Big Picture

A full loan origination system where **LLMs assist but never decide**. Every credit outcome is produced by deterministic code (rules engine + scorecard). The LLMs handle language (onboarding copilot, OCR extraction) and the humans handle overrides — the engine handles everything in between.

```
Applicant → Onboarding → KYC / Doc Intel → Underwriting → Decision → Offer / Decline
                                ↕                  ↕              ↕
                          KYC_EXCEPTION      UW_EXCEPTION      REFERRED
                                ↕                  ↕              ↕
                          Underwriter review (HITL) → resolve forward or decline
```

---

## 2. Core Infrastructure

| Component | Technology | Purpose |
|---|---|---|
| Workflow engine | Temporal | Durable, resumable origination workflow; human-wait via signals |
| API | FastAPI + SQLAlchemy | HTTP surface for both applicant and underwriter frontends |
| Database | PostgreSQL | Application state, KYC record, audit log |
| Frontend | React + Vite + TypeScript + Tailwind | Applicant journey + underwriter console, served via nginx |
| LLM | Google Gemini (flash / flash-lite) | Onboarding copilot, OCR extraction |
| Container | Docker Compose | Single `docker compose up --build` brings up the whole stack |

---

## 3. Origination Pipeline

Six workflow states, each backed by a Temporal activity:

1. **Lead qualification** — eligibility gate (age, employment type, income). Hard knockouts are non-overridable; soft failures escalate to `LEAD_EXCEPTION` for human review.
2. **KYC & Document Intelligence** — extracts fields from 4 documents (Aadhaar card, PAN card, salary slips, Form 16), cross-checks them, and computes grounded per-field confidence. Failures park at `KYC_EXCEPTION`.
3. **Underwriting** — pulls a credit bureau report, runs the scorecard, evaluates DTI and risk band. Soft-policy breaches escalate to `REFERRED`; borderline cases go to `UW_EXCEPTION`.
4. **Decision** — deterministic rules engine emits `approve` / `decline` / `refer`. The LLM never touches this path.
5. **Offer generation** — computes EMI, processing fee, GST, net disbursal. Terms are from reviewed templates + versioned policy, not LLM-generated.
6. **E-sign** — mock e-sign harness; `OFFER_GENERATED → OFFER_ACCEPTED` transition.

---

## 4. Document Intelligence & OCR

**Hybrid extractor**: if a real file was uploaded (bytes present in the document store), the LLM extracts it; if it's a mock/demo upload, a reflective mock echoes the applicant's own data. Real OCR is the default — no env toggle needed.

**Grounded confidence** (no LLM self-report):
```
ocr_confidence = self_consistency(N=3 samples) × provenance(source_quote present in doc)
field_confidence = ocr_conf × cross_source_agreement × validator_pass_ratio
```

**Cross-source checks**: every field reported by ≥ 2 documents is compared pairwise with type-aware matching (name token order, date format normalisation, income within ±10%, ID normalised case/spaces). A mismatch on any key field → `KYC_EXCEPTION`.

**Claimed-vs-documented**: the applicant's self-reported PAN / Aadhaar / name / DOB are cross-checked against what the documents actually show. A disagreement is a fraud signal — it drops that field's confidence and routes to human review. The applicant's claimed values are never overwritten by the documents; both are stored and shown side-by-side to the underwriter.

**Key fields** (version-pinned in policy): `name`, `date_of_birth`, `pan`, `aadhaar`, `gross_monthly_income`.

---

## 5. Rules Engine & Scoring

- **Versioned policy** (`RULES_POLICY`, `SCORECARD_POLICY`, `PRICING_POLICY`, `CONFIDENCE_POLICY`). All thresholds live in `policy.py` — not hardcoded in business logic.
- **Hard knockouts** (`is_knockout=True`): auto-DECLINED, not overridable by any human. Examples: `UNDERAGE`, `NOT_SALARIED`, `LOW_CIBIL`.
- **Soft policy** (`is_knockout=False`): escalate to `REFER`; an underwriter can approve with a bounded reason code.
- **DTI** (debt-to-income): post-loan DTI = `(existing_obligations + new_EMI) / monthly_income`. Canonical formula shared by the rules engine and the underwriting summary.
- **Risk bands**: PRIME / NEAR_PRIME / SUBPRIME / DECLINE, each with an interest rate and max loan cap.

---

## 6. Human-in-the-Loop (HITL)

Three parking states, each with a specific resolution path:

| State | Cause | Resolution options |
|---|---|---|
| `KYC_EXCEPTION` | Low doc confidence or claimed-vs-doc mismatch | Mark verified → `KYC_VERIFIED`, or reject → `DECLINED` (DOC_NOT_GENUINE) |
| `UW_EXCEPTION` | Cannot underwrite from current data | Re-run assessment → `UNDERWRITING`, or reject → `DECLINED` (CANNOT_UNDERWRITE) |
| `REFERRED` | Soft-policy breach, borderline score | Approve → `DECISION_READY`, or Decline → `DECLINED` |

Every override requires a **mandatory typed justification** recorded in the audit trail. The binding reason stays a structured code — not free text — so it's auditable and consistent. Hard knockouts cannot be overridden by any path.

---

## 7. Underwriter Console

- **Pipeline view**: all applications with status and last-updated time.
- **Application detail**: workflow state graph, decision, underwriting summary, full audit trail.
- **Review & resolve screen**: full collected-info view before approve/decline — applicant's claim vs. what documents extracted (mismatches flagged in red), KYC field confidence with risk flags, underwriting summary with DTI, engine decision, and the resolve panel with mandatory justification.
- **Policy view**: live rendering of all rules (hard/soft badges), risk bands, pricing, and document thresholds — so the underwriter understands the engine's logic without reading code.
- **Document viewer**: underwriters can open any uploaded document (image or PDF) inline from the review screen.

---

## 8. Applicant Journey

- **Onboarding copilot**: multi-turn chat (Gemini) that collects all required fields conversationally. Completeness-gated — the workflow won't start until all fields + documents are present.
- **Form-fill alternative**: one-page form with all fields at once; real file upload for each document (Aadhaar card, PAN card, salary slips, Form 16).
- **Consent**: two-layer capture — Layer-1 authorises bureau pull; Layer-2 authorises AI document processing (DPDP-aligned). Both are audited.
- **Live status page**: after submission the applicant sees a real-time stage stepper that updates as each workflow step completes. Declined applications show an adverse-action explanation. Parked applications show what's pending.

---

## 9. Audit & Explainability

- Every action produces an immutable audit event: state transitions, agent reasoning (with cross-checks and confidence scores), bureau pulls, consent captures, HITL overrides.
- Adverse-action explanations use **reviewed templates** (not LLM-generated text), with code-inserted numbers. A faithfulness check verifies the rendered text covers exactly the fired reason codes and no others.
- Human-override reason codes (`DOC_NOT_GENUINE`, `CANNOT_UNDERWRITE`) have their own adverse-action templates so declined applications always have a readable explanation.

---

## 10. Demo & Testing

**Demo scenario selector** (no rebuild needed): tag an application with a scenario at creation and the mock bureau + OCR honour it.

| Scenario | Path triggered |
|---|---|
| `clean` | Happy path → Offer |
| `high_dti` | Soft-policy breach → REFERRED |
| `low_cibil` | Hard knockout → DECLINED |
| `thin_file` | Thin bureau file → UW_EXCEPTION |
| `doc_mismatch` | Name mismatch across docs → KYC_EXCEPTION |
| `lead_review` | Uncertain lead → LEAD_EXCEPTION |

**Test coverage**: 369 tests (unit + integration), covering the rules engine, confidence service, document intelligence, workflow activities, OCR adapter, storage, and API endpoints. Tests run against an in-process SQLite + Temporal test server — no external services needed.

---

## 11. Key Design Guardrails

| Principle | How it's enforced |
|---|---|
| LLM never makes the credit decision | Deterministic code gates every `approve` / `decline`; LLM output is never on the binding path |
| Grounded confidence only | Confidence = observable signals (OCR × agreement × validators). LLM self-reported confidence is never used |
| Versioned policy | All thresholds in `policy.py`; version string threads through every agent and is recorded in audit |
| Bounded overrides | Reason codes are an enum; free-text justification is audit-only, never the binding reason |
| Hard knockouts non-overridable | `is_knockout=True` rules auto-DECLINE with no resolution path available in the UI |
| PII handling | Documents stored on a shared encrypted volume; Aadhaar masked in logs; AI processing requires explicit Layer-2 consent |
