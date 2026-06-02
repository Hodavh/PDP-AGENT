import json
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from langsmith import traceable

from utils.gemini_client import EVALUATOR_MODEL, call_gemini, parse_json_response

load_dotenv()

_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "prompts"))

EVALUATOR_SYSTEM = (
    "You are a quality assurance auditor. You evaluate PDP audits for accuracy, specificity, "
    "compliance safety, and internal consistency. You output only valid JSON."
)


@traceable(name="evaluator", metadata={"model": EVALUATOR_MODEL})
def run_evaluator(audit: dict, target_json: dict) -> dict:
    target_json_text = {
        k: v for k, v in target_json.items()
        if k not in ("atf_screenshot_base64", "image_urls")
    }
    if "full_markdown" in target_json_text:
        target_json_text["full_markdown"] = target_json_text["full_markdown"][:6000]

    template = _jinja.get_template("evaluator_prompt.j2")
    user_text = template.render(
        audit_json=json.dumps(audit, indent=2),
        target_json=json.dumps(target_json_text, indent=2),
    )

    response_text = call_gemini(
        system_prompt=EVALUATOR_SYSTEM,
        user_message=user_text,
        model_name=EVALUATOR_MODEL,
        max_tokens=16000,
        require_json=True,
        caller="evaluator",
    )

    return parse_json_response(response_text)
