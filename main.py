import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from langsmith import traceable
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

load_dotenv()

console = Console()


@traceable(name="reflexion_loop")
def reflexion_loop(
    target_json: dict,
    competitor_jsons: dict,
    rubric: dict,
    max_passes: int = 2,
    _pass1_ready=None,   # threading.Event — set after Pass 1 actor finishes
    _pass1_holder=None,  # dict — populated with {"audit": pass1_audit}
) -> tuple[dict, dict, str]:
    from layers.actor import run_actor
    from layers.evaluator import run_evaluator
    from layers.reflector import run_reflector, needs_second_pass, count_failures

    reflection = None
    audit = None
    evaluation = {}

    for pass_num in range(1, max_passes + 1):
        console.print(f"    Pass {pass_num}...", end=" ")
        audit = run_actor(target_json, rubric, reflection=reflection, pass_number=pass_num)
        console.print("[green]done[/green]")

        # Signal after Pass 1 actor so the rewriter can start concurrently
        if pass_num == 1 and _pass1_holder is not None:
            _pass1_holder["audit"] = audit
        if pass_num == 1 and _pass1_ready is not None:
            _pass1_ready.set()

        console.print("    Evaluator...", end=" ")
        evaluation = run_evaluator(audit, target_json)
        console.print("[green]done[/green]")
        console.print(f"    Evaluation dict: {evaluation}")

        if not needs_second_pass(evaluation):
            console.print("    [green]All criteria passed — no second pass needed.[/green]")
            break

        if pass_num < max_passes:
            failed_count = count_failures(evaluation)
            console.print(f"    [yellow]{failed_count} criteria failed — generating reflection...[/yellow]")
            reflection = run_reflector(audit, evaluation)
        else:
            console.print("    [yellow]Max passes reached — proceeding with best output.[/yellow]")

    return audit, evaluation, reflection or ""


def _extract_scores(audit: dict) -> dict:
    scores = {}
    dims = audit.get("dimension_scores", {})
    for dim_name, dim_data in dims.items():
        scores[dim_name] = dim_data.get("score", 0)
    scores["overall"] = audit.get("overall_score", 0)
    return scores


def _log_metadata(event: str, data: dict) -> None:
    """Attach a named metadata snapshot to the current LangSmith run tree."""
    try:
        from langsmith import get_current_run_tree
        rt = get_current_run_tree()
        if rt:
            existing = rt.extra or {}
            existing.setdefault("pipeline_events", {})[event] = data
            rt.extra = existing
    except Exception:
        pass


@traceable(
    name="pdp_audit_pipeline",
    tags=["pw-pdp-agent"],
)
def run_pipeline(url: str) -> dict:
    from scraper import scrape_pdp
    from layers.actor import RUBRIC
    from layers.rewriter import run_rewriter
    from database import init_db, insert_audit

    init_db()

    console.rule("[bold blue]PW PDP Optimisation Agent[/bold blue]")

    # Stage 1
    console.print("\n[bold]Stage 1/3[/bold] Scraping target page...")
    target_json = scrape_pdp(url)
    console.print(f"  [green]✓[/green] {url}")
    s = target_json.get("structured", {})
    _log_metadata("scrape_complete", {
        "url": url,
        "scraper_used": target_json.get("scraper_used"),
        "product_name": s.get("product_name", ""),
        "h1": s.get("h1", ""),
        "key_benefits_count": len(s.get("key_benefits", [])),
        "compliance_flags_count": len(s.get("compliance_sensitive_sentences", [])),
    })

    # Stage 2
    console.print(f"\n[bold]Stage 2/3[/bold] Running Reflexion audit (max 2 passes)...")
    audit, evaluation, reflection_used = reflexion_loop(target_json, {}, RUBRIC)
    scores = _extract_scores(audit)
    _log_metadata("audit_complete", {
        "scores": scores,
        "overall_score": audit.get("overall_score"),
        "reflection_triggered": bool(reflection_used),
        "compliance_flags_count": len(audit.get("compliance_flags", [])),
    })

    # Stage 3
    console.print("\n[bold]Stage 3/3[/bold] Rewriting page copy...")
    rewrite = run_rewriter(target_json, audit)
    console.print("  [green]✓[/green] Rewrite complete")
    _log_metadata("rewrite_complete", {
        "assumptions_flagged_count": len(rewrite.get("assumptions_flagged", [])),
    })

    # Send scores as LangSmith feedback so they appear in the feedback chart
    _submit_langsmith_feedback(url, scores, audit)

    # Save
    row_id = insert_audit(
        url=url,
        target_json=target_json,
        competitor_json={},
        audit_json=audit,
        rewrite_json=rewrite,
        scores_json=scores,
    )
    console.print(f"\n  [green]✓[/green] Saved to database (row id: {row_id})")

    result = {
        "url": url,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "audit": audit,
        "evaluation": evaluation,
        "rewrite": rewrite,
        "scores": scores,
        "db_row_id": row_id,
    }

    print_report(result)
    return result


