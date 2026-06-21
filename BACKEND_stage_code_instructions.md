# Backend — Agent Status `stage_code` Internationalization

**Repo:** Python / FastAPI / LangGraph agent backend.

## Goal

The agent currently emits English progress strings in the `stage` field of every
`_emit` call (e.g. `"Searching for Jobs"`). The frontend can't translate these.

We are adding a language-neutral **`stage_code`** to every emit. The frontend
will translate the code into the user's language. The English `stage` string
**stays** as a fallback. Control-flow logic stays driven by `status`, never by
the English text.

`stage_code` is **additive and display-only**. Do not change any `status` value.

---

## 1. Create the stage-code Enum

Create a new file `auto_apply_app/infrastructures/agent/stage_codes.py`.

Use a `str`-mixed `Enum` so it serializes to its plain string value in JSON
automatically (no `.value` needed when dumped by `json.dumps`, and `==` against
a string works).

> ⚠️ Name it `StageCode` — **not** `AgentState`. There is already an `AgentState`
> in this codebase and we must not collide with it.

```python
# auto_apply_app/infrastructures/agent/stage_codes.py
from enum import Enum


class StageCode(str, Enum):
    # --- Worker stages ---
    INITIALIZING_BROWSER = "INITIALIZING_BROWSER"
    NAVIGATING = "NAVIGATING"
    AUTHENTICATING = "AUTHENTICATING"
    SEARCHING = "SEARCHING"
    EXTRACTING_DATA = "EXTRACTING_DATA"
    SUBMITTING = "SUBMITTING"
    CLEANING_UP = "CLEANING_UP"

    # --- Master stages ---
    LAUNCHING_WORKERS = "LAUNCHING_WORKERS"
    GENERATING_LETTERS = "GENERATING_LETTERS"
    DISPATCHING = "DISPATCHING"
    WAITING_REVIEW = "WAITING_REVIEW"
    LAUNCHING_SUBMISSION = "LAUNCHING_SUBMISSION"
    SAVING_RESULTS = "SAVING_RESULTS"

    # --- Terminal states ---
    COMPLETE = "COMPLETE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    NO_JOBS = "NO_JOBS"
```

> **Serialization note:** because of the `str` mix-in, `StageCode.NAVIGATING`
> behaves as the string `"NAVIGATING"`. The existing SSE payload is sent through
> `json.dumps(...)`, which serializes `str`-Enum members to their value. If you
> hit any edge case where a member is not auto-stringified, pass `.value`
> explicitly at that call site — but in the standard path this is not required.

Import it in `master.py` and in each of the four workers:

```python
from auto_apply_app.infrastructures.agent.stage_codes import StageCode
```

---

## 2. Update every `_emit` signature + payload

There are five `_emit` methods: `master.py` and the four workers (APEC,
HelloWork, JobTeaser, WTTJ).

### 2a. All workers — add `stage_code` param + payload field

New signature (worker version):

```python
async def _emit(
    self,
    state: JobApplicationState,
    stage: str,
    status: str = "in_progress",
    error: str = None,
    error_code: str = None,
    stage_code: str = None,   # NEW
):
```

Add to the payload dict:

```python
"stage_code": stage_code,   # NEW
```

### 2b. APEC fix — it is missing `error_code` entirely

The APEC worker's `_emit` is the odd one out: it currently has **no**
`error_code` parameter and **no** `"error_code"` payload field, unlike the other
three workers. Bring it in line:

