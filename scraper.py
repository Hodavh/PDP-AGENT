"""
PDP Scraper — Firecrawl primary, Tavily fallback.
Returns structured JSON ready for LLM analysis.
"""

import re
import os
import sys
import json
import warnings
from datetime import datetime, timezone
from urllib.parse import urlparse

warnings.filterwarnings("ignore", category=UserWarning, module="firecrawl")

from dotenv import load_dotenv

load_dotenv()


class ScraperError(Exception):
    pass


COMPLIANCE_KEYWORDS = [
    "reduce", "prevent", "treat", "cure", "heal", "boost", "improve",
    "support", "protect", "fight", "combat", "immunity", "immune",
    "anti-inflammatory", "antioxidant", "cognitive", "mental health",
    "gut health", "digestion", "energy levels", "stress", "anxiety",
    "sleep", "weight loss", "fat burn", "build muscle", "testosterone",
    "hormone", "inflammation", "blood sugar", "cholesterol",
]

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "product_name":                   {"type": "string"},
        "h1":                             {"type": "string"},
        "meta_title":                     {"type": "string"},
        "meta_description":               {"type": "string"},
        "price":                          {"type": "string", "description": "The current selling price (use the lowest/offer price if multiple prices shown)"},
        "original_price":                 {"type": "string", "description": "The original/RRP price if a sale/offer price is also shown (e.g. was £50, now £35)"},
        "price_per_serving":              {"type": "string"},
        "flavours_available":             {"type": "array", "items": {"type": "string"}},
        "key_benefits":                   {"type": "array", "items": {"type": "string"}},
        "ingredient_highlights":          {"type": "array", "items": {"type": "string"}},
        "review_rating":                  {"type": "string"},
        "review_count":                   {"type": "string"},
        "review_snippets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text":     {"type": "string"},
                    "reviewer": {"type": "string"},
                    "date":     {"type": "string"},
                },
            },
        },
        "trust_signals":                  {"type": "array", "items": {"type": "string"}},
        "cta_buttons":                    {"type": "array", "items": {"type": "string"}},
        "faq_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer":   {"type": "string"},
                },
            },
        },
        "image_descriptions":             {"type": "array", "items": {"type": "string"}},
        "nutritionist_quote":             {"type": "string"},
        "compliance_sensitive_sentences": {"type": "array", "items": {"type": "string"}},
        "nutritional_information": {
            "type": "object",
            "description": "Full nutritional table including per-serving and per-100g values. Extract even if inside a collapsed/expandable accordion tab.",
            "properties": {
                "per_serving": {"type": "object", "description": "Nutrient name to value mapping for per-serving column"},
                "per_100g":    {"type": "object", "description": "Nutrient name to value mapping for per-100g column"},
                "serving_size": {"type": "string"},
            },
        },
        "ingredients_list": {"type": "string", "description": "Full ingredients list text, even if inside a collapsed tab"},
        "shipping_info": {"type": "string", "description": "Any shipping threshold, free delivery offer, or delivery timeframe text visible on the page (e.g. 'Free delivery on orders £60+')"},
        "subscription_info": {"type": "string", "description": "Any subscribe-and-save offer, subscription discount, cancel anytime, or skip delivery messaging visible near the Buy Box"},
        "nutritional_table": {
            "type": "object",
            "description": "Extract the full nutritional information table as a structured object with per_100g and per_serving columns if present.",
            "properties": {
                "per_serving": {"type": "object", "description": "Nutrient name to value mapping for per-serving column"},
                "per_100g":    {"type": "object", "description": "Nutrient name to value mapping for per-100g column"},
            },
        },
        "serving_size":          {"type": "string", "description": "The serving size in grams or ml (e.g. '10g', '250ml')"},
        "calories_per_serving":  {"type": "number", "description": "Calories per serving as a number"},
    },
    "required": ["product_name", "h1"],
}

EMPTY_STRUCTURED: dict = {
    "product_name":                   None,
    "h1":                             None,
    "meta_title":                     None,
    "meta_description":               None,
    "price":                          None,
    "original_price":                 None,
    "price_per_serving":              None,
    "flavours_available":             [],
    "key_benefits":                   [],
    "ingredient_highlights":          [],
    "review_rating":                  None,
    "review_count":                   None,
    "review_snippets":                [],
    "trust_signals":                  [],
    "cta_buttons":                    [],
    "faq_items":                      [],
    "image_descriptions":             [],
    "nutritionist_quote":             None,
    "compliance_sensitive_sentences": [],
    "nutritional_information":        None,
    "ingredients_list":               None,
    "shipping_info":                  None,
    "subscription_info":              None,
    "nutritional_table":              None,
    "serving_size":                   None,
    "calories_per_serving":           None,
}


# ── Compliance post-processing ────────────────────────────────────────────────

