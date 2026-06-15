# AI-Native Lending Platform — MVP PRD

**Status:** Draft for build planning
**Source:** `AI-Native-Lending-MVP-Design-Doc.md` (v0.2, incl. §16 Design Review Decisions)
**Version:** 0.1
**Test scope (agreed):** automated tests for the **deterministic decision core** only (see *Testing Decisions*).

> This PRD translates the design doc into buildable work. It decomposes the system into **deep modules** (rich logic behind a small, stable, testable interface) and a set of **small, independently-grabbable build components** with interfaces, dependencies, and acceptance criteria. Where the design doc's §16 decisions apply, they are cited inline (e.g. `[16.1]`) and take precedence over the v0.1 body of the design doc.

---

## Problem Statement

A lender wants to take a personal-loan applicant from first contact to a priced, explainable credit decision **in under a couple of minutes, with no human touch on clean cases** — while still being able to defend every decision to a regulator. Today this requires manual document checks, manual bureau/bank review, and manually written decline reasons, which is slow, inconsistent, and hard to audit. Naively automating it with an LLM creates the opposite problem: a fast but un-auditable, non-compliant decision nobody can stand behind.

The applicant experiences slow, opaque decisions. The operations reviewer is buried in cases that don't actually need judgment. The credit/risk owner can't prove *why* a given decision was made. Compliance can't reconstruct a decision after the fact.

## Solution

An **agentic origination pipeline** that runs the five-stage flow (Acquisition → Onboarding → KYC → Credit Assessment → Approval & Offer) end-to-end for a clean applicant, and **degrades gracefully to a human queue** for everything else. The load-bearing rule: **the binding credit decision is always produced by a deterministic rules + scorecard engine, never by an LLM** `[2.1]`. AI agents orchestrate data pulls, extract and verify documents, interpret results, and *render* (never author) explanations and adverse-action reasons `[16.1]`. Low-confidence work escalates to a unified exceptions queue with full context.

From the user's perspective:
- **Applicant:** apply, upload docs, consent once, and get an instant, explained decision and a priced offer to e-sign — or a clear, human-reviewed outcome.
- **Ops reviewer:** see only cases that genuinely need judgment, with the agent's reasoning, the specific low-confidence items, and bounded, audited resolution actions.
- **Credit/risk owner:** own the rules, scorecard, pricing, and reason codes as **versioned config**, with every decision reproducible from the versions that produced it.
- **Compliance/auditor:** reconstruct any decision — inputs, tool calls, model/prompt versions, rules fired, agent reasoning, human actions — from an append-only audit stream.

---

## User Stories

### Applicant
1. As an applicant, I want to give consent once for the lender to access my data, so that I don't have to re-authorize at every step `[16.6]`.
2. As an applicant, I want my application form pre-filled from what I've already provided, so that I spend less time on data entry.
3. As an applicant, I want a checklist of exactly which documents to upload, so that I don't get rejected for a missing document.
4. As an applicant, I want guidance in my preferred language while filling the form, so that I understand what's being asked `[16.11]`.
5. As an applicant, I want to be told immediately if I'm not eligible for this product, so that I don't waste time on an application that can't succeed `[16.7]`.
6. As an applicant, I want an instant decision on a clean application, so that I know where I stand in under a couple of minutes.
7. As an applicant, I want a clear, human-readable explanation if I'm declined, so that I understand the principal reasons `[16.1]`.
8. As an applicant, I want any decline reason and offer terms presented faithfully in my language, so that I'm not misled by a bad machine translation `[16.11]`.
9. As an applicant, I want a personalized offer (amount, rate, tenure, EMI) I can e-sign, so that I can accept on the spot.
10. As an applicant, I want my offer to clearly state when it expires, so that I know how long I have to accept.
11. As an applicant whose case needs a closer look, I want it routed to a human rather than wrongly auto-declined, so that I get a fair review.