- Add the `error_code: str = None` parameter.
- Add `"error_code": error_code or ("SYSTEMERROR" if error else None),` to the
  payload (match HelloWork's exact form).
- Then also add `stage_code` per 2a.

Easiest path: copy HelloWork's `_emit` verbatim into APEC, swap the `[HW]` log
prefix for `[APEC]` / `self._source_name`, then add the `stage_code` param +
field.

### 2c. Master — add `stage_code` **and** `count`

The master needs one extra field beyond the workers: `count`, for the dynamic
"Dispatching" message.

```python
async def _emit(
    self,
    state: JobApplicationState,
    stage: str,
    status: str = "in_progress",
    error: str = None,
    error_code: str = None,
    stage_code: str = None,   # NEW
    count: int = None,        # NEW (for DISPATCHING)
):
```

Add both to the payload:

```python
"stage_code": stage_code,   # NEW
"count": count,             # NEW
```

---

## 3. Update every call site with the matching `stage_code`

**Keep the existing English `stage` string at every call site** (it is the
fallback). Only add the `stage_code=` keyword argument.

### Workers (APEC, HelloWork, JobTeaser, WTTJ)

| Current `stage` string                              | Add                                    |
|-----------------------------------------------------|----------------------------------------|
| `"Initializing Browser"` / `"Initializing Secure Browser"` | `stage_code=StageCode.INITIALIZING_BROWSER` |
| `"Navigating to Job Board"`                         | `stage_code=StageCode.NAVIGATING`      |
| `"Authenticating"`                                  | `stage_code=StageCode.AUTHENTICATING`  |
| `"Searching for Jobs"`                              | `stage_code=StageCode.SEARCHING`       |
| `"Extracting Job Data"`                             | `stage_code=StageCode.EXTRACTING_DATA` |
| `"Submitting Applications"`                         | `stage_code=StageCode.SUBMITTING`      |
| `"Cleaning Up"`                                     | `stage_code=StageCode.CLEANING_UP`     |

Apply to **all four** workers. Note APEC uses both `"Initializing Browser"`
(scrape track) and `"Initializing Secure Browser"` (submit track) — both map to
`INITIALIZING_BROWSER`. Same for the other workers' two browser-boot nodes.

### Master (`master.py`)

| Current `stage` string                               | Add                                     |
|------------------------------------------------------|-----------------------------------------|
| `"Launching Search Workers"`                         | `stage_code=StageCode.LAUNCHING_WORKERS`|
| `"AI Generating Cover Letters"`                      | `stage_code=StageCode.GENERATING_LETTERS`|
| `"Waiting for User Review"`                          | `stage_code=StageCode.WAITING_REVIEW`   |
| `"Launching Submission Workers"`                     | `stage_code=StageCode.LAUNCHING_SUBMISSION`|
| `"Saving Final Results"`                             | `stage_code=StageCode.SAVING_RESULTS`   |

### Master — terminal notification nodes

| Node                       | Add                              |
|----------------------------|----------------------------------|
| `completion_notification`  | `stage_code=StageCode.COMPLETE`  |
| `stop_agent_notification`  | `stage_code=StageCode.STOPPED`   |
| `no_jobs_notification`     | `stage_code=StageCode.NO_JOBS`   |
| `error_notification`       | `stage_code=StageCode.FAILED`    |

### Master — the dynamic DISPATCHING emit

In `dispatch_submit`, the current call is:

```python
await self._emit(state, stage=f"Dispatching up to {remaining_quota} submissions")
```

Change to (keep the English fallback, add the code + the count value):

```python
await self._emit(
    state,
    stage=f"Dispatching up to {remaining_quota} submissions",
    stage_code=StageCode.DISPATCHING,
    count=remaining_quota,
)
```

> `remaining_quota` is the remaining **daily application quota** (number of
> applications), which is the correct value to show the user. It is not a worker
> count.

---

## 4. DO NOT change any `status` value — critical

The frontend uses `status` for all control-flow logic. These must remain exactly
as they are today:

- `route_review` / paused emit → `status="paused"`
- completion → `status="finished"`
- stop → `status="killed"`
- no-jobs → `status="no_jobs_found"`
- errors → `status="error"`
- worker "done" → `status` of `"completed"` / `"success"` where already emitted

`stage_code` is purely additive. Do not remove or reword the English `stage`
strings — they remain the fallback.

---

## 5. Contract with the frontend

The `stage_code` **string values** (`"LAUNCHING_WORKERS"`, etc.) are a shared
contract: they must match the keys under `agent.stages.*` in the frontend's
`translation.json` exactly. If you rename a code here, it must be renamed on the
frontend too.
