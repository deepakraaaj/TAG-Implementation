# Production Readiness Audit — TAG Assistant Backend

**Scope audited:** `NL2SQL Assistant/` (the FastAPI NL-to-SQL chatbot backend).
**Date:** 2026-06-16

> **Note on stack:** The brief anticipated Java/Spring Boot + JUnit. The actual
> backend is **Python 3.12 / FastAPI / SQLAlchemy + aiomysql / LangGraph**, with
> a **pytest** suite. Tests below are written in pytest to match the repo.

---

## Verdict

**🔴 NOT READY for production.** The SQL guardrail itself is solid (real
`sqlglot` AST parsing, DDL/DML/stacked-statement/system-table rejection), **but
the entire authentication and tenant-trust model described in the design does
not exist.** There is **no JWT verification**, **no signed identity**, and the
tenant database is selected from a **client-controlled header**. Identity and
roles arrive as **unsigned Base64 JSON** the client fully controls. Any caller
can choose which tenant database to query and can self-assert any role. This is
release-blocking regardless of how good the SQL layer is.

---

## Area-by-area status

| # | Area | Status | Severity | Evidence |
|---|------|--------|----------|----------|
| 1 | SQL guardrail | **Partial (good core, real gaps)** | High | `app/services/data/sql_validator.py` |
| 2 | Tenant isolation | **Missing (trust boundary broken)** | 🔴 Blocker | `app/api/v1/endpoints/chat.py:130-172`, `app/db/multi_tenant_manager.py:34-37` |
| 3 | Auth handshake (JWT) | **Missing entirely** | 🔴 Blocker | `app/api/v1/endpoints/chat.py:71-79,228-264`; no auth middleware in `app/main.py` |
| 4 | RBAC at data layer | **Partial (mutation only; trusts client role)** | High | `app/assistant/nodes/sql/sql_validate_node.py:177-189` |
| 5 | General production surface | **Partial** | Medium | see notes |

---

## 1. SQL guardrail layer — Partial

**Implemented & verified** (`SQLValidatorService`, real `sqlglot` parser, v29):
- DDL rejected: `DROP`, `ALTER`, `CREATE`, `TRUNCATE` → not an allowed top-level type. (`sql_validator.py:24-25,165-167`)
- `DELETE` rejected; `INSERT`/`UPDATE` rejected unless `allow_mutations` (default **False** in `AppConfig`, `app/apps/registry.py:20`).
- **Statement chaining via `;` is rejected** — sqlglot parses multiple statements as a `Block`, which fails the allowed-top-level check. Verified empirically.
- Subqueries/joins/UNIONs to non-allowlisted tables rejected; system schemas (`information_schema`, `mysql`, `performance_schema`, `sys`) hard-blocked. (`sql_validator.py:10,221-244`)
- Plain `SELECT` without `WHERE` rejected; `UPDATE` without `WHERE` rejected. (`sql_validator.py:104-128,195-201`)

**Gaps (release-relevant):**
- **No row `LIMIT` enforcement.** A `WHERE`-filtered but unbounded `SELECT` passes, and `sql_execute_node.py:172` does `result.mappings().all()` — **all** rows are pulled into memory. Resource-exhaustion + bulk-exfiltration risk. *(test: `test_select_without_limit_should_be_rejected`, xfail)*
- **No deny-by-default.** `allowed_tables` defaults to `None`, and `SQLValidateNode` constructs the validator with `allowed_tables=None` (`sql_validate_node.py:25`). When the app registry is disabled, **every table is readable**. *(test: `test_missing_allowlist_should_deny_by_default`, xfail)*
- **DB principal is not provably read-only.** `sql_execute_node.py:169-180` uses an ordinary connection and even calls `conn.commit()` for non-row statements — read-only is *policy in the validator*, not *defense-in-depth at the DB*. The connection user should be a `SELECT`-only grant.

## 2. Tenant isolation — Missing (🔴 blocker)

- Tenant/database is chosen by `app_id`, taken from the **client-supplied `x-app-id` header or request body metadata** (`chat.py:130-142`), then mapped to a DB URL (`multi_tenant_manager.py:34-37`). Nothing binds the caller to a tenant.
- **A request can reach any tenant's database by changing one header.** *(tests: `test_client_controls_tenant_selection_via_app_id_header` documents it; `test_app_id_should_not_be_taken_from_client_header`, xfail, asserts the fix.)*
- **Guardrail metadata is client-spoofable when the registry is disabled.** `x-user-context` is merged into `request.metadata` (`chat.py:260`); `_apply_app_config` only overrides `allow_mutations`/`allowed_tables`/`require_select_where` **if the registry is enabled** (`chat.py:145-172`). With it disabled, the client sets those flags directly and weakens the guardrail. *(test: `test_client_cannot_self_assert_guardrail_metadata`, xfail.)*
- Isolation is "database-per-app via config registry," not "database-per-tenant derived from a verified `tenant_id` claim" as designed.

## 3. Auth handshake — Missing entirely (🔴 blocker)