### Operations reviewer
12. As a reviewer, I want a single queue of all exception cases (KYC, underwriting, referral), so that I have one place to work `[8]`.
13. As a reviewer, I want each parked case to show the agent's reasoning and the specific low-confidence items, so that I can resolve it quickly.
14. As a reviewer, I want to see the source documents and extracted fields side by side, so that I can verify a flagged field.
15. As a reviewer, I want only bounded, pre-defined resolution actions with structured reason codes, so that my decision stays within policy `[16.10]`.
16. As a reviewer, I want hard knockouts (e.g. sanctions hits) to be non-overridable at the console, so that I can't accidentally approve a prohibited case `[16.10]`.
17. As a reviewer, I want my resolution to resume the application automatically, so that I don't manage workflow state by hand.
18. As a reviewer, I want my override recorded as the decision of record with my identity and reason, so that the decision is attributable `[16.10]`.

### Credit / risk owner
19. As the risk owner, I want the rules tables, scorecard weights, pricing policy, and reason codes as versioned config, so that I own the decision logic without engineering changes `[6.4–6.6]`.
20. As the risk owner, I want every decision to record which config versions produced it, so that it's reproducible `[9.4]`.
21. As the risk owner, I want adverse-action reasons derived deterministically from fired policy hits, so that the explanation can't drift from the decision `[16.1]`.
22. As the risk owner, I want an income-haircut sensitivity test to flag income-sensitive cases for referral, so that decisions don't rest on unverified income `[16.8]`.
23. As the risk owner, I want a golden eval set with expected dispositions and a documented input mix, so that I can measure automation and routing honestly `[16.2]`.
24. As the risk owner, I want the false-automation rate (should-escalate-but-auto-decided) reported as a release gate, so that unsafe auto-decisions block releases `[16.2]`.

### Compliance / auditor
25. As an auditor, I want an append-only record of every input, tool call, model/prompt version, rule fired, and human action per application, so that any decision is reconstructable `[9.1]`.
26. As an auditor, I want consent checked (purpose + status, plus a fresh per-pull artifact) before every data pull, so that no pull happens without valid consent `[16.6]`.
27. As an auditor, I want 100% of decisions to carry a complete, faithful explanation and adverse-action reasons, so that the system meets fair-lending obligations `[1.4, 16.1]`.

### Engineer / operator
28. As an engineer, I want each integration behind a swappable adapter with a mock mode, so that the pipeline runs end-to-end before real providers are onboarded `[6.3]`.
29. As an engineer, I want every external call to carry an idempotency key, so that a retry never repeats a paid bureau hard inquiry `[16.5]`.
30. As an engineer, I want long-running and human-wait steps modeled durably, so that a crash never loses application progress `[6.1]`.
31. As an engineer, I want each agent constrained to a fixed tool set and a strict output schema with reject-and-retry, so that an agent can't emit free text where a field is expected `[2.3]`.
32. As an engineer, I want confidence computed from grounded signals (OCR score, cross-source agreement, validators), so that the escalation gate isn't driven by LLM self-report `[16.4]`.
33. As an engineer, I want distributed tracing and a KPI dashboard (incl. the confusion matrix), so that I can observe automation rate, routing correctness, and latency `[9.5, 16.2]`.
34. As an operator, I want the time-to-decision SLA measured from data-present-to-decision, with data-acquisition latency tracked separately, so that the metric reflects what we control `[16.3]`.

---

## Implementation Decisions

### Architecture
- **Four planes** retained from the design doc: workflow backbone, agent layer, integration & model services, cross-cutting. `[3]`
- **Two orchestration engines with a pinned boundary** `[16.5]`: Temporal owns the macro spine (state, human-wait signals, retries, timeouts); LangGraph owns the per-agent micro loop with a checkpointer. The four rules: (1) shared keyed Postgres checkpoint, (2) retry = reload (resume from last completed node, never restart from top), (3) **idempotency on every external call** (`application_id + provider + purpose`), (4) the LOS record is the sole source of truth for application *state*.
- **Deterministic decision core is LLM-free** `[2.1]`. Agents call the rules/scorecard engine **read-only**. The decision of record is `rules + scorecard` output, or a tiered human override `[16.10]`.
- **Segment is narrowed** to credit-tested, salaried applicants with a CIBIL record `[16.7]`. Thin-file → `UW_EXCEPTION`. AA + cashflow-led underwriting is the first fast-follow, **out of MVP scope**.

