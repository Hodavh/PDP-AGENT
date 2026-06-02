CRITERIA_MESSAGES = {
    "grounded_in_evidence": "not grounded in evidence — cite a specific field from the page data using [DATA: field | 'value'] format",
    "specific_to_page": "not specific to this page — rewrite to reference a concrete element unique to this PDP rather than generic advice",
    "compliant_language": "compliance risk — remove or rephrase any health claim language; use only GB NHC Register wording or flag for human review",
    "justified_impact_score": "impact score not justified — the impact_score must be consistent with the commercial rationale in why_it_matters",
}


def needs_second_pass(evaluation: dict) -> bool:
    """
    Trigger pass 2 only if there are genuine failures.
    Evaluation structure: {rec_1: {criterion: bool}, rec_2: ...}
    """
    fail_count = 0
    has_compliance_failure = False

    for rec_key, criteria in evaluation.items():
        if not isinstance(criteria, dict):
            continue
        for criterion, passed in criteria.items():
            if passed is False:
                fail_count += 1
                if criterion == "compliant_language":
                    has_compliance_failure = True

    print(f"  Evaluation — fail_count: {fail_count}, compliance_failure: {has_compliance_failure}")
    print(f"  needs_second_pass: {has_compliance_failure or fail_count > 3}")

    return has_compliance_failure or fail_count > 3


def count_failures(evaluation: dict) -> int:
    return sum(
        1
        for criteria in evaluation.values()
        if isinstance(criteria, dict)
        for passed in criteria.values()
        if passed is False
    )


def run_reflector(audit: dict, evaluation: dict) -> str:
    critiques = []
    recs = audit.get("recommendations", [])
    rec_by_rank = {f"rec_{r.get('priority_rank')}": r for r in recs if r.get("priority_rank")}

    for rec_key, criteria in evaluation.items():
        if not isinstance(criteria, dict):
            continue
        rec = rec_by_rank.get(rec_key, {})
        dimension = rec.get("dimension", rec_key)
        for criterion, passed in criteria.items():
            if passed is False:
                message = CRITERIA_MESSAGES.get(criterion, f"{criterion} failed")
                critiques.append(f"{dimension} {rec_key}: {message}.")

    return "\n".join(critiques)
