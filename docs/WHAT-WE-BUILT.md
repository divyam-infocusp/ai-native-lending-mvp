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

## 2. Modules at a Glance

A recap of what each important module does — the internal flow, what it outputs,
and (where relevant) how scores are computed. The guiding decision throughout:
**LLMs perceive; deterministic code decides.** Only three modules call an LLM; the
entire binding credit path is plain, versioned arithmetic.

### AI / agentic layer (`agents/`) — LLM-backed (perceive, never decide)

**Onboarding Copilot** (`onboarding.py`) — Gemini-lite, multi-turn.
Flow: greet → on each turn the model extracts any fields the applicant actually
stated (typed schema → structured output) and writes the next question, grouping
2–4 still-missing items. Extracted values are routed to the applicant record /
features; **completeness is decided deterministically** (all required fields present
+ all documents uploaded), never from the model's say-so. Conversation memory is
durable (LangGraph checkpointer keyed by `application_id`), so the applicant can
leave and resume. Output: the assistant message + collected/missing; when complete
the journey moves to consent. Now fronted by the early lead-intent gate (below).

**Lead Qualification** (`lead_qualification.py`) — Gemini-lite, segment triage.
Flow: classify the inquiry into `segment_fit` ∈ {in_segment, out_of_segment,
uncertain} with a stable `reason_code` and a model confidence. Guardrails: strict
schema (reject + retry on malformed output) and **escalate-on-uncertainty** —
`uncertain`, or confidence < 0.70, routes to human review rather than an arbitrary
auto-decline. Output: `qualified` / `declined_early` / `manual_review` →
`LEAD_QUALIFIED` / `LEAD_DECLINED` / `LEAD_EXCEPTION`. Runs in two places: the early
in-chat gate (a UX short-circuit that stops spam / not-a-loan before any PII is
collected) and the authoritative post-submission workflow step. Key decision: it
**never rejects on employment type or credit history** — those are assessed
deterministically downstream.

**Document Intelligence** (`document_intelligence.py` + `doc_compare.py`) — the
verify-and-gate pipeline (the LLM is used only inside the OCR adapter). Flow, all
deterministic with no LLM self-report: (1) **extract** each uploaded document via
the OCR adapter — Gemini Vision on real files — every field carrying an OCR
confidence; (2) **cross-check** every field reported by ≥2 sources, pairwise and
type-aware (id / name / money / date), folding in the applicant's *claimed*
identity and income as an extra source; (3) **validate** format/checksum (PAN
regex, Aadhaar 12-digit); (4) **score** grounded per-field confidence (see
Confidence Service); (5) **gate** — any key field missing/unreliable, or any
cross-source mismatch on a key field → `KYC_EXCEPTION`. Output: a verified profile
+ per-field confidence + risk flags, with claim-vs-document kept side by side for
the underwriter.

*Extracted fields* (`FIELD_SCHEMAS`, scoped per document so the model pulls only what each carries):

| Document | Fields |
|---|---|
| Aadhaar card | `name`, `date_of_birth`, `aadhaar`, `address` |
| PAN card | `name`, `date_of_birth`, `pan` |
| Salary slips | `name`, `employer_name`, `gross_monthly_income`, `net_monthly_income` |
| Form 16 | `name`, `pan`, `employer_name`, `gross_monthly_income` |

Per-field OCR confidence is itself grounded — `self_consistency (N=3 samples) × provenance (source quote present in the doc)` — never the model's self-report. **Key fields** that must each be reliable *and* agree across sources (or it's a `KYC_EXCEPTION`): `name`, `date_of_birth`, `pan`, `aadhaar`, `gross_monthly_income`. Cross-source matching is type-aware: income within ±10%, IDs normalised for case/spaces, names token-order-insensitive, dates format-normalised.

> **Underwriting** and **Decision QA** also live in `agents/` but use **no LLM** —
> they are deterministic orchestrators, described in the decision core below.

### Deterministic decision core — the binding credit path (no LLM)

**Underwriting** (`underwriting.py`). Flow: enforce bureau-pull consent → idempotent
hard bureau inquiry → assemble engine features (bureau + KYC/onboarding data) → run
a **read-only** preview of the rules engine + scorecard. It does **not** decide.
Output: a cashflow / explainability summary — post-loan DTI, bureau score, internal
score + band, disposition hint, fired reason codes, and the pinned `version_set` —
or routes to `UW_EXCEPTION` if required data is missing.