### Module catalog (deep modules + their interfaces)

> Interfaces are described as behavior, not code. "Pure" = deterministic, no I/O, isolation-testable.

**A. Deterministic decision core (pure)**
- **Rules Engine** — `evaluate(features, rules_version) → { rule_results[], policy_hits[] (each with reason_code), disposition_hint }`. Config-driven decision tables: hard knockouts → policy checks → band assignment. Versioned. `[6.4]`
- **Scorecard** — `score(features, scorecard_version) → { score, band }`. Single-mode, bureau-led, expert/heuristic. ML-swappable behind this interface. `[6.5, 16.7]`
- **Pricing Engine** — `price(band, policy, pricing_version) → { rate, amount, tenure, emi }` and `income_sensitivity(features, haircut_pct) → { sensitive: bool }` (re-runs scorecard+pricing with income discounted; flips outcome/band ⇒ sensitive ⇒ refer). Versioned. `[6.6, 16.8]`
- **Adverse-Action / Explanation Renderer** — `render(reason_codes[], language) → { text }` from human-reviewed templates keyed by `reason_code × language`, numbers code-inserted; plus `verify_faithful(reason_codes[], text) → bool` asserting the output covers exactly the fired set. `[16.1, 16.11]`

**B. Cross-cutting deep modules**
- **Consent Manager** — `gate(application_id, pull_purpose) → allow | block` checking Layer-1 customer authorization (purpose + `active` status, no timer) AND a fresh Layer-2 per-pull artifact minted at pull time. `[16.6]`
- **Confidence Service** — `field_confidence(ocr_conf, cross_source_checks, validators) → { confidence, risk_flags[] }`. Grounded only; includes payslip obvious-fake checks (arithmetic, cross-doc, plausibility, tamper flag, hash reuse). `[16.4, 16.8]`
- **Audit & Explainability Store** — `append(application_id, event)` (append-only) and `reconstruct(application_id) → decision_trail`. `[9.1]`
- **Governance/Versioning** — pins and records model IDs, prompt versions, and rules/scorecard/pricing config versions on every decision. `[9.4]`

**C. Orchestration & runtime**
- **Workflow Orchestrator (Temporal)** — drives the state machine `[4]`; human reviews modeled as signals; durable retries/timeouts.
- **LOS / Application Aggregate Store** — Postgres system-of-record for the application aggregate + state; S3 for documents/artifacts. `[6.1]`
- **Agent Runtime** — common LangGraph scaffold enforcing the §7 five-part contract: fixed tools, strict schema (reject + retry), grounded confidence gate, escalation path, checkpointer with retry=reload. `[6.2, 16.5]`

**D. Integration adapters** (common interface, mock mode, idempotency key): OCR/IDP; KYC + Sanctions/PEP; Credit Bureau; e-Sign; Notifications. *(Account Aggregator adapter is stubbed/deferred — see Out of Scope.)* `[6.3, 16.5]`

**E. Agents** (thin configs over the Agent Runtime): Lead Qualification (Step 2, enforces the segment gate `[16.7]`); Onboarding Copilot (Step 3); Document Intelligence (Step 4 → `KYC_EXCEPTION`); Underwriting (Step 5, read-only rules/scorecard → `UW_EXCEPTION`); Decision QA (Step 6, renders explanation/adverse-action, prices, e-sign, underwriter copilot on referrals → `REFERRED`). `[7]`

**F. Surfaces**: Ops Console (exceptions queue, tiered overrides, structured reason codes `[16.10]`); Applicant Web App.

**G. Quality**: Eval Harness (golden sets, expected dispositions, calibration, confusion-matrix gate `[16.2, 16.4]`); Observability (tracing + KPI dashboards `[9.5]`).

