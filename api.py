import os
import threading
import concurrent.futures
import uuid
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Simple in-memory cache: key -> (value, expires_at)
_cache: dict = {}

_CACHE_TTLS = {
    "embed-url":    3600,   # 1 hour  — project URL never changes
    "metrics":       300,   # 5 min
    "recent-runs":   120,   # 2 min   — most time-sensitive
}
_CACHE_TTL_DEFAULT = 180

def _cache_get(key):
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None

def _cache_set(key, value):
    prefix = key.split("-")[0] if "-" in key else key
    ttl = _CACHE_TTLS.get(key) or _CACHE_TTLS.get(prefix) or _CACHE_TTL_DEFAULT
    _cache[key] = (value, time.time() + ttl)

load_dotenv()

app = FastAPI(title="PW PDP Optimisation Agent")
app.mount("/static", StaticFiles(directory="static"), name="static")

# In-memory run registry: run_id -> status dict
_runs: dict[str, dict] = {}


def _warm_cache():
    """Pre-fetch LangSmith data in parallel so first dashboard open is instant."""
    import urllib.request
    import os
    time.sleep(4)
    port = int(os.getenv("PORT", 8000))
    paths = ["/api/langsmith/metrics", "/api/langsmith/recent-runs?limit=20", "/api/langsmith/embed-url"]

    def _fetch(path):
        try:
            urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=120)
        except Exception:
            pass

    threads = [threading.Thread(target=_fetch, args=(p,), daemon=True) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

from database import init_db
init_db()

threading.Thread(target=_warm_cache, daemon=True).start()


class AuditRequest(BaseModel):
    url: str


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.post("/api/audit")
def start_audit(req: AuditRequest):
    run_id = str(uuid.uuid4())
    _runs[run_id] = {
        "id": run_id,
        "url": req.url,
        "status": "queued",
        "stage": None,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "error": None,
        "result": None,
    }
    thread = threading.Thread(target=_run_pipeline, args=(run_id, req.url), daemon=True)
    thread.start()
    return {"run_id": run_id}


@app.get("/api/audit/{run_id}/status")
def get_status(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Return everything except the full result payload (use /result for that)
    return {k: v for k, v in run.items() if k != "result"}


@app.get("/api/audit/{run_id}/result")
def get_result(run_id: str):
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] != "complete":
        raise HTTPException(status_code=202, detail="Audit not complete yet")
    return run["result"]


@app.get("/api/audits")
def list_audits():
    from database import get_all_audits
    return get_all_audits()


@app.get("/api/langsmith/embed-url")
def get_langsmith_embed_url():
    cached = _cache_get("embed-url")
    if cached:
        return cached
    import os
    from langsmith import Client
    client = Client(api_key=os.getenv("LANGCHAIN_API_KEY"))
    project_name = os.getenv("LANGCHAIN_PROJECT", "pw-pdp-optimisation-agent")
    try:
        projects = list(client.list_projects())
        project = next((p for p in projects if p.name == project_name), None)
        if not project:
            return {"error": "Project not found", "embed_url": None, "project_name": project_name}
        result = {"embed_url": f"https://smith.langchain.com/o/{project.tenant_id}/projects/p/{project.id}",
                  "project_name": project_name, "project_id": str(project.id)}
        _cache_set("embed-url", result)
        return result
    except Exception as e:
        return {"embed_url": "https://smith.langchain.com", "project_name": project_name, "error": str(e)}


@app.get("/api/langsmith/recent-runs")
def get_recent_runs(limit: int = 20):
    cache_key = f"recent-runs-{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    import os
    from langsmith import Client
    client = Client(api_key=os.getenv("LANGCHAIN_API_KEY"))
    project_name = os.getenv("LANGCHAIN_PROJECT", "pw-pdp-optimisation-agent")
    try:
        runs = list(client.list_runs(project_name=project_name, execution_order=1, limit=min(limit, 20)))
        result = []
        for run in runs:
            latency = None
            if run.end_time and run.start_time:
                latency = round((run.end_time - run.start_time).total_seconds(), 1)
            result.append({
                "id": str(run.id),
                "name": run.name or "",
                "url": (run.inputs or {}).get("url", ""),
                "status": run.status,
                "started_at": run.start_time.isoformat() if run.start_time else None,
                "latency_s": latency,
                "total_tokens": run.total_tokens or 0,
                "prompt_tokens": run.prompt_tokens or 0,
                "completion_tokens": run.completion_tokens or 0,
                "total_cost": round(float(run.total_cost or 0), 5),
                "overall_score": (run.outputs or {}).get("overall_score"),
                "error": run.error,
                "trace_url": f"https://smith.langchain.com/public/{run.id}/r",
            })
        payload = {"runs": result}
        _cache_set(cache_key, payload)
        return payload
    except Exception as e:
        return {"runs": [], "error": str(e)}


@app.get("/api/langsmith/metrics")
def get_metrics():
    cached = _cache_get("metrics")
    if cached:
        return cached
    import os
    from langsmith import Client
    client = Client(api_key=os.getenv("LANGCHAIN_API_KEY"))
    project_name = os.getenv("LANGCHAIN_PROJECT", "pw-pdp-optimisation-agent")
    try:
        runs = sorted(
            list(client.list_runs(project_name=project_name, execution_order=1, limit=20)),
            key=lambda r: r.start_time,
        )

        def _lat(r):
            if r.end_time and r.start_time:
                return round((r.end_time - r.start_time).total_seconds(), 1)
            return None

        # Pair each reflexion_loop with the rewriter that immediately follows it
        # (they run sequentially: reflexion_loop → rewriter per audit)
        latency_trend = []
        token_trend, cost_trend = [], []
        step_stats = {}
        used_ids = set()

        reflexion_runs = [r for r in runs if r.name == "reflexion_loop"]
        rewriter_runs  = [r for r in runs if r.name == "rewriter"]

        for ref in reflexion_runs:
            # Find the earliest rewriter that starts after this reflexion_loop ends
            ref_end = ref.end_time or ref.start_time
            paired = next(
                (rw for rw in rewriter_runs
                 if rw.id not in used_ids and rw.start_time >= ref_end),
                None
            )
            label = ref.start_time.strftime("%d %b %H:%M")
            ref_lat = _lat(ref)
            rw_lat  = _lat(paired) if paired else None
            overall = round(ref_lat + (rw_lat or 0), 1) if ref_lat is not None else None
            latency_trend.append({
                "label": label,
                "reflexion_loop": ref_lat,
                "rewriter": rw_lat,
                "overall": overall,
            })
            if paired:
                used_ids.add(paired.id)

        # Build token_trend from SQLite (persistent, accurate, no LangSmith dependency)
        from database import get_all_audits as _get_all_audits
        db_audits = sorted(_get_all_audits(), key=lambda a: a["run_at"])[-20:]
        import datetime as _dt
        for a in db_audits:
            tok = a.get("scores_json", {}).get("_tokens") or {}
            try:
                label = _dt.datetime.fromisoformat(a["run_at"]).strftime("%d %b %H:%M")
            except Exception:
                label = a["run_at"][:16]
            token_trend.append({
                "label": label,
                "prompt": tok.get("prompt", 0),
                "completion": tok.get("completion", 0),
            })

        # Cost + step stats still from LangSmith
        for r in runs:
            if not r.start_time:
                continue
            label = r.start_time.strftime("%d %b %H:%M")
            cost_trend.append({"label": label, "cost": round(float(r.total_cost or 0), 5)})
            n = r.name or "unknown"
            if n not in step_stats:
                step_stats[n] = {"name": n, "calls": 0, "tokens": 0, "cost": 0.0, "latency_ms": 0, "errors": 0}
            step_stats[n]["calls"] += 1
            step_stats[n]["tokens"] += r.total_tokens or 0
            step_stats[n]["cost"] += float(r.total_cost or 0)
            if r.end_time and r.start_time:
                step_stats[n]["latency_ms"] += int((r.end_time - r.start_time).total_seconds() * 1000)
            if r.error:
                step_stats[n]["errors"] += 1

        steps = [{"name": n, "calls": v["calls"],
                  "avg_tokens": round(v["tokens"] / v["calls"]) if v["calls"] else 0,
                  "avg_latency_ms": round(v["latency_ms"] / v["calls"]) if v["calls"] else 0,
                  "avg_cost": round(v["cost"] / v["calls"], 5) if v["calls"] else 0,
                  "error_rate": round(v["errors"] / v["calls"] * 100, 1) if v["calls"] else 0,
                  "total_cost": round(v["cost"], 4)}
                 for n, v in step_stats.items()]

        total_cost = sum(float(r.total_cost or 0) for r in runs)
        total_tokens = sum(
            (a.get("scores_json", {}).get("_tokens") or {}).get("total", 0)
            for a in db_audits
        )
        errors = sum(1 for r in runs if r.error)
        n = len(runs)
        payload = {
            "summary": {
                "total_runs": n, "total_cost": round(total_cost, 4),
                "total_tokens": total_tokens,
                "avg_cost_per_run": round(total_cost / n, 4) if n else 0,
                "avg_tokens_per_run": round(total_tokens / n) if n else 0,
                "success_rate": round((n - errors) / n * 100, 1) if n else 0,
                "error_rate": round(errors / n * 100, 1) if n else 0,
            },
            "token_trend": token_trend,
            "cost_trend": cost_trend,
            "latency_trend": latency_trend,
            "steps": steps,
        }
        _cache_set("metrics", payload)
        return payload
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/langsmith/runs/{run_id}/spans")
def get_run_spans(run_id: str):
    import os
    from langsmith import Client
    client = Client(api_key=os.getenv("LANGCHAIN_API_KEY"))
    project_name = os.getenv("LANGCHAIN_PROJECT", "pw-pdp-optimisation-agent")
    try:
        parent = client.read_run(run_id)
        trace_id = str(parent.trace_id) if parent.trace_id else run_id
        # Fetch all spans in the trace, then exclude the root run itself
        all_in_trace = list(client.list_runs(trace_id=trace_id))
        spans = [s for s in all_in_trace if str(s.id) != run_id]
        parent_start = parent.start_time
        result = []
        for s in spans:
            offset = int((s.start_time - parent_start).total_seconds() * 1000) if s.start_time and parent_start else 0
            duration = int((s.end_time - s.start_time).total_seconds() * 1000) if s.end_time and s.start_time else 0
            result.append({"id": str(s.id), "name": s.name, "run_type": s.run_type,
                           "status": s.status, "offset_ms": offset, "duration_ms": duration,
                           "prompt_tokens": s.prompt_tokens or 0, "completion_tokens": s.completion_tokens or 0,
                           "total_tokens": s.total_tokens or 0, "total_cost": round(s.total_cost or 0, 5),
                           "error": s.error})
        result.sort(key=lambda x: x["offset_ms"])
        total_ms = int((parent.end_time - parent.start_time).total_seconds() * 1000) if parent.end_time and parent.start_time else 0
        return {"run_id": run_id, "total_ms": total_ms, "total_tokens": parent.total_tokens or 0,
                "total_cost": round(parent.total_cost or 0, 4), "spans": result}
    except Exception as e:
        return {"spans": [], "error": str(e)}



@app.delete("/api/audits/{audit_id}")
def delete_audit(audit_id: int):
    from database import delete_audit as db_delete
    deleted = db_delete(audit_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Audit not found")
    return {"deleted": True}


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: int):
    from database import get_audit_by_id
    record = get_audit_by_id(audit_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audit not found")
    return record


def _set_stage(run_id: str, stage: str):
    if run_id in _runs:
        _runs[run_id]["stage"] = stage
        _runs[run_id]["status"] = "running"


PIPELINE_TIMEOUT = 900  # 15 minutes hard limit

def _start_watchdog(run_id: str):
    """Mark run as error after PIPELINE_TIMEOUT if still running."""
    def _watch():
        import time as _t
        _t.sleep(PIPELINE_TIMEOUT)
        run = _runs.get(run_id)
        if run and run.get("status") not in ("complete", "error"):
            run["status"] = "error"
            run["error"] = f"Pipeline timed out after {PIPELINE_TIMEOUT}s"
            run["finished_at"] = datetime.now(timezone.utc).isoformat()
            print(f"  ⚠ Run {run_id} timed out — marked as error")
    threading.Thread(target=_watch, daemon=True).start()

def _run_pipeline(run_id: str, url: str):
    _start_watchdog(run_id)
    try:
        import concurrent.futures
        from scraper import scrape_pdp
        from layers.actor import RUBRIC
        from layers.rewriter import run_rewriter
        from database import init_db, insert_audit
        from utils.token_store import set_current_run, get_tokens
        import main as _main

        init_db()
        set_current_run(run_id)  # all LLM calls in this thread accumulate tokens here

        _set_stage(run_id, "Scraping target page")
        target_json = scrape_pdp(url)

        _set_stage(run_id, "Running Reflexion audit")

        # Run the rewriter concurrently with the reflexion loop:
        # pass1_ready fires as soon as Pass 1 actor finishes, letting the
        # rewriter start while Pass 2 (if needed) runs in parallel.
        pass1_ready = threading.Event()
        pass1_holder = {}

        def _run_loop():
            # Thread-local run_id must be set in each worker thread
            set_current_run(run_id)
            return _main.reflexion_loop(
                target_json, {}, RUBRIC,
                _pass1_ready=pass1_ready, _pass1_holder=pass1_holder,
            )

        def _rewrite_when_ready():
            set_current_run(run_id)
            pass1_ready.wait()
            return run_rewriter(target_json, pass1_holder["audit"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            loop_future   = ex.submit(_run_loop)
            rewrite_future = ex.submit(_rewrite_when_ready)

            audit, evaluation, reflection_used = loop_future.result()

            _set_stage(run_id, "Rewriting page copy")
            if reflection_used:
                # Pass 2 changed the audit — discard concurrent rewrite and redo
                print("  Pass 2 triggered — rewriting from corrected audit")
                rewrite = run_rewriter(target_json, audit)
            else:
                rewrite = rewrite_future.result()

        scores = _main._extract_scores(audit)
        tokens = get_tokens(run_id)
        scores_with_tokens = {**scores, "_tokens": tokens}  # stored in same JSON blob
        db_row_id = insert_audit(
            url=url,
            target_json=target_json,
            competitor_json={},
            audit_json=audit,
            rewrite_json=rewrite,
            scores_json=scores_with_tokens,
        )

        product_name = target_json.get("structured", {}).get("product_name", "") or \
                       target_json.get("structured", {}).get("h1", "") or ""

        result = {
            "db_id": db_row_id,
            "url": url,
            "product_name": product_name,
            "run_at": datetime.now(timezone.utc).isoformat(),
            "scores": scores,
            "audit": audit,
            "rewrite": rewrite,
            "atf_screenshot": target_json.get("atf_screenshot_base64"),
            "structured": target_json.get("structured", {}),
        }

        _runs[run_id]["status"] = "complete"
        _runs[run_id]["stage"] = "Done"
        _runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
        _runs[run_id]["result"] = result
        _runs[run_id]["tokens"] = get_tokens(run_id)

        # Invalidate LangSmith caches so the next dashboard open shows this run
        _cache.pop("metrics", None)
        _cache.pop("recent-runs-20", None)

    except Exception as e:
        _runs[run_id]["status"] = "error"
        _runs[run_id]["error"] = str(e)
        _runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