**Rules Engine** (`rules_engine/`). Flow: evaluate ordered policy rules against the
features. Output: fired `policy_hits` (reason codes) + a disposition hint
(DECLINE / ESCALATE / CONTINUE). How: **hard knockouts** (`is_knockout=True`, e.g.
UNDERAGE, NOT_SALARIED, LOW_CIBIL) fire first → DECLINE, non-overridable; **soft
policy** (e.g. HIGH_DTI) → ESCALATE → REFER. Post-loan **DTI** =
`(existing_obligations + new_EMI) / monthly_income`, the same number surfaced in the
summary and judged by the HIGH_DTI rule.

**Scorecard** (`scorecard/`). Flow: extract four features, bin each into points, sum
the weighted points, map the total to a band. Output: `score` + `RiskBand`
(A/B/C/D, or X = not lendable). How: features are **CIBIL, monthly income, DTI,
employment tenure**; each value falls into a policy-defined bin worth fixed points;
`total = Σ(points × weight)` (all weights 1.0, max 110). Below `min_score` (30) →
band X; otherwise the first threshold cleared sets the band (≥90 A/PRIME, ≥70
B/NEAR_PRIME, ≥50 C/SUBPRIME, ≥30 D/DECLINE — each band carries its own interest
rate and max-loan cap in `PRICING_POLICY`). It also re-scores at a 10% income
haircut and flags band/lendability flips as income-sensitive.

**Pricing** (`pricing/`). Flow: look up the band's rate, clamp tenure to policy
bounds, cap the amount for affordability, compute the EMI. Output:
`{rate, amount, tenure, emi}`. How: **EMI** via standard reducing-balance
amortization `P·r·(1+r)^n / ((1+r)^n − 1)`; **affordability cap** = the present
value of the EMI headroom (`max_dti·income − existing_obligations`) over the tenure,
so `amount = min(requested, band ceiling, affordability cap)` — we never offer a
loan the applicant can't service.

**Decision assembly** (`decision/assembly.py`). Flow: compose rules + scorecard +
income sensitivity into the **decision-of-record** (pure function), then persist +
audit it. Output: a `Decision` with disposition, reason codes, score/band, a
rendered adverse-action explanation, and the pinned `version_set` (reproducible).
How (outcome policy): hard knockout or band X → DECLINE; soft-policy escalate or
income-sensitive → REFER; otherwise APPROVE. A human soft-override is recorded as a
*new* decision-of-record (`source = underwriter:<id>`) preserving the engine's
pinned versions; the original stays in the audit trail.

**Decision QA + offer delivery** (`decision_qa.py`). Flow: QA-check the
decision-of-record against invariants → for an APPROVE, price the offer, assemble
the sanction-style **offer letter**, persist it, send a notification, and route to
e-sign. Output: a delivered offer letter (amount, rate, tenure, EMI, processing fee,
GST, net disbursal, totals, validity, terms) or a `blocked` result. How (QA
invariant): every non-approve decision **must** carry reason codes + an
adverse-action explanation — this enforces the "100% of decisions carry reasons"
guarantee. (It does **not** answer applicant questions — offer text is reviewed
templates + policy values, never LLM-authored.)

### Trust, compliance & explainability

**Confidence Service** (`confidence/`). Output: a per-field composite confidence
[0,1], risk flags, and an `is_reliable` verdict. How: three grounded signals
multiplied — `composite = ocr_conf × cross_source_agreement_ratio ×
validator_pass_ratio`. Flags fire for LOW_OCR (ocr below min), CROSS_SOURCE_MISMATCH,
FORMAT_INVALID, and CONFIDENCE_BELOW_THRESHOLD; `is_reliable` requires composite ≥
threshold (0.70) **and** no FORMAT_INVALID. Confidence is never the LLM's
self-report — only observable signals. Also runs payslip obvious-fake checks.

**Adverse-Action Renderer** (`explanation/`). Output: human-readable decline/refer
text built from **reviewed templates** with code-inserted policy numbers — never
free-form LLM text. How: a faithfulness check verifies the rendered text covers
exactly the fired reason codes and no others; human-override codes (DOC_NOT_GENUINE,
CANNOT_UNDERWRITE) have their own templates.

