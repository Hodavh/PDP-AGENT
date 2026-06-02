import json
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from langsmith import traceable

from utils.gemini_client import parse_json_response

load_dotenv()

REWRITER_MODEL = "claude-sonnet-4-6"
REWRITER_FALLBACK_MODEL = "claude-haiku-4-5-20251001"
_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "prompts"))

BRAND_VOICE_EXAMPLES = """
PROTEIN WORKS VOICE — LEARN FROM THESE EXAMPLES:

Good headline (from their actual site):
"The protein shake that actually tastes like a milkshake. Not a compromise."

Good benefit copy:
"69 ingredients. One scoop. Zero excuses."

Good objection handling:
"Yes, it tastes good. We know you've been disappointed before. Try it anyway."

Good CTA context:
"Most people feel it in week two. Some in day three."

What makes this voice:
- Short sentences. Sometimes fragments.
- Acknowledges the customer's scepticism directly
- Specific numbers over vague claims
- Confident, not corporate
- Talks to the customer like they are smart

What to AVOID:
- "Elevate your wellness journey" — wellness-speak
- "Premium quality ingredients" — meaningless
- "Unlock your potential" — cliché
- Long sentences with multiple clauses
- Passive voice
- Exclamation marks everywhere

IMPORTANT: The examples above are STYLE REFERENCES ONLY. Do not copy any specific numbers,
claims, or product details from them (e.g. "69 ingredients" applies to a different product —
never use it unless verified in the source data for THIS product).
"""

REWRITER_SYSTEM = """You are a UK food supplement copywriter writing for Protein Works.

BRAND VOICE — match this exactly:
""" + BRAND_VOICE_EXAMPLES + """
You write product page copy that:
1. Complies fully with ASA CAP Code rules 15.1-15.9 and MHRA enforcement guidance
2. Uses only permitted nutrition and health claims from the GB Nutrition and Health Claims Register
3. Never makes disease prevention, treatment, or cure claims
4. Flags every compliance-sensitive sentence with [HUMAN REVIEW REQUIRED] at the end of that sentence

SELECTED PERMITTED CLAIMS FROM GB NHC REGISTER (use verbatim):
- Protein: "Protein contributes to the maintenance and growth of muscle mass"
- Protein: "Protein contributes to a growth in muscle mass"
- Protein: "Protein contributes to the maintenance of normal bones"
- Vitamin C: "Vitamin C contributes to the normal function of the immune system"
- Vitamin C: "Vitamin C contributes to the reduction of tiredness and fatigue"
- Vitamin D: "Vitamin D contributes to the normal function of the immune system"
- Vitamin D: "Vitamin D contributes to the maintenance of normal bones"
- Magnesium: "Magnesium contributes to the reduction of tiredness and fatigue"
- Magnesium: "Magnesium contributes to normal muscle function"
- Creatine: "Creatine increases physical performance in successive bursts of short-term, high intensity exercise"
- Calcium: "Calcium is needed for the maintenance of normal bones"
- Iron: "Iron contributes to the reduction of tiredness and fatigue"
- Collagen: No authorised claim — flag all collagen efficacy statements [HUMAN REVIEW REQUIRED]
- Greens/superfoods: No authorised claim for the blend — flag all efficacy statements [HUMAN REVIEW REQUIRED]

PROHIBITED LANGUAGE (never use):
- "boost immunity", "supports immunity" (unless using exact registered wording)
- "anti-inflammatory", "detox", "cleanse"
- Any reference to treating, preventing, or curing a condition
- "clinically proven" unless accompanied by a specific clinical reference

This copy is a DRAFT for human review only. It must never be auto-published."""


def _extract_top_recs(audit: dict) -> list[dict]:
    """Pull the top 5 recommendations from the flat ranked recommendations list."""
    all_recs = audit.get("recommendations", [])
    sorted_recs = sorted(all_recs, key=lambda r: r.get("priority_rank", 99))
    return [
        {
            "dimension": r.get("dimension", "").replace("_", " ").title(),
            "text": r.get("finding") or r.get("text", ""),
            "triage": r.get("triage", ""),
        }
        for r in sorted_recs[:5]
    ]


@traceable(name="rewriter", metadata={"model": REWRITER_MODEL})
def run_rewriter(target_json: dict, audit: dict) -> dict:
    template = _jinja.get_template("rewriter_prompt.j2")

    compliance_flags = audit.get("compliance_flags", [])

    # Slim target_json for the rewriter — no binary fields
    target_slim = {
        k: v for k, v in target_json.items()
        if k not in ("atf_screenshot_base64", "image_urls", "gallery_image_urls", "gallery_image_alts", "full_markdown")
    }

    user_text = template.render(
        target_json=json.dumps(target_slim, indent=2),
        top_recs=_extract_top_recs(audit),
        compliance_flags=compliance_flags,
        compliance_rules=REWRITER_SYSTEM,
    )

    import time as _time
    message = None
    active_model = REWRITER_MODEL
    for _attempt in range(1, 4):
        try:
            message = _client.messages.create(
                model=active_model,
                max_tokens=8192,
                system=REWRITER_SYSTEM,
                messages=[{"role": "user", "content": user_text}],
            )
            if active_model != REWRITER_MODEL:
                print(f"  ✓ Rewriter succeeded on fallback model: {active_model}")
            break
        except Exception as _e:
            _err = str(_e).lower()
            if any(x in _err for x in ["529", "overloaded", "rate_limit", "429"]):
                if active_model != REWRITER_FALLBACK_MODEL:
                    print(f"  Rewriter overloaded — switching to fallback {REWRITER_FALLBACK_MODEL}")
                    active_model = REWRITER_FALLBACK_MODEL
                elif _attempt < 3:
                    _delay = 20 * _attempt
                    print(f"  Rewriter fallback error (attempt {_attempt}/3) — waiting {_delay}s")
                    _time.sleep(_delay)
                else:
                    raise
            else:
                raise

    try:
        from utils.token_store import add_tokens
        u = message.usage
        print(f"  Rewriter tokens — input: {u.input_tokens:,}  output: {u.output_tokens:,}")
        add_tokens(u.input_tokens, u.output_tokens)
    except Exception:
        pass

    return parse_json_response(message.content[0].text)