### Schema — application aggregate (key changes from §5)
- `consent` split into **Layer 1** `{ purpose, status: active|withdrawn, artifact_id, granted_ts }` (no timer) and **Layer 2** per-pull artifacts minted at pull time (own validity), both logged to audit. `[16.6]`
- `kyc.field_confidence` annotated as a **derived** grounded signal, with `risk_flags[]`. `[16.4]`
- `decision` adds `source: engine | underwriter:<id>` and preserves the engine's original output alongside any override; `adverse_action_reasons` are **reason codes** + rendered text. `[16.1, 16.10]`
- `decision.engine_version` expands to the full pinned version set (model IDs, prompts, rules, scorecard, pricing). `[9.4]`

### API surface (illustrative) `[12]`
`POST /applications` · `POST /applications/{id}/documents` · `POST /applications/{id}/consent` · `GET /applications/{id}` · `GET /applications/{id}/explanation` · `GET /ops/queue` · `POST /ops/applications/{id}/resolve` (→ workflow signal).

### State machine `[4]`
`LEAD → LEAD_QUALIFIED → APPLICATION_SUBMITTED → KYC_IN_PROGRESS → {KYC_VERIFIED | KYC_EXCEPTION} → UNDERWRITING → {DECISION_READY | UW_EXCEPTION} → {APPROVED | DECLINED | REFERRED} → OFFER_GENERATED → {OFFER_ACCEPTED | OFFER_EXPIRED}`. Exceptions park durably and resume on a human signal.

---

## Modular Build Components

Small, independently-grabbable units. Each: **what**, **depends on**, **acceptance criteria**. Grouped by epic; epics roughly follow the design doc's risk-ordered phasing `[11]` (spine → high-risk AI → copilots).

### Epic 0 — Foundations
- **C0.1 LOS aggregate + state store.** Persist the application aggregate (post-§16 schema) in Postgres. *Deps:* none. *Accept:* a dummy application persists and reads back with the full schema incl. `decision.source` and two-layer consent.
- **C0.2 Audit & Explainability Store.** Append-only event stream + reconstruct. *Deps:* C0.1. *Accept:* every write is immutable; `reconstruct(id)` returns the ordered trail.
- **C0.3 Temporal skeleton + state machine.** Encode states/transitions; human-wait via signals. *Deps:* C0.1. *Accept:* a dummy app advances through states and emits an audited transition per step; an exception parks and a signal resumes it.
- **C0.4 Adapter interface + mock mode + idempotency.** Common adapter contract; mock implementations; idempotency key enforcement. *Deps:* none. *Accept:* a repeated call with the same key does not double-execute; mock mode returns canned data.
- **C0.5 Governance/versioning scaffold.** Pin and stamp config/model/prompt versions onto decisions. *Deps:* C0.1. *Accept:* a decision record carries the full version set.
- **C0.6 Observability scaffold.** Tracing + KPI metric plumbing. *Deps:* C0.3. *Accept:* a trace spans an app end-to-end; metric counters emit.

### Epic 1 — Deterministic decision core (the compliance spine; **tested in isolation**)
- **C1.1 Rules Engine.** Config-driven decision tables → `policy_hits` with reason codes. *Deps:* none (pure). *Accept:* given fixtures, returns correct hits/disposition; versioned; golden tests pass.
- **C1.2 Scorecard.** Bureau-led expert scorecard → score + band. *Deps:* none (pure). *Accept:* deterministic score/band on fixtures; versioned.
- **C1.3 Pricing Engine + income-haircut test.** Band/policy → terms; sensitivity flag. *Deps:* C1.2. *Accept:* terms correct on fixtures; haircut that flips band marks case sensitive → refer.
- **C1.4 Adverse-Action / Explanation Renderer + faithfulness check.** Reason codes (× language) → templated prose; verify coverage. *Deps:* C1.1. *Accept:* rendered text covers exactly the fired code set; mismatch is rejected; per-language template fills numbers via code.
- **C1.5 Decision assembly.** Compose engine output → decision-of-record record (with version stamp + audit append). *Deps:* C1.1–C1.4, C0.2, C0.5. *Accept:* a decision is fully reconstructable.