**Audit store** (`audit/`). Output: an append-only event trail — every state
transition, agent reasoning (with cross-checks + confidence), bureau pull, consent
capture, and HITL override — which is also what `reconstruct_decision` replays to
rebuild the issued decision.

**Consent gate** (`consent.py`). Output: audited consent records enforced before the
data they authorise is used. How: two layers — Layer-1 authorises the bureau pull,
Layer-2 authorises AI document processing; a missing/withdrawn/wrong-purpose consent
is treated as a data gap (DPDP-aligned).

**Governance / versioning** (`governance/`, `policy.py`). Output: an active
`version_set` pinning the rules / scorecard / pricing / confidence policy versions,
stamped onto every decision so it is reproducible and auditable. All thresholds live
in `policy.py`, not in business logic.

---

## 3. Core Infrastructure

| Component | Technology | Purpose |
|---|---|---|
| Workflow engine | Temporal | Durable, resumable origination workflow; human-wait via signals |
| API | FastAPI + SQLAlchemy | HTTP surface for both applicant and underwriter frontends |
| Database | PostgreSQL | Application state, KYC record, audit log |
| Frontend | React + Vite + TypeScript + Tailwind | Applicant journey + underwriter console, served via nginx |
| LLM | Google Gemini (flash / flash-lite) | Onboarding copilot, OCR extraction |
| Container | Docker Compose | Single `docker compose up --build` brings up the whole stack |

---

## 4. Human-in-the-Loop (HITL)

Three parking states, each with a specific resolution path:

| State | Cause | Resolution options |
|---|---|---|
| `KYC_EXCEPTION` | Low doc confidence or claimed-vs-doc mismatch | Mark verified → `KYC_VERIFIED`, or reject → `DECLINED` (DOC_NOT_GENUINE) |
| `UW_EXCEPTION` | Cannot underwrite from current data | Re-run assessment → `UNDERWRITING`, or reject → `DECLINED` (CANNOT_UNDERWRITE) |
| `REFERRED` | Soft-policy breach, borderline score | Approve → `DECISION_READY`, or Decline → `DECLINED` |

Every override requires a **mandatory typed justification** recorded in the audit trail. The binding reason stays a structured code — not free text — so it's auditable and consistent. Hard knockouts cannot be overridden by any path.

---

## 5. Underwriter Console

- **Pipeline view**: all applications with status and last-updated time.
- **Application detail**: workflow state graph, decision, underwriting summary, full audit trail.
- **Review & resolve screen**: full collected-info view before approve/decline — applicant's claim vs. what documents extracted (mismatches flagged in red), KYC field confidence with risk flags, underwriting summary with DTI, engine decision, and the resolve panel with mandatory justification.
- **Policy view**: live rendering of all rules (hard/soft badges), risk bands, pricing, and document thresholds — so the underwriter understands the engine's logic without reading code.
- **Document viewer**: underwriters can open any uploaded document (image or PDF) inline from the review screen.

---

## 6. Applicant Journey

- **Onboarding copilot**: multi-turn chat (Gemini) that collects all required fields conversationally. Completeness-gated — the workflow won't start until all fields + documents are present.
- **Form-fill alternative**: one-page form with all fields at once; real file upload for each document (Aadhaar card, PAN card, salary slips, Form 16).
- **Consent**: two-layer capture — Layer-1 authorises bureau pull; Layer-2 authorises AI document processing (DPDP-aligned). Both are audited.
- **Live status page**: after submission the applicant sees a real-time stage stepper that updates as each workflow step completes. Declined applications show an adverse-action explanation. Parked applications show what's pending.

---

## 7. Demo & Testing

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

## 8. Key Design Guardrails

| Principle | How it's enforced |
|---|---|
| LLM never makes the credit decision | Deterministic code gates every `approve` / `decline`; LLM output is never on the binding path |
| Grounded confidence only | Confidence = observable signals (OCR × agreement × validators). LLM self-reported confidence is never used |
| Versioned policy | All thresholds in `policy.py`; version string threads through every agent and is recorded in audit |
| Bounded overrides | Reason codes are an enum; free-text justification is audit-only, never the binding reason |
| Hard knockouts non-overridable | `is_knockout=True` rules auto-DECLINE with no resolution path available in the UI |
| PII handling | Documents stored on a shared encrypted volume; Aadhaar masked in logs; AI processing requires explicit Layer-2 consent |
