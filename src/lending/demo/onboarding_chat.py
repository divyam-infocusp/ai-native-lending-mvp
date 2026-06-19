"""
Interactive local runner for the Onboarding Copilot (#22) — chat with the live agent.

Uses an in-memory SQLite LOS + Memory checkpointer (single process), and a real
Gemini (lite) reasoning step, so it exercises the live model end to end.

Run:
    set -a; source .env; set +a          # exports GOOGLE_API_KEY etc.
    .venv/bin/python -m lending.demo.onboarding_chat

Type 'quit' to stop early. When the application is complete, the full collected
field set (the UI's review screen) is printed.
"""
from __future__ import annotations

import json

from lending.agents import OnboardingCopilot, register_document
from lending.agents.onboarding import REQUIRED_DOCUMENTS
from lending.audit import AuditStore
from lending.los import Applicant, Application, ApplicationRepository, make_engine


def main() -> None:
    engine = make_engine()  # in-memory SQLite, shared within this process
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)

    name = input("Applicant full name [Priya Sharma]: ").strip() or "Priya Sharma"
    application = Application(applicant=Applicant(full_name=name))
    repo.save(application)
    app_id = application.application_id

    copilot = OnboardingCopilot()  # real Gemini (lite) + durable memory

    print("(tip: simulate a real upload with '/upload <doc_type>' or '/upload all'; "
          f"doc types: {', '.join(REQUIRED_DOCUMENTS)})")

    # First turn: greeting / first cluster of questions (no user message yet).
    resp = copilot.turn(repo, audit, app_id)
    print(f"\nCopilot: {resp.assistant_message}")

    while not resp.complete:
        user = input("\nYou: ").strip()
        if user.lower() in {"quit", "exit"}:
            print("\n(stopped — application left incomplete)")
            return
        if user.startswith("/upload"):
            targets = user.split()[1:] or ["all"]
            docs = REQUIRED_DOCUMENTS if "all" in targets else targets
            for doc in docs:
                register_document(repo, app_id, doc)
            print(f"   · uploaded (registered): {docs}")
            user = "I've uploaded the documents."
        resp = copilot.turn(repo, audit, app_id, user)
        print(f"\nCopilot: {resp.assistant_message}")
        if resp.missing:
            print(f"   · still missing: {resp.missing}")

    print("\n✅ Application complete — review screen data:\n")
    print(json.dumps(resp.collected, indent=2, default=str))


if __name__ == "__main__":
    main()