def _find_compliance_sentences(text: str) -> list[str]:
    if not text:
        return []
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in COMPLIANCE_KEYWORDS) + r")\b",
        re.IGNORECASE,
    )
    sentences = re.split(r"(?<=[.!?])\s+", text)
    found = []
    for s in sentences:
        s = s.strip()
        if pattern.search(s) and 20 < len(s) < 400:
            found.append(s)
    return list(dict.fromkeys(found))


def _fix_price(s: dict) -> None:
    """
    When two prices are on the page (RRP + offer), Firecrawl may return the higher one.
    If original_price is set and price equals original_price, they are the same — no action.
    If both are set and differ, ensure 'price' holds the lower (offer) price.
    """
    import re as _re
    def _parse(val):
        if not val:
            return None
        m = _re.search(r'[\d,]+\.?\d*', str(val).replace(",", ""))
        return float(m.group().replace(",", "")) if m else None

    price = _parse(s.get("price"))
    original = _parse(s.get("original_price"))

    if price and original and original > price:
        # already correct — price is the lower offer price
        return
    if price and original and price > original:
        # swapped — put the lower value in price and higher in original_price
        s["price"], s["original_price"] = s["original_price"], s["price"]


def _fix_h1_meta_title(s: dict) -> None:
    """
    Firecrawl/Tavily sometimes returns the <title> tag content in the h1 field.
    A real H1 never contains ' | ' separators with a brand suffix.
    If detected, split on ' | ' and use the first segment as H1,
    and backfill meta_title if it was empty.
    """
    h1 = s.get("h1") or ""
    if " | " not in h1:
        return
    parts = [p.strip() for p in h1.split(" | ") if p.strip()]
    if len(parts) < 2:
        return
    # Heuristic: last segment is typically the brand name (short, title-cased, no verbs)
    # Promote the first segment as the real H1
    real_h1 = parts[0]
    s["h1"] = real_h1
    if s.get("product_name") == h1:
        s["product_name"] = real_h1
    # Backfill meta_title if empty
    if not s.get("meta_title"):
        s["meta_title"] = h1


def _merge_compliance(structured: dict, markdown: str) -> dict:
    existing = set(structured.get("compliance_sensitive_sentences") or [])
    from_markdown = _find_compliance_sentences(markdown)
    merged = list(existing) + [s for s in from_markdown if s not in existing]
    structured["compliance_sensitive_sentences"] = merged
    return structured


# ── Quality validation ────────────────────────────────────────────────────────

def _validate(structured: dict, markdown: str, error_log: list[str]) -> None:
    issues = []
    if not structured.get("product_name"):
        issues.append("product_name is null")
    if not markdown or len(markdown) < 500:
        issues.append(f"full_markdown too short ({len(markdown or '')} chars)")
    # Only hard-fail if BOTH product_name is missing AND markdown is too short
    # (Tavily fallback legitimately leaves many structured fields empty)
    if issues and len(issues) >= 2:
        raise ScraperError(
            "Content quality check failed — " + "; ".join(issues) +
            ". Errors: " + str(error_log)
        )
    # Warn (don't fail) if many fields are empty
    null_count = sum(1 for v in structured.values() if v is None or v == [] or v == "")
    if null_count > 10:
        missing = [k for k, v in structured.items() if v is None or v == [] or v == ""]
        error_log.append(f"Warning: {null_count} structured fields empty — {', '.join(missing[:8])}")


# ── Firecrawl ─────────────────────────────────────────────────────────────────

