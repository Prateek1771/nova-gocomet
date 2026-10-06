# M1 manual test guide: DSL + generic engine

Step-by-step checks for everything M1 built ([07 M1](../07-build-plan.md#m1--dsl--engine)). Each step says what to do and what you should see. The full guide was run end to end against the live stack on 2026-10-06, and every step passed.

## What M1 built

| # | Capability | Where |
|---|---|---|
| 1 | Workflow DSL: one Pydantic model per node type (agent, decide, rule, human_task, action, parallel, wait, subflow, end); durations like `4h` | `packages/nova_dsl/src/nova_dsl/models.py` |
| 2 | Graph validator: unique ids, no dangling edges or unknown `goto`, reachability, every path ends, human-task outputs covered, CEL syntax, `nodes.X` references | `nova_dsl/graph.py` |
| 3 | CEL for `rule.when` and `${{ }}` templates, plus a `??` fallback; compiled once and cached | `nova_dsl/cel.py` |
| 4 | YAML 1.2 loader (`on:` stays a key, not `true`); DocType loader | `nova_dsl/loader.py`, `definitions/doc_types/` |
| 5 | DSL JSON Schema and OpenAPI → generated TypeScript types | `packages/nova_dsl/schema/`, `apps/web/src/dsl/`, `apps/web/src/lib/api-types.ts` |
| 6 | `NovaWorkflow`: one generic Temporal workflow for every process. It covers every node type, SLA escalation and continue-as-new. A failed step becomes a `needs_attention` task (retry / abort). | `services/engine/src/nova_engine/` |
| 7 | Projection: `run_steps`, `workflow_runs.status` and `audit_log` written in one transaction | `nova_engine/activities.py` |
| 8 | Exactly-once actions through `action_executions.idempotency_key` | `activities.run_action`, `actions.py` |
| 9 | Registries for node handlers and actions | `nova_core/registry.py` |
| 10 | Versioned TenantConfig; each run pins the version it started on | `PUT /tenant-config`, `nova_core/runs.py` |
| 11 | API: workflows (list, create, draft, validate, publish, versions), runs (start, list, get, cancel) and tasks (inbox, claim, complete). Claim and complete are validated Temporal updates. | `services/api/src/nova_api/routers/` |
| 12 | One `authorize()` seam: tenant scope now, OpenFGA in M4 | `nova_api/authz.py` |
| 13 | Temporal dev server and engine worker in `core`; `data` profile with Kafka, Debezium and ClickHouse | `infra/docker-compose.yml` |
| 14 | CI: engine tests with a 100% branch gate, schema and TS drift checks, engine image | `.github/workflows/ci.yml` |

## Before you start

- Run every command in **PowerShell** from the repo root.
- Users (password `dev`):

  | User | Role |
  |---|---|
  | `ops@acme` | ops_exec |
  | `lead@acme` | ops_lead |
  | `fin@acme` | finance |
  | `admin@acme` | tenant_admin |
  | `ops@bolt` | Bolt tenant |

- Tokens last **5 minutes**. When you get a 401, fetch a new token.
- Workflow keys below (`test_flow`, `gated`, …) only work once per database. When you rerun the guide, add a suffix (`test_flow2`); otherwise `POST /workflows` returns 409 because a draft already exists.
- The run ids, task ids and user ids in responses are UUIDs. Copy them from the previous step.

**Swagger or PowerShell.** Swagger at <http://localhost:8100/api/docs> works for every API step. Click **Authorize** and paste a token. You can also use this helper:

```powershell
function nova($user, $method, $path, $body) {
  $t = uv run python scripts/dev_token.py $user
  $p = @{ Method=$method; Uri="http://localhost:8100/api/v1$path"; Headers=@{Authorization="Bearer $t"}; ContentType="application/json" }
  if ($body) { $p.Body = ($body | ConvertTo-Json -Depth 10) }
  try { Invoke-RestMethod @p } catch { $_.ErrorDetails.Message }
}
# nova lead@acme GET /workflows
# nova ops@acme POST /runs @{workflow_key="demo_approval"; input=@{amount=500}}
```

---

## 0. Stack up

1. Start the stack:

   ```powershell
   docker compose -f infra/docker-compose.yml --profile core up -d --build
   ```

2. Check its status:

   ```powershell
   docker compose -f infra/docker-compose.yml --profile core ps
   ```

   **Expect:**
   - postgres, redis, keycloak, temporal, api and web are `healthy`
   - engine is `Up`
   - migrate is `Exited (0)`
3. Check the seed: `docker compose -f infra/docker-compose.yml logs migrate`. **Expect** `seeded acme` and `seeded bolt`. A fresh DB also shows `published acme/demo_approval v1`.
4. Open <http://localhost:8233> (Temporal UI). **Expect** the Workflows list for namespace `default`.
5. Open <http://localhost:3300>, log in as `ops@acme` / `dev`, then Sign out. This confirms M0 login still works.

## 1. Auth

1. Run `uv run python scripts/dev_token.py lead@acme`, then authorize Swagger with the token it prints.
2. `GET /api/v1/me`. **Expect:** `tenant.slug = "acme"` and `roles = ["ops_lead"]`. Note your `sub`, since step 4.9 uses it.
3. Call without a token: `curl.exe -s http://localhost:8100/api/v1/runs`.
   - **Expect:** `{"error":{"code":"unauthenticated","message":"missing bearer token","details":[]},"request_id":"…"}`

## 2. Workflow definitions and validation

1. `GET /workflows`. **Expect:** `demo_approval` with `latest_version ≥ 1`.
2. `GET /workflows/demo_approval/versions/1`. **Expect:** `status: published`, and `yaml` identical to `definitions/workflows/acme/demo_approval.yaml`.
3. `POST /workflows` with this body:

   ```json
   {"key":"test_flow","yaml":"metadata: {key: test_flow}\nnodes:\n  - {id: a, type: action, action: noop}\n"}
   ```

   **Expect:** 201, `valid: false`, `issues[0].code = "next_edge"`.
4. `POST /workflows/test_flow/publish`. **Expect:** 422, `error.code = "invalid_workflow"`, and `details[0].code = "next_edge"`.
5. `POST /workflows/test_flow/validate` with `{"yaml": "…"}` for each case below. **Expect** the listed code in `issues`.

   | YAML (`metadata: {key: test_flow}` first) | Code |
   |---|---|
   | two nodes with `id: a` | `duplicate_id` |
   | rule with `goto: ghost` | `unknown_goto` |
   | rule `when: 'input.x >'` | `cel_error` |
   | rule `when: 'nodes.ghost.output.ok'` | `unknown_ref` |
   | human_task with only an `on: approved` edge | `uncovered_output` |
   | `metadata: {key: test_flow` (missing `}`) | `yaml` |
   | `{id: a, type: teleport}` | `schema` |
   | `metadata: {key: other}` | `key_mismatch` |

6. `PUT /workflows/test_flow/draft` with this body. **Expect:** `valid: true`.

   ```json
   {"yaml":"metadata: {key: test_flow}\nnodes:\n  - {id: a, type: action, action: notify.log, with: {message: hi}}\n  - {id: done, type: end}\nedges:\n  - {from: a, to: done}\n"}
   ```

7. Publish twice with `POST /workflows/test_flow/publish`. **Expect:** `version: 1`, then `version: 2`. Published versions are immutable.
8. `PUT` a draft whose YAML says `key: other`. **Expect:** issue `key_mismatch`.

## 3. Auto-approve path (rule node)

`demo_approval` auto-approves when `amount < tenant.auto_approve_below` (1000 in the seed config).

1. `POST /runs` with `{"workflow_key":"demo_approval","input":{"amount":500}}`. **Expect:** 201, `status: pending`, `config_version` set.
2. About a second later, `GET /runs/{id}`. **Expect:**
   - `status: completed`
   - steps `route` → `notify` → `done`
   - `route.output.goto = "notify"`
   - `notify.output = {"delivered": true}`
3. *(optional)* The action's input is stored in the idempotency table. **Expect** `auto`:

   ```sql
   select request->>'by' from action_executions where run_id = '<RUN_ID>';
   ```

4. Temporal UI → workflow `run-<RUN_ID>`. **Expect:**
   - **Completed**
   - the history shows activities `load_definition` and `run_action`
   - `project_step` appears as **MarkerRecorded** events (it's a local activity)

## 4. Human task: claim, complete, and transition checks

1. `POST /runs` with `{"workflow_key":"demo_approval","input":{"amount":5000,"note":"rush"}}`.
2. `GET /runs/{id}`. **Expect:**
   - `status: waiting_human`
   - one task titled `Approve 5000 — rush`, with `assignee_role: ops_lead`
3. `GET /tasks`. **Expect:** that task with `status: open` and `due_at` 2 minutes out. Copy its `id`.
4. As `lead@acme`, `POST /tasks/{id}/complete` with `{"decision":"approved"}`. **Expect:** 409 "claim the task before completing it".
5. `POST /tasks/{id}/claim`. **Expect:** `status: claimed`. Then `GET /tasks?mine=true` lists it.
6. As `ops@acme`, `POST /tasks/{id}/claim`. **Expect:** 409 "task is claimed by someone else".
7. As `lead@acme`, complete with `{"decision":"maybe"}`. **Expect:** 409 "decision must be one of ['approved', 'rejected']".
8. Complete with `{"decision":"approved","payload":{"note":"looks fine"}}`. **Expect:** `status: done`, `decision: approved`.
9. `GET /runs/{id}`. **Expect:** `completed`, with steps `route, review, notify, done`. Then `select request->>'by' from action_executions where run_id = '<RUN_ID>'` should return lead's `sub` from step 1.2.
10. Complete the same task again. **Expect:** 409.
11. Start another run with amount 5000, then claim and complete it with `{"decision":"rejected"}`. **Expect:** the run ends `rejected`, with no `notify` step.

## 5. SLA escalation (real time, about 2½ minutes)

1. Start two runs with amounts 7000 and 7100. As `lead@acme`, claim the second run's task and leave both alone.
2. After about 2½ minutes, `GET /tasks`. **Expect** both tasks show:
   - `status: escalated`
   - `assignee_role: finance`
   - `assignee_user: null`, so lead's claim on the second one was cleared
3. As `fin@acme`, claim and complete each with `approved`. **Expect:** both runs end `completed`.

## 6. Cancel

1. Start a run with amount 9000 and wait for `waiting_human`.
2. `POST /runs/{id}/cancel`. **Expect:** 202 `{"status":"cancelling"}`.
3. `GET /runs/{id}`. **Expect:** `cancelled` with `ended_at` set. The task is gone from `GET /tasks`.
4. Cancel again. **Expect:** 409 "run is already cancelled".
5. Temporal UI. **Expect:** the workflow shows **Canceled**. The CLI reports the same: `docker compose -f infra/docker-compose.yml exec temporal temporal workflow describe --workflow-id run-<RUN_ID>` prints `Status CANCELED`.

## 7. Tenant isolation

Authorize as `ops@bolt`:

1. `GET /runs/{an acme run id}`. **Expect:** 404.
2. `GET /runs` and `GET /tasks`. **Expect:** no Acme runs or tasks.
3. `GET /workflows`. **Expect:** only Bolt's `demo_approval`.
4. `GET /tenant-config`. **Expect:** `approval_limits.ops_lead = 5000` (Acme's is 10000).

## 8. TenantConfig versioning and pinning

1. As `admin@acme`, `GET /tenant-config`. Note `version` (call it N) and `config.auto_approve_below` (1000).
2. `POST /workflows` with the body below, then `POST /workflows/gated/publish`. Here a human task runs *before* the rule reads the config.

   ```json
   {"key":"gated","yaml":"metadata: {key: gated}\nnodes:\n  - {id: gate, type: human_task, title: Gate, assignee: {role: ops_exec}, app: generic_review}\n  - id: route\n    type: rule\n    cases: [{when: \"double(input.amount) < double(tenant.auto_approve_below)\", goto: ok}]\n    default: held\n  - {id: ok, type: end}\n  - {id: held, type: end, status: rejected}\nedges:\n  - {from: gate, to: route, on: approved}\n  - {from: gate, to: held, on: rejected}\n"}
   ```

3. Start `gated` with `{"amount":1500}`. **Expect:** `config_version: N`.
4. While it waits, `PUT /tenant-config` with the full config from step 1, changing only `"auto_approve_below": 2000`. **Expect:** `version: N+1`.
5. Claim and complete the gate task with `approved`. **Expect:** the run ends `rejected`. It routed on the pinned limit of 1000, so 1500 is not below it.
6. Start `gated` again with 1500 and approve. **Expect:** `config_version: N+1` and `completed`.
7. `PUT /tenant-config` with `{"currency":"dollars"}`. **Expect:** 422 `validation_error`.
8. Put the limit back: `PUT /tenant-config` with the step 1 config (`auto_approve_below: 1000`).

## 9. Failed step → needs_attention

1. Publish `broken`. Its action name isn't registered; names are only checked at run time until the M3 catalog.

   ```json
   {"yaml":"metadata: {key: broken}\nnodes:\n  - {id: a, type: action, action: does.not.exist}\n  - {id: done, type: end}\nedges:\n  - {from: a, to: done}\n"}
   ```

   Use `PUT /workflows/broken/draft`, then `POST /workflows/broken/publish`.
2. Start it, then `GET /runs/{id}`. **Expect:**
   - `status: needs_attention`
   - step `a` shows `failed`, with the error in its output
   - a task titled `Step 'a' failed: …` with `assignee_role: tenant_admin`
3. As `admin@acme`, claim and complete it with `{"decision":"retry"}`. **Expect:** it fails again and a **new** attention task appears.
4. Claim and complete the new task with `{"decision":"abort"}`. **Expect:** the run ends `failed`.
5. Variant: publish a rule with `when: 'input.amount'` (not a boolean) and start it with `{"amount":1}`. **Expect:** `needs_attention`, with an error containing "must be a bool".

## 10. Parallel, wait and subflow

1. Publish `combo`:

   ```yaml
   metadata: {key: combo}
   nodes:
     - {id: fan, type: parallel, branches: [[ping], [nap]]}
     - {id: ping, type: action, action: notify.log, with: {message: "branch 1"}}
     - {id: nap, type: wait, duration: 30s}
     - {id: child, type: subflow, workflow: demo_approval, with: {amount: 100}}
     - {id: done, type: end}
   edges:
     - {from: fan, to: child}
     - {from: child, to: done}
   ```

2. Start it, and within a few seconds `GET /runs/{id}`. **Expect:** `running`, with `ping` completed and `nap` running.
3. After about 30 seconds, check again. **Expect:**
   - `completed`
   - `fan.output = {"completed":[0,1]}`
   - `child.output` includes a `run_id` and `status: completed`
4. `GET /runs/{child.run_id}`. **Expect:** a separate `demo_approval` run, auto-approved. The Temporal UI shows it as a child of the parent.
5. *Join any:* publish a copy (`comboany`) with `join: any` on `fan` and `nap` set to `10m`. **Expect:**
   - the run completes right away
   - `nap` shows `skipped`
   - `fan.output = {"completed":[0]}`

## 11. Wait for an event (signal)

1. Publish `ev`:

   ```json
   {"yaml":"metadata: {key: ev}\nnodes:\n  - {id: w, type: wait, event: docs_ready, duration: 10m}\n  - {id: done, type: end}\nedges:\n  - {from: w, to: done}\n"}
   ```

2. Start it and copy the run id.
3. Send the signal. The backslashes are needed in Windows PowerShell 5.1; in Git Bash use `'"docs_ready"'` instead.

   ```powershell
   docker compose -f infra/docker-compose.yml exec temporal temporal workflow signal --workflow-id run-<RUN_ID> --name event --input '\"docs_ready\"' --input '{\"n\":2}'
   ```

4. `GET /runs/{id}`. **Expect:** `completed`, with `w.output = {"event":{"n":2}}`.
5. Start another `ev` run and leave it for 10 minutes. **Expect:** `w.output = {"timed_out":true}`.

## 12. Data in Postgres

Open psql with `docker compose -f infra/docker-compose.yml exec postgres psql -U postgres -d nova`. The superuser bypasses RLS, so you see every tenant.

```sql
select key, version, status, published_by from workflow_definitions order by key, version;
select id, status, config_version, started_at, ended_at from workflow_runs order by started_at desc limit 10;
select node_id, node_type, status from run_steps where run_id = '<RUN_ID>' order by started_at;
select title, status, assignee_role, decision from human_tasks order by due_at desc nulls last limit 10;
select idempotency_key, action, status from action_executions order by created_at desc limit 10;
select actor_type, actor_id, action, subject from audit_log order by id desc limit 20;
-- no action ever ran twice:
select idempotency_key from action_executions group by 1 having count(*) > 1;   -- expect 0 rows
-- a task's trail (task id from step 4):
select action from audit_log where subject = 'task:<TASK_ID>' order by id;      -- task.open, task.claimed, task.done
```

RLS as the app role:

```sql
set role nova_app;
select count(*) from workflow_runs;   -- expect 0: no tenant set, fail closed
select set_config('app.tenant_id', (select id::text from tenants where slug='acme'), false);
select count(*) from workflow_runs;   -- expect Acme's runs only
reset role;
```

## 13. Durability: engine restart

1. Start a run with amount 5000 and wait for `waiting_human`.
2. Run `docker compose -f infra/docker-compose.yml restart engine`.
3. Claim and complete the task. **Expect:** the run still ends `completed`, because Temporal replays the history to the new worker.

## 14. Data profile: Kafka, Debezium and ClickHouse

1. Start it with `docker compose -f infra/docker-compose.yml --profile core --profile data up -d`.
2. Register the connector. A 409 means it's already registered, which is fine.

   ```powershell
   curl.exe -X POST -H "Content-Type: application/json" --data "@infra/kafka-connect/debezium-nova.json" http://localhost:8083/connectors
   ```

3. `curl.exe http://localhost:8083/connectors/nova-outbox/status`. **Expect:** the connector and its task are both `RUNNING`.
4. In psql, insert an outbox row:

   ```sql
   insert into outbox (tenant_id, aggregate, type, payload) select id, 'run', 'run.completed', '{"run_id":"manual-1"}' from tenants where slug='acme';
   ```

5. Read it from Kafka:

   ```powershell
   docker compose -f infra/docker-compose.yml exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic nova.outbox.run --from-beginning --timeout-ms 15000 --property print.headers=true
   ```

   **Expect:** a line with headers `id:…,tenant_id:…,type:run.completed` and the body `{"run_id":"manual-1"}`.
6. Query ClickHouse:

   ```powershell
   curl.exe "http://localhost:8124/?user=nova&password=nova" --data-binary "select version()"
   ```

   **Expect:** `25.8.x`.

## 15. Automated suites

| Command | Expect |
|---|---|
| `uv run pytest tests/unit -q` | 47 passed |
| `uv run coverage run -m pytest tests/engine -q` | 16 passed |
| `uv run coverage report` | 100% on `interpreter.py` and `handlers.py` |
| `uv run pytest tests/integration -q` | 12 passed (about 3 min, needs Docker) |
| `uv run ruff check .` / `uv run mypy` / `uv run lint-imports` | clean; 3 contracts kept |
| `pnpm -C apps/web test` / `typecheck` / `lint` | 8 passed; clean |

Drift check: running `uv run python -m nova_dsl.schema`, `uv run python -m nova_api.openapi` and `pnpm -C apps/web gen` should leave `git status` unchanged.

## Known limits (by design in M1, not bugs)

- Any user in the tenant can claim any task, and the inbox isn't filtered by role. Role checks (OpenFGA) arrive in M4.
- Unknown action and agent names pass validation and fail at run time. The M3 catalog closes this.
- `agent` and `decide` nodes need the agents worker (M2). Until then, runs that use them wait on that activity.
- There are no Runs or Inbox screens yet. The Inbox comes in M2 and the Studio in M3.