### Epic 2 — Backbone happy path
- **C2.1 Applicant intake API + consent capture (Layer 1).** *Deps:* C0.1, C0.4. *Accept:* application + Layer-1 authorization persisted with purpose/status.
- **C2.2 Consent Manager gate.** Two-layer check before any pull. *Deps:* C2.1, C0.4. *Accept:* blocks on missing/withdrawn/wrong-purpose Layer 1 or stale Layer 2; mints Layer 2 at pull time.
- **C2.3 Hard-coded happy path to OFFER_GENERATED.** Wire submitted → (stub decision) → offer letter. *Deps:* C0.3, C1.5. *Accept:* a clean dummy app reaches `OFFER_GENERATED`.
- **C2.4 Ops Console + exceptions queue (tiered overrides).** Queue, case view, bounded actions, structured reason codes, signal back. *Deps:* C0.3. *Accept:* an exception parks, a reviewer resolves with a reason code, the app resumes; hard knockouts are non-overridable; override recorded as `decision.source`.

### Epic 3 — Document Intelligence + KYC (highest-risk AI)
- **C3.1 Agent Runtime.** LangGraph scaffold (fixed tools, schema reject/retry, confidence gate, escalation, checkpointer, retry=reload). *Deps:* C0.3, C0.4. *Accept:* a schema-violating output is rejected/retried; a crash mid-loop resumes from the last node without repeating an external call.
- **C3.2 Confidence Service.** Grounded confidence + payslip obvious-fake checks. *Deps:* C0.4. *Accept:* confidence derives from OCR + cross-source + validators; obvious fakes raise risk flags.
- **C3.3 OCR/IDP + KYC/Sanctions adapters (mock + real-ready).** *Deps:* C0.4. *Accept:* returns fields + per-field confidence; screening returns hits.
- **C3.4 Document Intelligence Agent.** Extract/verify → verified profile + confidence; low confidence → `KYC_EXCEPTION`. *Deps:* C3.1–C3.3. *Accept:* on golden set, extraction precision ≥ 95% on key fields; low-confidence routes to queue.

### Epic 4 — Underwriting
- **C4.1 Credit Bureau adapter (idempotent hard inquiry).** *Deps:* C0.4. *Accept:* repeated pull with same key does not re-inquire.
- **C4.2 Underwriting Agent.** Assemble features, call rules/scorecard **read-only**, produce cashflow/explainability summary; thin file/data gap → `UW_EXCEPTION`. *Deps:* C3.1, C1.1–C1.2, C4.1. *Accept:* decision-of-record reproducible; thin-file escalates.

### Epic 5 — Decisioning + Offer
- **C5.1 Decision QA Agent.** QA decision, render explanation + adverse-action `[16.1]`, price, generate offer; borderline/policy hit → `REFERRED`. *Deps:* C1.3–C1.5, C3.1. *Accept:* clean case runs to offer < 90s (data-present-to-decision); 100% have adverse-action reasons.
- **C5.2 e-Sign + Notifications adapters.** *Deps:* C0.4. *Accept:* offer sent; e-sign transitions `OFFER_GENERATED → OFFER_ACCEPTED`; timeout → `OFFER_EXPIRED`.

### Epic 6 — Acquisition + Onboarding copilots
- **C6.1 Lead Qualification Agent (segment gate).** Eligibility pre-check enforces the narrowed segment; ineligible → decline-early/nurture. *Deps:* C3.1. *Accept:* out-of-segment applicants filtered at Step 2, not downstream `[16.7]`.
- **C6.2 Onboarding Copilot.** Autofill, checklist, multilingual conversational assist (binding text stays templated). *Deps:* C3.1. *Accept:* complete submitted application; legal text uses reviewed templates `[16.11]`.

