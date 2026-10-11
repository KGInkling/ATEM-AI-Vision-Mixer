# Nonblocking model requests

`director/async_runner.py` provides `AsyncRunner` for LLM-directors task group 2
and its slow-call checkpoint. It accepts a caller's initial result and a positive,
finite timeout in seconds. A request is a no-argument function that returns a
validated result: the future local client will return `ShotIntent`, and the cloud
client will return `ShotPolicy`.

The runner uses one standard-library executor worker. Call `tick()` and `close()`
from the control thread. Model calls run in the worker; only the control thread
publishes results. No model SDK, credential, hardware, or event loop is needed to
use the wrapper. Model clients and application wiring belong to later groups.

## Tick behavior

- The first tick starts a request and returns the supplied initial result.
- While a request is running, later ticks return the retained result. Their
  request functions are not submitted, queued, or executed.
- When a request finishes before its deadline, the next tick accepts that result
  and starts its current request. Completion time is recorded in the worker;
  polling after the deadline does not discard a result that finished in time.
- A request completing at or after the deadline cannot replace the retained
  result. Exceptions also retain it. Deadline notices are logged once per request;
  failures log only the exception type, without response content or a traceback.

The deadline uses monotonic elapsed time, independently of recorded frame PTS.
The caller must validate output inside the request function before returning it.
The generic runner does not parse or partially accept a model response.

## Timeouts and cleanup

Python cannot cancel a running executor thread. On deadline expiry the runner
abandons the response, keeps returning the previous result, and continues
skipping new calls until the worker actually finishes. This bounds work to one
physical request, with no retry queue or growing set of abandoned workers.

Each future SDK client must therefore set its own transport timeout. A permanently
hung transport leaves that runner unavailable, while rules remain the later
layered director's fallback. `close()` is idempotent and returns without waiting;
subsequent ticks raise `RuntimeError`. A running call finishes in the background,
and Python process exit still waits for executor threads. These are standard
[executor cancellation and shutdown semantics](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).

```python
from atem_ai_vision_mixer.director.async_runner import AsyncRunner
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY

runner = AsyncRunner(initial_result=DEFAULT_POLICY, timeout_seconds=2.0)
try:
    # Replace this offline stub with a bounded call that validates its response.
    policy = runner.tick(lambda: DEFAULT_POLICY)
finally:
    runner.close()
```

The existing integration test owner verifies real shadow-controller ticks remain
responsive while a stub sleeps, no request is queued, deadline boundaries and
late-result rejection, delayed polling of timely results, error-content privacy,
recovery, and nonblocking cleanup. Deterministic deadline cases use a controlled
clock and real executor futures. Run without model services or credentials:

A separate local stub check with 50 ms and 250 ms sleeps measured busy-tick p95
of 0.003 ms and 0.001 ms, with one actual request in each run. First submissions
took 0.086 ms and 0.079 ms. These measure the wrapper with sleeping stubs, not
model inference, SDK transport deadlines, or live-production timing.

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_integration.py -q
```
