This folder contains all business-domain packages consumed by the chatbot engine.

- `app/assistant/`, `app/services/`, and `app/domains/registry.py` stay generic engine code.
- Each subfolder here is a self-contained domain package such as `vts`, `ims`, or `maintenance`.
- Generator and onboarding tooling now write new domain packages here by default.
