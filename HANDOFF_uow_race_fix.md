# Handoff: Fix shared-`UnitOfWork` race in the parallel agent run

**Audience:** Claude Code, working in the `auto_apply_app` repo.
**Type:** Bug fix + a scoped consistency cleanup. Backend / dependency-injection layer.
**Priority:** High — this corrupts DB sessions during every parallel agent run in production.

> **Read this whole document before touching code.** The fix itself is a small, uniform,
> mechanical transform, but there are three tempting shortcuts that are *wrong* and a few
> non-uniform call sites that must be edited by hand. Getting those wrong reintroduces the
> bug or breaks the web app. Do not improvise beyond what is written here.

---

## 1. Symptom

Running the agent as a Cloud Run Job produces a flood of SQLAlchemy/asyncpg tracebacks during
the scrape phase. The distinct error classes seen in logs:

- `asyncpg.exceptions._base.InterfaceError: cannot perform operation: another operation is in progress`
- `sqlalchemy.exc.ResourceClosedError: This transaction is closed`
- `sqlalchemy.exc.PendingRollbackError`
- `sqlalchemy.orm.exc.IllegalStateChangeError`
- `sqlalchemy.exc.InvalidRequestError`

All tracebacks originate at two spots in `agent_state_use_cases.py`:

- `IsAgentKilledForSearchUseCase.execute` (the `async with self.uow` line)
- `HeartbeatAgentForSearchUseCase.execute` (the `async with self.uow` line)