def _scrape_firecrawl(url: str, error_log: list[str]) -> tuple[dict | None, str | None]:
    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))

        response = app.scrape_url(
            url,
            formats=[
                "markdown",
                {
                    "type": "json",
                    "prompt": (
                        "Extract the following fields from this product page: "
                        "product_name, h1, meta_title, meta_description, price, price_per_serving, "
                        "flavours_available (list), key_benefits (list), ingredient_highlights (list), "
                        "review_rating, review_count, "
                        "review_snippets (up to 5 items, each with text/reviewer/date), "
                        "trust_signals (list of certifications/guarantees/badges), "
                        "cta_buttons (list), "
                        "faq_items (list of question+answer pairs), "
                        "image_descriptions (list of alt texts), "
                        "nutritionist_quote (any quote or statement attributed to a nutritionist, dietitian, or named expert on the page — look for sections labelled 'nutritionist insight', 'expert view', 'formulated by'), "
                        "compliance_sensitive_sentences (sentences containing health or efficacy claims), "
                        "nutritional_information (IMPORTANT: the full nutrition facts table with per-serving "
                        "and per-100g values — this is frequently hidden inside a collapsed accordion tab "
                        "labelled 'Nutritional Information'; you must extract it even if it appears collapsed), "
                        "ingredients_list (full ingredients text, also often inside a collapsed tab), "
                        "shipping_info (any free delivery threshold or delivery timeframe text), "
                        "subscription_info (any subscribe-and-save or cancel anytime messaging)."
                    ),
                    "schema": EXTRACT_SCHEMA,
                },
            ],
            actions=[
                # Expand nutrition/ingredients accordion tabs
                {"type": "click", "selector": "[class*='accordion'], [class*='collapse'], details, [class*='tab']"},
                {"type": "wait", "milliseconds": 800},
                # Expand any 'More details' or 'Show more' toggles
                {"type": "click", "selector": "button:has-text('More details'), button:has-text('Show more'), button:has-text('Nutritional Information'), button:has-text('Ingredients'), a:has-text('More details')"},
                {"type": "wait", "milliseconds": 800},
                # Expand delivery/shipping info popups or dropdowns
                {"type": "click", "selector": "button:has-text('Delivery'), button:has-text('Shipping'), [class*='delivery'], [class*='shipping']"},
                {"type": "wait", "milliseconds": 500},
            ],
        )

        markdown = getattr(response, "markdown", None) or ""
        # v2 returns structured data under .json (not .extract)
        extracted = getattr(response, "json", None) or getattr(response, "extract", None) or {}

        if not markdown and not extracted:
            error_log.append("Firecrawl returned empty markdown and empty extract")
            return None, None

        structured = {**EMPTY_STRUCTURED, **{k: v for k, v in extracted.items() if v is not None}}
        _fix_h1_meta_title(structured)
        _fix_price(structured)

        if extracted and not any(extracted.values()):
            error_log.append("Firecrawl extract fields all empty — markdown present, structured fields skipped")

        return structured, markdown

    except Exception as e:
        error_log.append(f"Firecrawl error: {e}")
        return None, None


# ── Tavily fallback ───────────────────────────────────────────────────────────

def _scrape_tavily(url: str, error_log: list[str]) -> tuple[dict | None, str | None]:
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

        response = client.extract(urls=[url], extract_depth="advanced", format="markdown")
        results = response.get("results", [])

        if not results:
            error_log.append("Tavily extract returned no results")
            return None, None

        first = results[0]
        raw_content = first.get("raw_content") or first.get("content") or ""
        if not raw_content:
            error_log.append("Tavily returned empty content")
            return None, None

        structured = _parse_tavily_content(raw_content, error_log)
        return structured, raw_content

    except Exception as e:
        error_log.append(f"Tavily error: {e}")
        return None, None


def _parse_tavily_content(content: str, error_log: list[str]) -> dict:
    """Map Tavily raw content to the standard structured schema using heuristics."""
    s = {**EMPTY_STRUCTURED}

    lines = content.splitlines()
    non_empty = [l.strip() for l in lines if l.strip()]

    # H1 — first markdown heading
    for line in non_empty:
        if line.startswith("# "):
            s["h1"] = line.lstrip("# ").strip()
            s["product_name"] = s["h1"]
            break

    # Meta title fallback — first non-heading line if no H1
    if not s["h1"] and non_empty:
        s["h1"] = non_empty[0]
        s["product_name"] = non_empty[0]

    _fix_h1_meta_title(s)
    _fix_price(s)

    # Price
    price_match = re.search(r"[£€\$]\s?\d+[\.,]?\d*", content)
    if price_match:
        s["price"] = price_match.group(0).strip()

    # Per-serving price
    serving_match = re.search(
        r"(?:[£€\$]?\s?\d+[\.,]?\d*\s*p?)\s*(?:per|\/)\s*serving",
        content, re.I
    )
    if serving_match:
        s["price_per_serving"] = serving_match.group(0).strip()

    # Review rating
    rating_match = re.search(r"(\d\.\d)\s*(?:out of\s*5|\/\s*5|\s*stars?)", content, re.I)
    if rating_match:
        s["review_rating"] = rating_match.group(0).strip()

    # Review count
    count_match = re.search(r"([\d,]+)\s*(?:reviews?|ratings?)", content, re.I)
    if count_match:
        s["review_count"] = count_match.group(0).strip()

    # Key benefits — lines starting with bullet markers
    bullets = []
    for line in non_empty:
        if re.match(r"^[-*•]\s+", line):
            bullets.append(re.sub(r"^[-*•]\s+", "", line))
    s["key_benefits"] = bullets[:12]

    # Trust signals
    trust_kws = ["money back", "guarantee", "certified", "informed sport",
                 "batch tested", "gmp", "vegan", "gluten free", "award"]
    trust_found = []
    for kw in trust_kws:
        if kw.lower() in content.lower():
            idx = content.lower().find(kw.lower())
            snippet = content[max(0, idx - 10):idx + 80].strip()
            trust_found.append(snippet)
    s["trust_signals"] = trust_found[:8]

    # FAQ items
    faq_items = []
    faq_matches = re.findall(
        r"(?:^|\n)\s*(?:Q[:\.]?\s*|##\s*)(.+\?)\s*\n\s*(?:A[:\.]?\s*)?(.+?)(?=\n\s*(?:Q[:\.]?|##\s*|\Z))",
        content, re.DOTALL | re.IGNORECASE
    )
    for q, a in faq_matches[:8]:
        faq_items.append({"question": q.strip(), "answer": a.strip()[:300]})
    s["faq_items"] = faq_items

    error_log.append("Tavily fallback used — structured fields parsed from raw content heuristics")
    return s


