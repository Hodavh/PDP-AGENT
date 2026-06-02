import json
import logging
import os
import time

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage

load_dotenv()

logger = logging.getLogger(__name__)

_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
print(f"Using API key: ...{_api_key[-6:] if _api_key else 'NOT FOUND'}")

ACTOR_MODEL     = "gemini-3.5-flash"
EVALUATOR_MODEL = "gemini-3.5-flash"
REFLECTOR_MODEL = "gemini-3.5-flash"

_FALLBACK_CHAIN = ["gemini-2.5-flash", "gemini-3-flash-preview"]

def _make_llm(model_name: str, max_tokens: int, require_json: bool, multimodal: bool = False) -> ChatGoogleGenerativeAI:
    kwargs = dict(
        model=model_name,
        google_api_key=_api_key,
        temperature=0.2,
        max_output_tokens=max_tokens,
    )
    # response_mime_type conflicts with multimodal input on some models — skip it for image calls
    if require_json and not multimodal:
        kwargs["response_mime_type"] = "application/json"
    return ChatGoogleGenerativeAI(**kwargs)


def call_gemini(
    system_prompt: str,
    user_message,           # str  OR  list[dict]  (LangChain multimodal content blocks)
    model_name: str = "gemini-2.5-flash",
    max_tokens: int = 4000,
    max_retries: int = 3,
    require_json: bool = True,
    caller: str = "unknown",
) -> str:
    """
    Single entry point for all Gemini calls via LangChain.
    user_message can be:
      - str  → plain text prompt
      - list → LangChain multimodal content blocks
                [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:..."}}]
    Returns response text.
    Token usage is reported automatically to LangSmith by LangChain.
    """
    base_delay = 10
    active_model = model_name
    _fallback_index = 0  # pointer into _FALLBACK_CHAIN

    # Build the HumanMessage once (content is the same regardless of model)
    human = HumanMessage(content=user_message)

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Gemini call: model={active_model} caller={caller} attempt={attempt}/{max_retries}")
            llm = _make_llm(active_model, max_tokens, require_json, multimodal=isinstance(user_message, list))
            messages = [SystemMessage(content=system_prompt), human]
            # Hard timeout — ChatGoogleGenerativeAI has no native timeout param
            # 360s for large multimodal calls (Actor pass 2 can generate 20k+ tokens)
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(llm.invoke, messages)
                try:
                    response = _fut.result(timeout=360)
                except _cf.TimeoutError:
                    raise TimeoutError(f"Gemini call timed out after 360s (model={active_model})")
            # Some models return content as a list of blocks (objects or dicts)
            raw = response.content
            if isinstance(raw, list):
                parts = []
                for block in raw:
                    if isinstance(block, dict):
                        parts.append(block.get("text", ""))
                    elif hasattr(block, "text"):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
                text = "".join(parts)
            else:
                text = raw

            if not text:
                raise ValueError(f"Empty response from {active_model}")

            # Token tracking for our local store (LangSmith gets them automatically via LangChain)
            try:
                usage = response.usage_metadata or {}
                prompt_tok     = usage.get("input_tokens", 0)
                completion_tok = usage.get("output_tokens", 0)
                total_tok      = usage.get("total_tokens", prompt_tok + completion_tok)
                if total_tok:
                    print(f"  Tokens — input: {prompt_tok:,}  output: {completion_tok:,}  total: {total_tok:,}")
                    from utils.token_store import add_tokens
                    add_tokens(prompt_tok, completion_tok)
            except Exception:
                pass

            if active_model != model_name:
                print(f"  ✓ Succeeded on fallback model: {active_model}")
            logger.info(f"Gemini call succeeded: model={active_model} chars={len(text)}")
            return text

        except Exception as e:
            error_str = str(e).lower()
            exc_type  = type(e)
            status_code = getattr(e, "status_code", None) or getattr(e, "code", None)
            is_retriable = isinstance(e, TimeoutError) or any(x in error_str for x in [
                "429", "quota", "rate limit", "resource exhausted", "too many requests",
                "503", "unavailable", "high demand", "overloaded", "try again later",
            ])
            is_503 = isinstance(e, TimeoutError) or any(x in error_str for x in ["503", "unavailable", "high demand", "overloaded"])

            print(f"\n{'='*60}")
            print(f"  GEMINI ERROR — caller={caller}  attempt={attempt}/{max_retries}  model={active_model}")
            print(f"  Exception type : {exc_type.__module__}.{exc_type.__name__}")
            print(f"  Message        : {str(e)}")
            print(f"  HTTP status    : {status_code or 'n/a'}")
            print(f"  Is retriable   : {is_retriable}")
            print(f"  API key (last6): ...{_api_key[-6:] if _api_key else 'NOT SET'}")
            print(f"{'='*60}\n")

            if is_503 and _fallback_index < len(_FALLBACK_CHAIN):
                next_model = _FALLBACK_CHAIN[_fallback_index]
                _fallback_index += 1
                print(f"  503/timeout on {active_model} — switching to {next_model}...")
                active_model = next_model
                continue

            if attempt == max_retries:
                logger.error(f"Max retries reached: {e}")
                raise

            if is_retriable:
                delay = min(base_delay * (2 ** (attempt - 1)), 30)
                print(f"  Retriable error — waiting {delay}s before retry...")
                time.sleep(delay)
            else:
                print(f"  Non-retriable error — waiting 5s before retry...")
                time.sleep(5)


def parse_json_response(response_text: str) -> dict:
    """Parse JSON from Gemini response, stripping markdown fences if present."""
    text = response_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nResponse: {text[:500]}")
        # Attempt to recover truncated JSON by closing open braces
        try:
            depth = 0
            for ch in text:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
            # Strip trailing incomplete key/value then close open braces
            trimmed = text.rstrip().rstrip(",").rstrip()
            recovered = trimmed + ("}" * max(depth, 0))
            return json.loads(recovered)
        except Exception:
            raise e


def count_gemini_tokens(system_prompt: str, user_message: str, model_name: str = "gemini-2.5-flash") -> int:
    """Approximate token count via LangChain's get_num_tokens."""
    try:
        llm = _make_llm(model_name, 4000, False)
        return llm.get_num_tokens(system_prompt + "\n" + user_message)
    except Exception:
        return 0
