# Chatbot Widget Integration Guide

Last updated: **2026-03-05**

## Scope

This guide covers integrating the Kriti/FITS chatbot widget with TAG backend endpoints.

## Backend Endpoint Contract

- `POST /session/start`
  - Returns `{ "session_id": "<uuid>" }`
- `POST /chat` (or `/query`)
  - Default mode: NDJSON stream (`application/x-ndjson`)
  - Debug mode: `/chat?stream=false` returns buffered terminal JSON (`application/json`)

## Required Request Body

```json
{
  "session_id": "string",
  "message": "string",
  "user_id": "optional string",
  "user_role": "optional string",
  "idempotency_key": "optional string",
  "metadata": {}
}
```

## Recommended Headers

- `Content-Type: application/json`
- `x-user-context: <base64-json>`
  - Supports `user_id`, `user_role`, `company_id`, `user_name`, `company_name`
- `x-trace-id: <optional trace id>`
- `x-response-format: json|toon|both` (optional)

## Response Modes

### Stream Mode (default)

Use for production widget UX with incremental assistant updates.

`POST /chat`

Event envelopes:

- `{"type":"token","content":"..."}`
- `{"type":"error","message":"..."}`
- `{"type":"result", ...}` (terminal)

### Buffered JSON Mode (debug/devtools)

Use when you need full payload visibility in browser Network tab.

`POST /chat?stream=false`

Returns only terminal result object:

- `{"type":"result","status":"ok|error", ...}`

## Frontend Example

```ts
const response = await fetch(`${backendUrl}/chat?stream=false`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-user-context": contextB64,
  },
  body: JSON.stringify({
    session_id: currentSessionId,
    message: trimmedMessage,
  }),
});
```

## Report Routing Behavior

- Explicit report intent (for example, `list reports`, `show ... report`) routes to report node.
- Generic SQL-like prompts (for example, `show pending tasks`) stay on SQL path.
- Report response data is returned in terminal payload under `report` and `report_result`.

## Health and Readiness Checks

- `GET /health/live`: liveness
- `GET /health/ready`: readiness (`503` when required checks fail)
- `GET /health`: readiness snapshot (`checks` map with config/db/cache/reporting statuses)

## Troubleshooting

### "Failed to load response data" in DevTools

Cause: request used streamed `/chat` response.

Fix:

- Use `/chat?stream=false` for debugging
- Or inspect stream via `curl -N` / reader API

### Report output appears when expecting SQL output

Check:

- Query includes explicit report keywords (`report` / `reports`)
- Backend is updated to latest router guard behavior

### Widget context not applied

Check:

- `x-user-context` is base64 URL-safe JSON
- `company_id` and `user_id` values are populated and not empty
- `user_name` fallback lookup has DB access in backend logs