# ── Layout signals ────────────────────────────────────────────────────────────

def extract_layout_signals(url: str) -> dict:
    """
    Use Playwright to query computed styles and DOM structure of key page elements.
    Returns a structured dict of layout facts for the Actor to score against the rubric.
    No screenshot — pure computed style interrogation.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                locale="en-GB",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            # Expand accordions, tabs, and popups to expose hidden content
            for selector in [
                "button:has-text('More details')",
                "button:has-text('Show more')",
                "button:has-text('Nutritional Information')",
                "button:has-text('Ingredients')",
                "button:has-text('Delivery')",
                "button:has-text('Shipping')",
                "[class*='accordion'] button",
                "[class*='tab-label']",
                "details summary",
            ]:
                try:
                    for btn in page.query_selector_all(selector):
                        if btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(300)
                except Exception:
                    pass

            signals = page.evaluate("""
                () => {
                    const cs = (el) => el ? window.getComputedStyle(el) : null;
                    const attr = (el, a) => el ? (el.getAttribute(a) || '') : '';

                    // ── 1. Benefit section layout ────────────────────────────
                    // Find sections containing repeated (icon/image + heading + paragraph) patterns
                    const benefitSections = Array.from(document.querySelectorAll(
                        'section, [class*="benefit"], [class*="feature"], [class*="highlight"], [class*="reason"]'
                    )).filter(el => {
                        const imgs = el.querySelectorAll('img, svg').length;
                        const headings = el.querySelectorAll('h2,h3,h4').length;
                        const paras = el.querySelectorAll('p').length;
                        return imgs >= 2 && headings >= 2 && paras >= 2;
                    });

                    let benefitLayout = 'unknown';
                    let benefitColumns = 1;
                    let iconPairedWithHeading = false;

                    if (benefitSections.length > 0) {
                        const s = cs(benefitSections[0]);
                        benefitLayout = s ? s.display : 'unknown';
                        if (benefitLayout === 'grid') {
                            const cols = s.gridTemplateColumns.split(' ').filter(c => c && c !== 'none');
                            benefitColumns = cols.length || 1;
                        } else if (benefitLayout === 'flex') {
                            const items = benefitSections[0].children.length;
                            benefitColumns = items > 1 ? items : 1;
                        }
                        // Check if icon/img is a sibling/child alongside heading in same container
                        const firstChild = benefitSections[0].querySelector('*');
                        if (firstChild) {
                            const childStyle = cs(firstChild);
                            const childDisplay = childStyle ? childStyle.display : '';
                            const hasImg = firstChild.querySelector('img, svg') !== null;
                            const hasHeading = firstChild.querySelector('h2,h3,h4') !== null;
                            iconPairedWithHeading = hasImg && hasHeading;
                        }
                    }

                    // ── 2. Nutrition table ───────────────────────────────────
                    const nutritionSelectors = [
                        'table', '[class*="nutrition"]', '[class*="nutrient"]',
                        '[id*="nutrition"]', '[class*="ingredient"]', '[id*="ingredient"]'
                    ];
                    let nutritionTable = null;
                    for (const sel of nutritionSelectors) {
                        nutritionTable = document.querySelector(sel);
                        if (nutritionTable) break;
                    }
                    const nutritionFound = !!nutritionTable;
                    const nutritionIsTable = nutritionTable ? nutritionTable.tagName === 'TABLE' : false;

                    // Detect popup/modal trigger for nutrition (button or link that opens a modal)
                    const pageText = (document.body ? document.body.innerText : '').toLowerCase();
                    const nutritionInPopup = !nutritionFound && (
                        !!document.querySelector('button[data-modal*="nutri"], button[data-target*="nutri"], [data-toggle*="nutri"]') ||
                        /nutritional information|view nutrition|nutrition info|see nutrition/i.test(pageText)
                    );

                    // Estimate position: what fraction down the page is the nutrition section?
                    let nutritionPositionFraction = null;
                    if (nutritionTable) {
                        const rect = nutritionTable.getBoundingClientRect();
                        const scrollHeight = document.documentElement.scrollHeight;
                        nutritionPositionFraction = scrollHeight > 0
                            ? Math.round(((rect.top + window.scrollY) / scrollHeight) * 100)
                            : null;
                    }

                    // ── 3. Image gallery navigation ──────────────────────────
                    // Look for dot indicators vs thumbnail strips
                    const dotIndicators = document.querySelectorAll(
                        '[class*="dot"], [class*="indicator"], [class*="slick-dot"], [aria-label*="slide"]'
                    ).length;
                    const thumbnails = document.querySelectorAll(
                        '[class*="thumb"], [class*="thumbnail"], [class*="gallery-nav"] img'
                    ).length;
                    let galleryNavType = 'unknown';
                    if (thumbnails > 1) galleryNavType = 'thumbnails';
                    else if (dotIndicators > 0) galleryNavType = 'dots';

                    // ── 4. Main content column layout ────────────────────────
                    const main = document.querySelector('main, [role="main"], #main, .main');
                    const mainStyle = cs(main);
                    const mainDisplay = mainStyle ? mainStyle.display : 'unknown';
                    let mainColumns = 1;
                    if (mainDisplay === 'grid' && mainStyle) {
                        const cols = mainStyle.gridTemplateColumns.split(' ').filter(c => c && c !== 'none');
                        mainColumns = cols.length;
                    }

                    // ── 5. Benefit highlight count ───────────────────────────
                    // Count top-level items in what looks like a benefit/feature list
                    let benefitHighlightCount = 0;
                    const featureLists = document.querySelectorAll(
                        '[class*="benefit"] li, [class*="feature"] li, [class*="highlight"] li, ' +
                        '[class*="reason"] li, [class*="usp"] li'
                    );
                    if (featureLists.length > 0) {
                        benefitHighlightCount = featureLists.length;
                    }

                    // ── 6. Paragraph density in hero/benefit area ────────────
                    // Check if benefit paragraphs are long (wall of text) or short (scannable)
                    const benefitParas = Array.from(document.querySelectorAll(
                        '[class*="benefit"] p, [class*="feature"] p, [class*="description"] p'
                    )).map(p => p.innerText.trim().length).filter(l => l > 0);
                    const avgParaLength = benefitParas.length > 0
                        ? Math.round(benefitParas.reduce((a, b) => a + b, 0) / benefitParas.length)
                        : null;

                    // ── 7. Collapsible / accordion sections ──────────────────
                    const accordions = document.querySelectorAll(
                        '[class*="accordion"], [class*="collapse"], details, [class*="faq"]'
                    ).length;

                    // ── 8. Review trust signals ──────────────────────────────
                    // Detect: star filter controls, verified buyer badges,
                    // review count breakdown (1-star through 5-star bars),
                    // and whether negative reviews are visible

                    // Star filter controls — clickable elements to filter by star rating
                    const starFilters = document.querySelectorAll(
                        '[class*="star-filter"], [class*="rating-filter"], [class*="review-filter"], ' +
                        '[aria-label*="star"], [aria-label*="rating filter"], ' +
                        '[class*="rating-bar"] a, [class*="rating-bar"] button, ' +
                        '[class*="histogram"] a, [class*="histogram"] button'
                    ).length;

                    // Rating breakdown bars (1★ through 5★ percentage bars)
                    const ratingBars = document.querySelectorAll(
                        '[class*="rating-bar"], [class*="histogram-bar"], ' +
                        '[class*="star-bar"], [class*="review-bar"]'
                    ).length;

                    // Verified buyer / verified purchase badges
                    const verifiedBadges = document.querySelectorAll(
                        '[class*="verified"], [class*="verified-buyer"], [class*="verified-purchase"], ' +
                        '[class*="badge"]:not([class*="cert"]):not([class*="award"])'
                    );
                    const verifiedBadgeCount = verifiedBadges.length;
                    const verifiedBadgeText = Array.from(verifiedBadges)
                        .map(el => el.innerText.trim().toLowerCase())
                        .filter(t => t.includes('verified') || t.includes('buyer') || t.includes('purchase'))
                        .slice(0, 3);

                    // Check if 1-star and 2-star reviews are actually visible on the page
                    const reviewText = document.body ? document.body.innerText : '';
                    const lowStarMentions = (reviewText.match(/1[ -]?star|2[ -]?star|[★]☆|1[/]5|2[/]5/gi) || []).length;

                    // Review section: count visible review cards
                    const reviewCards = document.querySelectorAll(
                        '[class*="review-item"], [class*="review-card"], [class*="review-block"], ' +
                        '[class*="review-container"] > *, [itemprop="review"]'
                    ).length;

                    // Rating aggregate display (the main star + count widget)
                    const ratingWidget = document.querySelector(
                        '[class*="aggregate-rating"], [class*="overall-rating"], ' +
                        '[class*="average-rating"], [itemprop="aggregateRating"]'
                    );
                    const ratingWidgetPosition = ratingWidget ? (() => {
                        const rect = ratingWidget.getBoundingClientRect();
                        return rect.top + window.scrollY < 900 ? 'atf_or_near' : 'below_fold';
                    })() : 'not_found';

                    // ── 9. Internal catalog differentiation ──────────────────
                    // Detect variant comparison tables, product selector UI, and
                    // cross-product comparison content (e.g. "vs" tables, product matrices)

                    // Comparison tables: tables with 2+ columns and product-like headings
                    const allTables = Array.from(document.querySelectorAll('table'));
                    const comparisonTables = allTables.filter(t => {
                        const headerCells = t.querySelectorAll('th').length;
                        const rows = t.querySelectorAll('tr').length;
                        const cols = t.querySelector('tr')
                            ? t.querySelector('tr').querySelectorAll('td,th').length
                            : 0;
                        return cols >= 2 && rows >= 2 && headerCells >= 2;
                    });

                    // Variant selectors: button-style or dropdown product/size/flavour pickers
                    const variantButtons = document.querySelectorAll(
                        '[class*="variant"] button, [class*="swatch"], [class*="option"] button, ' +
                        '[class*="flavour"] button, [class*="flavor"] button, [class*="size"] button, ' +
                        '[data-variant], [data-option]'
                    ).length;
                    const variantDropdowns = document.querySelectorAll(
                        '[class*="variant"] select, [class*="option"] select, ' +
                        'select[name*="variant"], select[name*="option"]'
                    ).length;

                    // "Compare" or "vs" content anywhere on page
                    const comparePageText = document.body ? document.body.innerText : '';
                    const hasCompareLanguage = /\bvs\b|\bversus\b|\bcompare\b|\bdifference between\b/i.test(comparePageText);

                    // Named product variants in visible text (e.g. "Superblend", "Peakblend")
                    // Look for repeated capitalised brand words near variant selectors
                    const variantNames = [];
                    document.querySelectorAll(
                        '[class*="variant"] [class*="name"], [class*="variant"] [class*="title"], ' +
                        '[class*="option"] [class*="label"], [class*="swatch"] span'
                    ).forEach(el => {
                        const t = el.innerText.trim();
                        if (t.length > 1 && t.length < 40) variantNames.push(t);
                    });

                    // Product tabs that might contain a comparison
                    const productTabs = document.querySelectorAll(
                        '[class*="tab"], [role="tab"]'
                    );
                    const tabTexts = Array.from(productTabs)
                        .map(t => t.innerText.trim().toLowerCase())
                        .filter(t => t.length > 0 && t.length < 30);

                    return {
                        benefit_section: {
                            found: benefitSections.length > 0,
                            count: benefitSections.length,
                            display: benefitLayout,
                            columns: benefitColumns,
                            icon_paired_with_heading: iconPairedWithHeading,
                            highlight_count: benefitHighlightCount,
                            avg_paragraph_chars: avgParaLength,
                        },
                        nutrition: {
                            found: nutritionFound,
                            is_html_table: nutritionIsTable,
                            position_percent_down_page: nutritionPositionFraction,
                            in_popup_or_modal: nutritionInPopup,
                        },
                        gallery: {
                            nav_type: galleryNavType,
                            dot_count: dotIndicators,
                            thumbnail_count: thumbnails,
                        },
                        main_layout: {
                            display: mainDisplay,
                            columns: mainColumns,
                        },
                        accordions_found: accordions > 0,
                        accordion_count: accordions,
                        review_trust_signals: {
                            star_filter_controls_found: starFilters > 0,
                            star_filter_count: starFilters,
                            rating_breakdown_bars_found: ratingBars > 0,
                            rating_breakdown_bar_count: ratingBars,
                            verified_badge_found: verifiedBadgeCount > 0,
                            verified_badge_count: verifiedBadgeCount,
                            verified_badge_text_samples: verifiedBadgeText,
                            low_star_reviews_visible: lowStarMentions > 0,
                            low_star_mention_count: lowStarMentions,
                            review_card_count: reviewCards,
                            rating_widget_position: ratingWidgetPosition,
                        },
                        catalog_differentiation: {
                            comparison_table_found: comparisonTables.length > 0,
                            comparison_table_count: comparisonTables.length,
                            variant_buttons_found: variantButtons > 0,
                            variant_button_count: variantButtons,
                            variant_dropdowns_found: variantDropdowns > 0,
                            variant_dropdown_count: variantDropdowns,
                            compare_language_detected: hasCompareLanguage,
                            variant_names_found: [...new Set(variantNames)].slice(0, 10),
                            tab_labels: tabTexts.slice(0, 10),
                        },
                    };
                }
            """)

            browser.close()
            return signals

    except Exception as e:
        return {"error": str(e), "note": "Layout signal extraction failed — rubric evaluated from text signals only"}


# ── ATF screenshot ────────────────────────────────────────────────────────────

def capture_gallery_image_urls(url: str, max_images: int = 8) -> tuple[list[str], list[str]]:
    """
    Launch a headless browser, collect all unique product gallery image URLs
    and their alt texts from slider-thumbnail containers.
    Upscales from w=120 to w=800 for full-size images.
    Returns (list of full-size URLs, list of alt texts) up to max_images.
    """
    import re as _re
    try:
        from urllib.parse import urlparse
        from playwright.sync_api import sync_playwright

        domain = "." + urlparse(url).netloc.lstrip("www.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
            )
            context.add_cookies([
                {"name": "OptanonAlertBoxClosed", "value": "true", "domain": domain, "path": "/"},
                {"name": "OptanonConsent", "value": "isGpcEnabled=0&datestamp=Mon+Jan+01+2024&version=6.10.0&consentId=accepted", "domain": domain, "path": "/"},
            ])
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            page.wait_for_timeout(2500)

            # Collect img srcs + alt texts from slider-thumbnail containers (deduplicated by base URL)
            items = page.evaluate("""() => {
                const imgs = document.querySelectorAll(
                    "[class*='slider-thumbnail'] img, [class*='slider-thumb'] img"
                );
                const seen = new Set();
                const result = [];
                imgs.forEach(img => {
                    const src = img.src || img.getAttribute('data-src') || '';
                    if (!src) return;
                    const base = src.split('?')[0];
                    if (!seen.has(base)) {
                        seen.add(base);
                        result.push({ src, alt: img.alt || '' });
                    }
                });
                return result;
            }""")

            browser.close()

        # Upscale thumbnail URLs from w=120 to w=800 for full-size images
        full_size_urls = []
        alt_texts = []
        for item in items[:max_images]:
            full_size_urls.append(_re.sub(r'([?&]w=)\d+', r'\g<1>800', item["src"]))
            alt_texts.append(item["alt"])

        print(f"  ✓ Gallery images collected: {len(full_size_urls)}")
        return full_size_urls, alt_texts

    except Exception as e:
        print(f"  ⚠ Gallery image capture failed: {e}")
        return [], []


def capture_atf_screenshot(url: str) -> str | None:
    """
    Launch a headless Chromium browser at iPhone 14 viewport (390×844px),
    dismiss cookie banners, then capture only the above-the-fold area.
    Returns a base64-encoded PNG string, or None on failure.
    """
    try:
        import base64
        from urllib.parse import urlparse
        from playwright.sync_api import sync_playwright

        domain = "." + urlparse(url).netloc.lstrip("www.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 390, "height": 844},
                device_scale_factor=2,
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                locale="en-GB",
            )

            # Strategy 3 — pre-set consent cookies so banner never appears
            context.add_cookies([
                {"name": "OptanonAlertBoxClosed", "value": "true", "domain": domain, "path": "/"},
                {"name": "OptanonConsent", "value": "isGpcEnabled=0&isIABGlobal=false&datestamp=Mon+Jan+01+2024&version=6.10.0&consentId=accepted", "domain": domain, "path": "/"},
            ])

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                # Fallback: commit=false fires as soon as headers arrive
                try:
                    page.goto(url, wait_until="commit", timeout=30000)
                except Exception:
                    pass
            page.wait_for_timeout(2000)

            # Strategy 1 — click known cookie dismiss buttons
            cookie_selectors = [
                "button#onetrust-accept-btn-handler",
                "button.onetrust-close-btn-handler",
                "#accept-recommended-btn-handler",
                "button:has-text('Accept all')",
                "button:has-text('Accept All')",
                "button:has-text('Accept essential cookies only')",
                "button:has-text('That\\'s OK')",
                "button:has-text('Allow all')",
                "button:has-text('Got it')",
                "button:has-text('I agree')",
                "button:has-text('OK')",
                "[aria-label='Close']",
                "[aria-label='close']",
                ".cookie-close",
                ".consent-close",
            ]
            for selector in cookie_selectors:
                try:
                    btn = page.wait_for_selector(selector, timeout=1000, state="visible")
                    if btn:
                        btn.click()
                        print(f"  ✓ Cookie banner dismissed: {selector}")
                        page.wait_for_timeout(800)
                        break
                except Exception:
                    continue

            # Strategy 2 — remove OneTrust overlay via JS if still visible
            try:
                overlay = page.query_selector("#onetrust-consent-sdk")
                if overlay and overlay.is_visible():
                    page.evaluate("""() => {
                        const sdk = document.getElementById('onetrust-consent-sdk');
                        if (sdk) sdk.remove();
                        const backdrop = document.querySelector('.onetrust-pc-dark-filter');
                        if (backdrop) backdrop.remove();
                        document.body.style.overflow = 'auto';
                        document.body.style.position = 'static';
                    }""")
                    print("  ✓ Cookie banner removed via JS")
                    page.wait_for_timeout(500)
            except Exception as e:
                print(f"  ⚠ JS banner removal failed: {e}")

            # Wait for gallery/carousel to initialise — try specific selectors, fall back to fixed wait
            gallery_loaded = False
            for gallery_sel in [
                "[class*='thumb']", "[class*='thumbnail']",
                "[class*='gallery-nav']", "[class*='slick']",
                "[class*='swiper']", "[class*='carousel']",
            ]:
                try:
                    page.wait_for_selector(gallery_sel, timeout=3000, state="visible")
                    gallery_loaded = True
                    print(f"  ✓ Gallery element found: {gallery_sel}")
                    break
                except Exception:
                    continue
            if not gallery_loaded:
                # Gallery not detected — give JS an extra 2s to render
                page.wait_for_timeout(2000)

            # Scroll back to absolute top — cookie dismissal or gallery init may have shifted position
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)

            # Capture exactly the viewport — no scroll
            screenshot_bytes = page.screenshot(
                clip={"x": 0, "y": 0, "width": 390, "height": 844},
                type="png",
            )
            browser.close()

        return base64.standard_b64encode(screenshot_bytes).decode("utf-8")

    except Exception as e:
        print(f"  ⚠ ATF screenshot failed: {e}")
        return None


# ── Nutrition meta ────────────────────────────────────────────────────────────

def extract_nutrition_meta(scraped: dict) -> dict:
    s = scraped.get("structured", {})
    nutrition_text = s.get("nutritional_information", "") or ""
    nutrition_table = s.get("nutritional_table", {}) or {}
    return {
        "raw_text": nutrition_text,
        "is_structured": bool(nutrition_table),
        "serving_size": s.get("serving_size"),
        "calories_per_serving": s.get("calories_per_serving"),
        "nutritional_table": nutrition_table,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape_pdp(url: str) -> dict:
    error_log: list[str] = []
    firecrawl_success = False
    tavily_success = False
    fallback_used = False

    structured, markdown = _scrape_firecrawl(url, error_log)

    if structured is not None:
        firecrawl_success = True
    else:
        fallback_used = True
        structured, markdown = _scrape_tavily(url, error_log)
        if structured is not None:
            tavily_success = True
        else:
            raise ScraperError(
                f"Both Firecrawl and Tavily failed for {url}. "
                f"Errors: {error_log}"
            )

    structured = _merge_compliance(structured, markdown or "")

    _validate(structured, markdown or "", error_log)

    print("  Extracting layout signals (computed styles)...")
    layout_signals = extract_layout_signals(url)
    if "error" not in layout_signals:
        print("  ✓ Layout signals extracted")
    else:
        print(f"  ⚠ Layout signals failed: {layout_signals.get('error', '')[:60]}")

    print("  Capturing gallery images via thumbnail clicks...")
    gallery_image_urls, gallery_image_alts = capture_gallery_image_urls(url)

    print("  Capturing above-the-fold screenshot (mobile 390×844px)...")
    atf_screenshot = capture_atf_screenshot(url)
    if atf_screenshot:
        print("  ✓ ATF screenshot captured")
    else:
        print("  ⚠ ATF screenshot unavailable — falling back to text-based ATF evaluation")

    scraped = {
        "url": url,
        "domain": urlparse(url).netloc,
        "scraper_used": "firecrawl" if firecrawl_success else "tavily",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "structured": structured,
        "full_markdown": markdown or "",
        "layout_signals": layout_signals,
        "gallery_image_urls": gallery_image_urls,
        "gallery_image_alts": gallery_image_alts,
        "atf_screenshot_base64": atf_screenshot,
        "scrape_metadata": {
            "firecrawl_success": firecrawl_success,
            "tavily_success": tavily_success,
            "fallback_used": fallback_used,
            "gallery_images_captured": len(gallery_image_urls),
            "atf_screenshot_captured": atf_screenshot is not None,
            "error_log": error_log,
        },
    }
    scraped["nutrition"] = extract_nutrition_meta(scraped)
    return scraped


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 \
          else "https://www.theproteinworks.com/ai-greens"
    result = scrape_pdp(url)
    print(json.dumps(result, indent=2))
    print(f"\nScraper used:              {result['scraper_used']}")
    print(f"Product name:              {result['structured']['product_name']}")
    print(f"Review rating:             {result['structured']['review_rating']}")
    print(f"Compliance sentences found: {len(result['structured']['compliance_sensitive_sentences'])}")