def print_report(result: dict) -> None:
    console.rule("[bold blue]AUDIT REPORT[/bold blue]")

    # Scores table
    console.print()
    table = Table(title="Dimension Scores", box=box.ROUNDED, show_header=True)
    table.add_column("Dimension", style="cyan", min_width=22)
    table.add_column("Score", justify="center", min_width=7)
    table.add_column("Summary", min_width=50)

    dims = result["audit"].get("dimension_scores", {})
    STATUS_ICON = {"pass": "[green]✓[/green]", "fail": "[red]✗[/red]", "partial": "[yellow]~[/yellow]"}
    for dim_name, dim_data in dims.items():
        score = dim_data.get("score", 0)
        reasoning = dim_data.get("score_rationale", dim_data.get("reasoning", ""))[:80]
        colour = "green" if score >= 4 else ("yellow" if score >= 3 else "red")
        table.add_row(dim_name.replace("_", " ").title(), f"[{colour}]{score}/5[/{colour}]", reasoning)

        if "element_checks" in dim_data:
            for el_name, el_result in dim_data["element_checks"].items():
                status_key = el_result.split(" — ")[0].strip().lower() if " — " in el_result else "partial"
                icon = STATUS_ICON.get(status_key, "·")
                detail = el_result.split(" — ")[-1][:60] if " — " in el_result else el_result[:60]
                table.add_row(f"  └ {el_name.replace('_', ' ').title()}", icon, detail)

        if dim_name == "dtc_benchmark" and "sub_scores" in dim_data:
            for sub_name, sub_score in dim_data["sub_scores"].items():
                sub_colour = "green" if sub_score >= 4 else ("yellow" if sub_score >= 3 else "red")
                table.add_row(f"  └ {sub_name.replace('_', ' ').title()}", f"[{sub_colour}]{sub_score}/5[/{sub_colour}]", "")

    overall = result["scores"].get("overall", 0)
    table.add_row("[bold]OVERALL[/bold]", f"[bold]{overall:.1f}/5[/bold]", "")
    console.print(table)

    # Top recommendations
    all_recs = result["audit"].get("recommendations", [])
    top_recs = sorted(all_recs, key=lambda r: r.get("priority_rank", 99))[:5]
    if top_recs:
        console.print(Panel(
            "\n".join(f"{r.get('priority_rank', i+1)}. [{r.get('triage','')[:4]}] {r.get('finding') or r.get('text','')}" for i, r in enumerate(top_recs)),
            title="[bold]Top 5 Recommendations[/bold]",
            border_style="yellow",
        ))

    # Compliance flags
    flagged_claims = result["audit"].get("compliance_flags", [])
    if not flagged_claims:
        console.print(Panel(
            "[green]No compliance risk identified in scraped content.[/green]",
            title="[bold]Claims Compliance[/bold]",
            border_style="green",
        ))
    else:
        lines = []
        for item in flagged_claims:
            lines.append(
                f"[bold]{item.get('risk_level', '').upper()}[/bold]\n"
                f"  Quote:  \"{item.get('verbatim_quote', '')}\"\n"
                f"  Reason: {item.get('risk_reason', '')}"
            )
        console.print(Panel(
            "\n\n".join(lines),
            title="[bold red]Claims Compliance — HUMAN REVIEW REQUIRED[/bold red]",
            border_style="red",
        ))

    # Rewrite preview
    rw = result["rewrite"]
    console.print(Panel(
        f"[bold]Meta title:[/bold] {rw.get('meta_title', '')}\n"
        f"[bold]H1:[/bold] {rw.get('h1', '')}\n"
        f"[bold]Sub-headline:[/bold] {rw.get('sub_headline', '')}\n\n"
        f"[bold]Benefit highlights:[/bold]\n" +
        "\n".join(f"  • {b}" for b in rw.get("benefit_highlights", [])) +
        (f"\n\n[bold]Assumptions flagged for review:[/bold]\n" +
         "\n".join(f"  ⚠ {a}" for a in rw.get("assumptions_flagged", []))
         if rw.get("assumptions_flagged") else ""),
        title="[bold]Suggested Rewrite (DRAFT — Do Not Publish Without Review)[/bold]",
        border_style="blue",
    ))

    console.rule()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        url = "https://www.theproteinworks.com/ai-greens"
        console.print(f"No URL provided — using default: [cyan]{url}[/cyan]")
    else:
        url = sys.argv[1]

    run_pipeline(url)
