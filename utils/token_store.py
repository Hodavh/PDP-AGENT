"""
Thread-local token accumulator for tracking LLM usage across a pipeline run.

Usage:
  - Call set_current_run(run_id) at the start of each pipeline thread
  - Call add_tokens(...) inside any LLM wrapper after a response
  - Call get_tokens(run_id) at the end of the pipeline to read the total
"""
import threading

_store: dict = {}
_lock = threading.Lock()
_local = threading.local()


def set_current_run(run_id: str) -> None:
    _local.run_id = run_id


def get_current_run() -> str | None:
    return getattr(_local, "run_id", None)


def add_tokens(prompt: int, completion: int) -> None:
    run_id = get_current_run()
    if not run_id:
        return
    with _lock:
        if run_id not in _store:
            _store[run_id] = {"prompt": 0, "completion": 0}
        _store[run_id]["prompt"] += prompt
        _store[run_id]["completion"] += completion


def get_tokens(run_id: str) -> dict:
    with _lock:
        entry = _store.get(run_id, {"prompt": 0, "completion": 0})
        return {"prompt": entry["prompt"], "completion": entry["completion"],
                "total": entry["prompt"] + entry["completion"]}