### Epic 7 — Quality gates (cross-cutting)
- **C7.1 Eval Harness.** Golden sets with expected dispositions; documented input mix; confusion matrix; calibration report. *Deps:* C1.x, C3.4, C4.2, C5.1. *Accept:* false-automation rate computed and gates release `[16.2]`.
- **C7.2 KPI dashboards.** Automation rate, confusion matrix, time-to-decision (data-present), data-acquisition latency, extraction precision. *Deps:* C0.6. *Accept:* dashboards render the §1.4 KPIs as redefined in §16.

---

## Testing Decisions

**Scope (agreed): the deterministic decision core only** — modules **C1.1 Rules Engine, C1.2 Scorecard, C1.3 Pricing + income-haircut, C1.4 Adverse-Action Renderer + faithfulness check**, plus **C2.2 Consent Manager gate** and **C3.2 Confidence Service**. These are pure or near-pure, carry the highest compliance value, and are cheap to test in isolation. Agents, adapters, workflow, and UI are covered by manual verification + the eval harness (C7.1) rather than unit tests for the MVP.

**What makes a good test here:** test **external behavior, not implementation** — feed inputs (features, reason codes, consent state, OCR/validator signals) and assert outputs (policy hits, score/band, terms, rendered text faithfulness, allow/block, confidence/risk flags). No assertions on internal call order or private structure.

**Per module:**
- **Rules Engine:** table-driven fixtures covering each rule, hard-knockout precedence, and policy-hit/reason-code emission. Version a fixture set so a config change that shifts a boundary is caught.
- **Scorecard:** golden input→{score,band} fixtures; monotonicity sanity (worse inputs never improve the band).
- **Pricing + income-haircut:** terms-on-fixtures; a haircut that flips outcome/band marks the case income-sensitive (→ refer); a stable case does not.
- **Adverse-Action Renderer:** output covers **exactly** the fired reason-code set (no orphan claims, no omissions); mismatch is rejected; per-language template inserts numbers via code (no free translation of the legal sentence).
- **Consent Manager gate:** blocks on missing/withdrawn/wrong-purpose Layer 1; blocks on stale/absent Layer 2; allows only when both pass; mints Layer 2 at pull time.
- **Confidence Service:** confidence derives from grounded signals; each obvious-fake check raises the expected risk flag; a clean payslip passes.

**Prior art:** none yet (greenfield). Establish the table-driven fixture pattern in C1.1 first; later core modules follow it. Eval-harness (C7.1) golden sets are the integration-level analog and live separately from these unit fixtures.

---

## Out of Scope

- **Account Aggregator integration and cashflow-led underwriting for new-to-credit applicants** — the named first fast-follow `[16.7]`; AA adapter is stubbed for the MVP, thin-file escalates.
- Disbursement, servicing, collections, repayments `[1.3]`.
- Trained ML risk model (expert scorecard only); multiple products; multiple geographies; co-applicants/guarantors `[1.3]`.
- Sophisticated/fraud-network analytics — only obvious-fake document checks are in scope `[16.8, 14]`.
- High availability / scale — single-region, modest throughput `[1.3]`.
- RAG / `pgvector` policy retrieval — dropped for the MVP; future-only and never feeds the decision `[16.12]`.
- Unit tests for agents, adapters, workflow, and UI (covered by eval harness + manual verification this MVP).

## Further Notes

- **Time-to-decision SLA** is measured **data-present → decision**; data-acquisition latency is tracked separately (non-SLA); the < 1-minute demo number is on **mock adapters** `[16.3]`.
- **Open decision — risk/credit SME ownership (design §16.9, skipped):** the rules tables, scorecard weights, pricing policy, reason codes, eval-set labels/mix, and income-haircut threshold are credit-policy judgment and need a clearly accountable owner before real offers are issued behind the feature flag. Resolve in resourcing/RACI planning before pilot.
- **Demo target** `[11]`: one clean application lead-to-offer in under a minute (mock adapters), then a deliberately messy one dropping gracefully to the human queue.
- This PRD is intended to be split into issues later (tracer-bullet vertical slices) — the build components above map roughly one-to-one to grabbable issues.
