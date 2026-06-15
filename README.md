# AI-Native Lending Platform — MVP

An agentic loan-origination pipeline that takes a personal-loan applicant from **lead → priced, explainable credit decision + offer**, fully automated on clean cases and degrading gracefully to a human queue for everything else.

**Load-bearing principle:** the binding credit decision is always produced by a **deterministic rules + scorecard engine, never by an LLM**. Agents handle perception (document/identity extraction), orchestration (data pulls on consent), and communication (faithful explanations, adverse-action reasons, offers) — they never decide.

## Documents

- [Design Doc](docs/AI-Native-Lending-MVP-Design-Doc.md) — architecture, agent specs, and the §16 Design Review Decisions.
- [PRD](docs/AI-Native-Lending-MVP-PRD.md) — problem/solution, user stories, modular build components, and testing scope.

## Scope (MVP)

- **Segment:** credit-tested, salaried applicants with a CIBIL record (India). Thin-file escalates.
- **Stack:** Python 3.11 + FastAPI · Temporal · LangGraph · Claude (Anthropic API) · PostgreSQL · React.
- **Out of scope:** Account Aggregator / cashflow-led underwriting (first fast-follow), disbursement/servicing, trained ML risk model, multi-product/geo, HA/scale.

## Status

Pre-implementation. Build components are enumerated in the PRD (Epics 0–7) and will be tracked as issues.
