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

## Run the demo

A one-command local stack (Postgres + Temporal + API + worker) with adapters in **mock mode**. Requires Docker. Demo/pilot-grade — not production (no HA/scale).

```bash
docker compose up --build -d          # start postgres + temporal + api + worker
curl localhost:8000/health            # → {"status":"ok"}

# drive a clean + an exception applicant through the live pipeline:
docker compose exec worker python -m lending.demo.run_demo
#   [clean]     ... → final state OFFER_GENERATED | decision approve | reasons []
#   [exception] ... → final state REFERRED        | decision refer   | reasons ['HIGH_DTI']

# create/read applications directly:
curl -X POST localhost:8000/applications -H 'content-type: application/json' \
  -d '{"applicant":{"full_name":"Priya"},"features":{"cibil_score":780}}'
curl localhost:8000/applications/<id>
curl localhost:8000/applications/<id>/explanation

docker compose down -v                # tear everything down
```

Temporal's web UI is at `localhost:8080`. Config is env-driven (`DATABASE_URL`,
`TEMPORAL_ADDRESS`, `ADAPTER_MODE`, `PILOT_ENABLED`); see `.env.example`. Frontend
surfaces (applicant UI, pipeline viewer) plug into this stack when built (#29/#30).

## Status

Decision backbone complete: intake → Temporal state machine → real rules+scorecard
decision → explanation → audit, reproducible and runnable via the demo harness above.
Remaining work (adapters, agents, UI surfaces) is tracked in the issues.