These two use cases are called by **every worker** on every node entry and inside the
scrape/submit loops (via each worker's `_beat()` and `_is_killed()` helpers). They are the two
use cases the parallel fan-out hammers hardest — which is why they, and not the serially-called
master-only use cases, are where the corruption surfaces.

The errors are currently *fail-soft* (both use cases catch the exception and return
`Result.success(False)`), so the run limps on rather than crashing. The real damage: lost
heartbeats (liveness/reconnect polling can read "dead" while the agent is alive), kill-checks
that silently return "not killed" during a collision window, and a swamped log.

The web/API path never trips this — see §3 for why.

---

## 2. Root cause (mechanical — this is confirmed, not a hypothesis)

A single `SqlAlchemyUnitOfWork` **instance** is shared across concurrently-running worker tasks.

Trace the object lifecycle in `auto_apply_app/infrastructures/configuration/container.py`:

```python
@cached_property
def _agent_service(self):
    uow = self.uow_factory()          # ← called ONCE; @cached_property → one instance per process
    ...
    return create_agent(
        is_agent_killed_for_search_use_case=IsAgentKilledForSearchUseCase(uow),  # same instance
        heartbeat_use_case=HeartbeatAgentForSearchUseCase(uow),                   # same instance
        ...                                                                       # ~9 more, all this uow
    )
```

`create_agent` passes those already-constructed use-case instances straight into the three
workers (`is_agent_killed=...`, `heartbeat=...`) and the `MasterAgent`. So all three workers
share one `heartbeat` instance and one `is_killed` instance — which share **one UoW instance**.

Now look at what `__aenter__` does in
`auto_apply_app/infrastructures/persistence/database/repositories/unit_of_work_repo_db.py`:

```python
async def __aenter__(self):
    self.session = self.session_factory()          # stored on the shared instance
    await self.session.begin()
    self.agent_state_repo = AgentStateRepoDB(self.session)   # repos bound to whatever self.session is now
    ...
    return self
```

The session **and every repository** are stored as attributes on `self`. When the LangGraph
`Send` fan-out launches APEC + HelloWork + WTTJ at the same instant, their `_beat()` /
`_is_killed()` calls enter `async with self.uow` on that one instance concurrently:

1. Task A enters → `self.session = SessionA`, begins it, repos point at A.
2. Task B enters → `self.session = SessionB` (**overwrites**), repos now point at B.
3. Task A runs a query through `uow.agent_state_repo`, which now points at SessionB →
   two coroutines drive one asyncpg connection → **`cannot perform operation: another
   operation is in progress`**.
4. Task A's `__aexit__` runs `await self.session.close()` — but `self.session` is now B's →
   Task A closes the transaction B is mid-flight on → **`This transaction is closed` /
   `PendingRollbackError` / `IllegalStateChangeError`** on B.

Every error class in the logs is a downstream symptom of this one race: an overwritten session
reference plus a cross-task `close()`. asyncpg connections are strictly single-operation-at-a-time,
and a SQLAlchemy `AsyncSession` is **not** safe for concurrent use by multiple tasks.

**The `uow_factory` was designed correctly.** `Application.uow_factory` is already a
`Callable[[], UnitOfWork]` that produces a fresh UoW per call. The container just resolves it
eagerly (`uow = self.uow_factory()`) and shares the single result, instead of passing the
factory down so each `execute()` can open its own. The fix is to stop resolving it early.

### There is a second, latent instance of the same bug

The container builds a **separate** shared UoW inside `agent_runner`:

```python
@cached_property
def agent_runner(self):
    uow = self.uow_factory()
    return AgentRunner(
        ...
        load_start_ctx=LoadStartRunContextUseCase(uow),
        load_resume_ctx=LoadResumeRunContextUseCase(uow),
    )
```

This one is **not** in the observed production crash, because `AgentRunner.run_start` awaits
`load_start.execute()` once, sequentially, *before* any workers spin up. But it becomes a live
race under `LocalDispatcher` (dev / MEMORY mode), where `_spawn` does
`asyncio.create_task(...)` — two dispatched runs then execute concurrently in one process and
share the cached `_agent_service` and `agent_runner`. The per-call-factory fix closes both the
production vector and this latent one at once, which is why we fix both rather than patching
only the hot path.

---

## 3. Why the web/API path is unaffected (do not "fix" it differently)

Every controller factory in the container is a plain `@property` (not `@cached_property`), so a
**fresh UoW instance is created per property access**, and a single HTTP request drives its use
cases serially. One instance is therefore only ever touched by one coroutine at a time. The
agent run is the *only* place one UoW instance is shared across concurrent tasks. After this
fix, the web path behaves identically — a fresh session per `execute()` is strictly safer and
changes no web semantics.

---

## 4. The fix

Inject the **factory**, and open a fresh `UnitOfWork` per `execute()` call.

### 4.1 The one mechanical rule (applies to every affected use case, without exception)

**Dataclass field:**

```python
# BEFORE
@dataclass
class HeartbeatAgentForSearchUseCase:
    uow: UnitOfWork

# AFTER
@dataclass
class HeartbeatAgentForSearchUseCase:
    uow_factory: UnitOfWorkFactory        # was: uow: UnitOfWork
```

**Method body — replace the context-manager expression only; keep the `as uow` local so the
body below is untouched:**

```python
# BEFORE
async def execute(self, ...):
    try:
        async with self.uow as uow:
            ... unchanged ...

# AFTER
async def execute(self, ...):
    try:
        async with self.uow_factory() as uow:      # fresh session per call → no sharing
            ... unchanged ...
```

That's it. The inner variable stays named `uow`, so nothing inside the block changes.

### 4.2 INVIOLABLE constraints on the transform

- **One `async with` block stays exactly one block. Never split it.** Several use cases perform
  multiple writes inside a single block to get one atomic transaction — e.g.
  `StartJobSearchAgentUseCase.prepare` writes user + search + agent_state, and
  `LoadStartRunContextUseCase` reads user + search + subscription + preferences + credentials.
  One `async with self.uow_factory()` is still one session = one transaction, so atomicity is
  preserved **only if you keep it as a single block**. Do not introduce a second `async with`.
- **Do not touch the existing commit semantics.** Some use cases call `await uow.commit()`
  explicitly inside the block *and* the UoW's `__aexit__` also auto-commits on clean exit. That
  double-commit is pre-existing and harmless; leave it exactly as is (see §7, out of scope).
- **`as uow` must be preserved.** Do not rewrite bodies to call `self.uow_factory()` twice or to
  reference `self.uow_factory` inside the block.

### 4.3 Recommended: add a type alias (best practice, do this first)

To avoid repeating `Callable[[], UnitOfWork]` across ~45 dataclasses, add to
`auto_apply_app/application/repositories/unit_of_work.py`:

```python
from typing import Callable

# ... existing UnitOfWork definition ...

UnitOfWorkFactory = Callable[[], "UnitOfWork"]
```

Then every use-case field becomes `uow_factory: UnitOfWorkFactory`. In each use-case module,
update the import accordingly:

```python
# BEFORE
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
# AFTER (keep UnitOfWork too only if the module still references it elsewhere)
from auto_apply_app.application.repositories.unit_of_work import UnitOfWorkFactory
```

---

## 5. Scope & sequencing

Deliver as **two commits in one PR**. Commit 1 fixes the production race and is independently
deployable and verifiable. Commit 2 is a mechanical consistency sweep so the codebase has one
rule, not two.

> **Why finish the migration (commit 2) instead of stopping at commit 1:** leaving some use
> cases on `uow: UnitOfWork` and some on `uow_factory` is itself the footgun that created this
> bug. The next person who adds an agent use case by copy-pasting an existing "instance" one
> silently reintroduces the race. A single rule across the codebase removes that class of bug.
> Commit 2 is low-risk (web use cases run serially per request) and purely mechanical.

The commit boundary is drawn **by file**, converting each use-case file wholesale and fully
updating every controller that consumes only converted files. This avoids any controller factory
ending up with a mix of "pass factory" and "pass instance" constructions.

### Commit 1 — the fix (agent run path)

Convert **all classes** in these six use-case files to the §4.1 rule:

- `application/use_cases/agent_state_use_cases.py`
- `application/use_cases/agent_use_cases.py`
- `application/use_cases/job_offer_use_cases.py`
- `application/use_cases/agent_usage_use_cases.py`
- `application/use_cases/fingerprint_use_cases.py`
- `application/use_cases/run_context_use_cases.py`

Then update `container.py`:

- `_agent_service` and `agent_runner` (exact diffs in §6.1 / §6.2).
- The three controller factories whose use cases all come from the files above:
  `agent_controller`, `job_offer_controller`, `agent_state_controller` (§6.3).

After commit 1 the app boots and runs on both the API process and the Cloud Run Job worker
process (both go through `Application.agent_runner` — there is no separate hand-wired worker
path to change). The production race is gone.

### Commit 2 — consistency (remaining web use cases)

Convert **all classes** in the remaining use-case files to the same rule (e.g.
`user_use_cases.py`, `subscription_use_cases.py`, `preferences_use_cases.py`,
`free_search_use_cases.py`, and any other use-case module in the tree that still declares
`uow: UnitOfWork`). Then update their controller factories in `container.py`
(`user_controller`, `auth_controller`, `subscription_controller`, `prefrences_controller`,
`free_search_controller`) with the same mechanical edit shown in §6.3.

**Discovery step for commit 2 (do not rely on this list being exhaustive):**

```bash
grep -rln "uow: UnitOfWork" auto_apply_app/application/use_cases/
```

Every file still matching after commit 1 must be converted in commit 2.

---

## 6. Exact container edits (these are NOT uniform — apply verbatim)

Delete every `uow = self.uow_factory()` local as you convert each property; pass
`self.uow_factory` (the factory object itself — **no parentheses**) into each use case.

### 6.1 `_agent_service`

```python
# BEFORE
@cached_property
def _agent_service(self):
    uow = self.uow_factory()
    proxy_service = _resolve_proxy_service()
    get_or_create_fingerprint_uc = GetOrCreateUserFingerprintUseCase(
        uow=uow, generator=FingerprintGenerator(),
    )
    return create_agent(
        results_saver=SaveJobApplicationsUseCase(uow),
        consume_credits_use_case=ConsumeAiCreditsUseCase(uow),
        get_ignored_hashes_use_case=GetIgnoredHashesUseCase(uow),
        file_storage=self.file_storage_port,
        encryption_service=self.encryption_port,
        get_agent_state_use_case=GetAgentStateUseCase(uow),
        create_agent_state_use_case=CreateAgentStateForSearchUseCase(uow),
        is_agent_killed_for_search_use_case=IsAgentKilledForSearchUseCase(uow),
        complete_agent_run_use_case=CompleteAgentRunUseCase(uow),
        heartbeat_use_case=HeartbeatAgentForSearchUseCase(uow),
        set_search_status_use_case=SetSearchStatusUseCase(uow),
        get_daily_stats_use_case=GetDailyStatsUseCase(uow),
        cleanup_unsubmitted_use_case=CleanupUnsubmittedJobsUseCase(uow),
        get_or_create_fingerprint_use_case=get_or_create_fingerprint_uc,
        proxy_service=proxy_service,
    )

# AFTER
@cached_property
def _agent_service(self):
    uowf = self.uow_factory                      # pass the FACTORY, not a resolved instance
    proxy_service = _resolve_proxy_service()
    get_or_create_fingerprint_uc = GetOrCreateUserFingerprintUseCase(
        uow_factory=uowf, generator=FingerprintGenerator(),
    )
    return create_agent(
        results_saver=SaveJobApplicationsUseCase(uowf),
        consume_credits_use_case=ConsumeAiCreditsUseCase(uowf),
        get_ignored_hashes_use_case=GetIgnoredHashesUseCase(uowf),
        file_storage=self.file_storage_port,
        encryption_service=self.encryption_port,
        get_agent_state_use_case=GetAgentStateUseCase(uowf),
        create_agent_state_use_case=CreateAgentStateForSearchUseCase(uowf),
        is_agent_killed_for_search_use_case=IsAgentKilledForSearchUseCase(uowf),
        complete_agent_run_use_case=CompleteAgentRunUseCase(uowf),
        heartbeat_use_case=HeartbeatAgentForSearchUseCase(uowf),
        set_search_status_use_case=SetSearchStatusUseCase(uowf),
        get_daily_stats_use_case=GetDailyStatsUseCase(uowf),
        cleanup_unsubmitted_use_case=CleanupUnsubmittedJobsUseCase(uowf),
        get_or_create_fingerprint_use_case=get_or_create_fingerprint_uc,
        proxy_service=proxy_service,
    )
```

> Note: `create_agent`, `MasterAgent`, and all three workers need **zero** edits — they receive
> use-case *instances* and only ever call `.execute()`. Confirmed by reading `create_agent.py`:
> it passes the injected instances straight through and never touches `.uow`.

### 6.2 `agent_runner`

```python
# BEFORE
@cached_property
def agent_runner(self):
    from auto_apply_app.infrastructures.agent.runner import AgentRunner
    uow = self.uow_factory()
    return AgentRunner(
        agent_service=self._agent_service,
        broker=self.progress_broker,
        load_start_ctx=LoadStartRunContextUseCase(uow),
        load_resume_ctx=LoadResumeRunContextUseCase(uow),
    )

# AFTER
@cached_property
def agent_runner(self):
    from auto_apply_app.infrastructures.agent.runner import AgentRunner
    uowf = self.uow_factory
    return AgentRunner(
        agent_service=self._agent_service,
        broker=self.progress_broker,
        load_start_ctx=LoadStartRunContextUseCase(uowf),
        load_resume_ctx=LoadResumeRunContextUseCase(uowf),
    )
```

### 6.3 Controller factories — the pattern

For every controller `@property`: delete the `uow = self.uow_factory()` line and replace each
`SomeUseCase(uow)` with `SomeUseCase(self.uow_factory)`. For use cases with extra dependencies,
keep those args unchanged and in the same position (the `uow`/`uow_factory` field stays first):

```python
# BEFORE (agent_controller)
@property
def agent_controller(self) -> AgentController:
    uow = self.uow_factory()
    return AgentController(
        start_agent_use_case=StartJobSearchAgentUseCase(uow, self._dispatcher),
        resume_agent_use_case=ResumeJobApplicationUseCase(uow, self._dispatcher),
        kill_agent_use_case=KillJobSearchUseCase(uow, self._agent_service),
        get_jobs_for_review_use_case=GetJobsForReviewUseCase(uow),
        update_cover_letter_use_case=UpdateCoverLetterUseCase(uow),
        approve_job_use_case=ApproveJobUseCase(uow),
        discard_job_use_case=DiscardJobUseCase(uow),
        list_recent_searches_use_case=ListRecentSearchesUseCase(uow),
        get_search_status_use_case=GetSearchStatusUseCase(uow),
        presenter=self.agent_presenter,
        job_presenter=self.job_presenter,
        search_presenter=self.search_presenter,
    )

# AFTER
@property
def agent_controller(self) -> AgentController:
    return AgentController(
        start_agent_use_case=StartJobSearchAgentUseCase(self.uow_factory, self._dispatcher),
        resume_agent_use_case=ResumeJobApplicationUseCase(self.uow_factory, self._dispatcher),
        kill_agent_use_case=KillJobSearchUseCase(self.uow_factory, self._agent_service),
        get_jobs_for_review_use_case=GetJobsForReviewUseCase(self.uow_factory),
        update_cover_letter_use_case=UpdateCoverLetterUseCase(self.uow_factory),
        approve_job_use_case=ApproveJobUseCase(self.uow_factory),
        discard_job_use_case=DiscardJobUseCase(self.uow_factory),
        list_recent_searches_use_case=ListRecentSearchesUseCase(self.uow_factory),
        get_search_status_use_case=GetSearchStatusUseCase(self.uow_factory),
        presenter=self.agent_presenter,
        job_presenter=self.job_presenter,
        search_presenter=self.search_presenter,
    )
```

Apply the identical pattern to `job_offer_controller` and `agent_state_controller` in commit 1,
and to the remaining five controllers in commit 2.

### 6.4 Keyword call sites — rename `uow=` → `uow_factory=`

Any construction that passes `uow` **by keyword** must be renamed (the field name changed).
Known keyword sites in `container.py` — verify and update each:

- `GetOrCreateUserFingerprintUseCase(uow=..., generator=...)` → `uow_factory=...` (commit 1)
- `RegisterUserUseCase(uow=..., ...)` (commit 2)
- `RequestPasswordResetUseCase(uow=..., ...)` (commit 2)
- `ConfirmPasswordResetUseCase(uow=..., ...)` (commit 2)
- `VerifyCodeUseCase(uow=..., ...)` (commit 2)
- `ResendVerificationEmailUseCase(uow=..., ...)` (commit 2)
- `RequestEmailChangeUseCase(uow=..., ...)` (commit 2)
- `ConfirmEmailChangeUseCase(uow=..., ...)` (commit 2)
- `FreeSearchUseCase(uow=..., fake_agent=...)` (commit 2)

Positional sites (e.g. `LoginUserUseCase(self.password_service, self.token_provider, uow)`) keep
working as long as the `uow`→`uow_factory` field stays in the same position; just pass
`self.uow_factory` instead of `uow`.

### 6.5 Repo-wide safety net (run after each commit)

The container is the main composition root, but grep for every converted class name to catch any
other construction site (tests, scripts, `worker_main.py`, etc.) and update it:

```bash
# For each converted class, find all construction sites:
grep -rn "IsAgentKilledForSearchUseCase(" auto_apply_app/ tests/
# ...repeat per class, or:
grep -rEn "\b(Heartbeat|IsAgentKilled|CreateAgentStateForSearch|GetAgentState|GetAgentLiveness|RequestAgentShutdown|SaveJobApplications|ConsumeAiCredits|GetIgnoredHashes|SetSearchStatus|GetDailyStats|CleanupUnsubmittedJobs|CompleteAgentRun|GetOrCreateUserFingerprint|LoadStartRunContext|LoadResumeRunContext)UseCase\(" auto_apply_app/ tests/
```

(`worker_main.py` is expected to go through `create_worker_application()` → `Application.agent_runner`,
so it should NOT construct use cases directly — but confirm with the grep.)

---

## 7. DO NOT (these are the wrong fixes)

- **Do not remove `@cached_property` from `_agent_service`.** `MasterAgent.kill_job_search`
  relies on `_active_workers` living on that one singleton instance; a fresh agent service per
  access would make kill unable to find live workers. (Also, un-caching wouldn't help — the
  three workers of a *single* run share one instance regardless.) Likewise leave `progress_broker`
  and `_dispatcher` as `@cached_property`.
- **Do not put a lock/mutex/semaphore around the UoW or the session.** That serializes all worker
  DB access, throws away the parallelism the design exists for, and invites deadlocks.
- **Per-use-case-*instance* isolation is not enough.** The single `heartbeat` instance is shared
  by all three workers; isolation must be per-*call* (a fresh session per `execute()`), which is
  exactly what the factory gives.
- **Do not reintroduce a shared or long-lived session**, and **do not switch off `NullPool`** in
  `session.py`. `NullPool` is required for PgBouncer compatibility (see the comments there).
- **Do not "optimize away" connection churn by caching sessions.** See §8.

---

## 8. Known tradeoff (accept it; do not try to be clever)

`session.py` uses `NullPool` (correct, because PgBouncer is the pool manager). With per-call
sessions, every `self.uow_factory()` opens and closes a fresh asyncpg connection, and heartbeats
fire frequently (every node entry + inside loops). So the fix increases the number of
short-lived connections through PgBouncer. **This is correct and safe to ship.** If connection-open
latency ever becomes a measured problem, the right move is a dedicated small-pool engine for the
worker process, or coalescing heartbeats — **not** going back to a shared session. Do not
pre-optimize this now.

`expire_on_commit=False` is already set in the session factory, so entities returned out of an
`async with` block (e.g. the `StartRunContext`/`ResumeRunContext` domain objects) stay usable
after the session closes. The fix does not change this.

---

## 9. Explicitly OUT OF SCOPE (note only; do not change in this PR)

- **Double-commit pattern.** Several use cases call `await uow.commit()` explicitly *and* the
  UoW `__aexit__` auto-commits on clean exit. Harmless and pre-existing; leave it. Separate ticket.
- **`MasterAgent._progress_callback` / `_active_workers` are per-run mutable state on a
  process-singleton.** Two *concurrent local runs* (`LocalDispatcher` in dev/MEMORY) would clobber
  them. Not triggered in production (`CloudRunJobsDispatcher` runs one process per dispatch). Flag
  for a future ticket; it is not part of this race fix.
- **The `ai_model` provider-selection bug** (provider stored as `"chatgpt"` not matching
  `["gpt","openai"]`, silently falling back to Gemini) — already fixed separately.
- **Gemini 403 / GCP billing ("Lightning dunning") issue** — separate, unrelated to this change.

---

## 10. Verification checklist

Run all of these before opening the PR (and re-run static checks after each commit).

**Static / structural**

- [ ] `grep -rn "self\.uow\b" auto_apply_app/application/use_cases/` returns **zero** matches
      after full migration. (The regex word boundary means `self.uow_factory()` does **not**
      match, so any hit is a leftover `self.uow` that was missed.)
- [ ] No use-case module still declares `uow: UnitOfWork`
      (`grep -rn "uow: UnitOfWork" auto_apply_app/application/use_cases/` → zero after commit 2;
      after commit 1, only the deferred web files remain).
- [ ] No `uow = self.uow_factory()` local remains in any converted controller property.
      (After commit 1: only the five deferred web controllers still have it; after commit 2: zero.)
- [ ] The §6.5 repo-wide grep shows every construction site passes `self.uow_factory`
      (or `uow_factory=`), never a resolved instance.
- [ ] `UnitOfWorkFactory` type alias added and imported where used.

**Boot / wiring smoke test** (proves every constructor signature matches its call site)

- [ ] Build an `Application` and access every controller property plus `_agent_service` and
      `agent_runner` without raising `TypeError`. Sketch:
      ```python
      app = create_worker_application()   # or the test container factory
      _ = app.agent_runner                # forces _agent_service + Load*RunContext construction
      # in the API test context, also touch each controller property:
      for name in ("user_controller","auth_controller","subscription_controller",
                   "agent_controller","job_offer_controller","prefrences_controller",
                   "agent_state_controller","free_search_controller"):
          getattr(app, name)
      ```
- [ ] Both process entry points construct: the API app **and** `create_worker_application()`.

**Behavioral**

- [ ] Existing web/API test suite is green (the per-request path must be unchanged).
- [ ] **Concurrency regression test** (new): drive `HeartbeatAgentForSearchUseCase.execute` and
      `IsAgentKilledForSearchUseCase.execute` concurrently for the same `search_id`, constructed
      via the real `uow_factory`, e.g.:
      ```python
      import asyncio
      hb = HeartbeatAgentForSearchUseCase(uow_factory)
      kb = IsAgentKilledForSearchUseCase(uow_factory)
      await asyncio.gather(*[
          coro
          for _ in range(20)
          for coro in (hb.execute(search_id), kb.execute(user_id, search_id))
      ])
      ```
      Assert **no** `InterfaceError` / `ResourceClosedError` / `PendingRollbackError` /
      `IllegalStateChangeError` is raised or logged, and every result is a success `Result`.
      (This is the direct regression guard for the bug; it should fail on `main` and pass after
      the fix.)
- [ ] A real 3-worker agent run (staging) produces a clean log with none of the §1 error classes
      during the scrape phase.

---

## 11. One-paragraph summary for the PR description

> The agent run shared a single `SqlAlchemyUnitOfWork` instance across the three parallel worker
> tasks (it was resolved once in the `@cached_property` `_agent_service` and passed to every
> use case). Because `UnitOfWork.__aenter__` stores the session and repos on `self`, concurrent
> `_beat()`/`_is_killed()` calls overwrote each other's session and closed transactions other
> tasks were mid-flight on — surfacing as asyncpg "another operation is in progress" and a
> cascade of SQLAlchemy transaction-state errors. Fix: inject the existing `uow_factory`
> (`Callable[[], UnitOfWork]`) into use cases instead of a resolved instance, and open a fresh
> UoW per `execute()` via `async with self.uow_factory()`. Migrated all use cases to this one
> pattern for consistency; no changes to workers, the master, or transaction boundaries.
