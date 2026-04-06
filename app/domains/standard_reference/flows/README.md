# Flows

Use this folder for write-oriented YAML flows that should guide the user through structured data collection before a DB write.

Included here:

- `create_work_item.yaml`: a simple example insert flow for the main entity

Guidelines:

1. Keep one flow per business action.
2. Prefer lookup-backed fields for IDs instead of free-text IDs when possible.
3. Make optional steps explicit.
4. End with a confirmation state before `db_write`.