- **No JWT anywhere.** No signature verification, no expiry, no issuer/audience, no per-tenant signing key, no auth middleware (`app/main.py` wires only rate-limit + request-context + CORS).
- Identity comes from `_decode_user_context` (`chat.py:71-79`): **unsigned, unverified Base64 JSON**. `user_id`, `user_role`, `company_id` are whatever the client sends. *(tests: `test_user_context_is_decoded_without_any_signature_check`, `test_forged_role_should_not_be_trusted` xfail, `test_modified_payload_should_be_rejected` xfail, `test_expired_token_should_be_rejected` xfail.)*
- Malformed tokens **fail open** — logged and ignored, request proceeds anonymously (`chat.py:262-264`). *(test: `test_tampered_token_is_silently_ignored_not_rejected`.)*
- **No iframe origin allowlist.** Only `CORSMiddleware`, defaulting to `["*"]` outside production (`main.py:24-31`, `app/config.py:263-264`). CORS is browser-advisory, not server-side origin enforcement.

## 4. RBAC at the data layer — Partial

- **Works:** INSERT/UPDATE are gated by role (`MUTATION_ALLOWED_ROLES`, default `admin,superadmin`) and require explicit permission (`sql_validate_node.py:177-189`). Verified: a `user` role cannot mutate; `admin` can with the flag.
- **Gap:** the role used for that check is the **client-asserted** role from the unsigned header — server-side enforcement trusts a client value. *(test: `test_mutation_role_should_not_come_from_client_metadata`, xfail.)*
- **Gap:** **no column/row-level entitlements.** The validator *can* reject out-of-set columns, but `SQLValidateNode` feeds it the **full live schema** (`SchemaService.get_table_columns`), never a role-scoped allowlist — so column RBAC is never actually applied. The "role-filtered semantic layer" is not enforced at the data layer. *(tests: `test_validator_can_enforce_column_allowlist_when_supplied` shows the lever exists; `test_schema_feed_is_role_scoped_for_column_rbac`, xfail.)*

## 5. General production surface — Partial

- **Audit incompleteness.** `AuditService` (`audit_service.py`) logs only **report executions** (company/user/report/time/rows/status) and is **off by default** (`ENABLE_AUDIT_LOGGING=False`, `config.py:76`). The NL-to-SQL chat path is **not audited**: prompt, generated SQL, executed SQL, principal, and tenant are **not** captured per the design's "every query is audited."
- **Prompt-injection detector is best-effort and partial.** Runs only on the general-chat route (`chat_node.py:353`), **not** the NL-to-SQL route, and misses natural phrasings (e.g. "show me your system prompt"). *(tests: `test_detector_is_invoked_on_sql_route` xfail, `test_detector_catches_natural_system_prompt_extraction` xfail.)* The SQL guardrail remains the real backstop.
- **Positives:** query timeout + DB pool settings exist (`config.py`), rate limiting present, structured request logging with request IDs, CORS hardening enforced for `APP_ENV=production` (`config.py:270-275`), secrets read from env (no committed vault, but `.env`/`.env.production` are present in-tree — verify they hold no real secrets).

---

## Top blockers to fix before release

1. **Introduce real JWT auth** (signature + expiry + issuer/audience, per-tenant key). Derive `tenant_id`, `user_id`, `roles` **only** from verified claims. Reject unsigned/tampered/expired tokens (fail closed).
2. **Bind tenant routing to the verified `tenant_id` claim**; ignore `x-app-id`/metadata for tenant selection.
3. **Stop trusting client metadata for guardrail flags and roles** — server-derived only, always (not just when the registry is enabled).
4. **Enforce a mandatory row `LIMIT`** and **deny-by-default table allowlist** in the guardrail.
5. **Use a read-only DB principal** for query execution (defense in depth).
6. **Audit the NL-to-SQL path** (prompt, generated SQL, executed SQL, principal, tenant, row count) and enable auditing by default.
7. **Enforce a server-side iframe origin allowlist.**

---

## Test files added (all runnable: `pytest tests/unit/security/`)

> Convention: passing tests pin current verified behaviour; `xfail` tests assert
> the *secure* behaviour and fail today — closing a gap turns them XPASS, a
> built-in signal to remove the marker. No guardrail was weakened to make a test
> pass. **Result: 40 passed, 11 xfailed.**

| File | Covers |
|------|--------|
| `tests/unit/security/test_sql_guardrail_security.py` | DDL/DML rejection, `;`-chaining/stacking, table allowlist (incl. subquery/join/UNION/system schemas), filtered-read enforcement; **xfail:** no-LIMIT, no deny-by-default allowlist. |
| `tests/unit/security/test_tenant_isolation_security.py` | Per-tenant DB routing, unknown-tenant rejection; **xfail:** client-controlled tenant via header, client-spoofable guardrail metadata. |
| `tests/unit/security/test_auth_handshake_security.py` | Unsigned context decoding, fail-open on tampered token; **xfail:** forged role, tampered payload, expired token must be rejected. |
| `tests/unit/security/test_rbac_data_layer_security.py` | Mutation role gating (allow/deny, explicit-permission); **xfail:** role sourced from client, no column-level RBAC. |
| `tests/unit/security/test_prompt_injection_security.py` | Detector hits on classic injections, sanitization; guardrail rejects injected/destructive SQL; **xfail:** SQL route not screened, natural prompt-extraction phrasing missed. |
