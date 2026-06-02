import json
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from langsmith import traceable

from utils.gemini_client import (
    ACTOR_MODEL,
    call_gemini,
    count_gemini_tokens,
    parse_json_response,
)

load_dotenv()




_jinja = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "prompts"))

RUBRIC = {
    "headline_clarity": {
        "description": (
            "The above-the-fold (ATF) mobile viewport must instantly communicate product identity, "
            "functional outcome, and unit price within a 4-9 second engagement window — "
            "no scrolling required. First impressions form in 50ms; cognitive overload from "
            "jargon or missing anchors causes scroll abandonment. "
            "Evaluate all five structural elements independently, then score the dimension overall."
        ),
        "elements": {
            "descriptive_h1": (
                "H1 must contain a recognisable category keyword so a first-time visitor knows what the product is. "
                "PASS if the H1 includes the product type anywhere — e.g. 'Gold Standard 100% Isolate Whey Protein Powder' passes because 'Whey Protein Powder' is present; brand differentiators before or after the category are fine. "
                "FAIL only if the H1 is a pure brand code with no category signal (e.g. 'AG1', 'Impact', 'Formula X')."
            ),
            "outcome_sub_headline": (
                "The text immediately below the H1 must state who the product is for and "
                "what it functionally does — in one concise sentence. "
                "Marketing slogans without functional meaning score 1."
            ),
            "price_visibility": (
                "Cost-per-serving must be visible ATF to reduce abandonment. "
                "Do NOT quote or reference the specific price value in your rationale. "
                "Score based solely on whether per-serving cost is present or absent."
            ),
            "visual_trust_anchors": (
                "A clear unobstructed product packaging image AND a star rating with review count "
                "must be visible ATF. Both establish immediate social proof and visual context. "
                "Evaluate using scraped image data and reviews fields."
            ),
            "prominent_cta": (
                "Primary 'Add to Cart' / 'Add to Basket' CTA must contrast sharply with the "
                "background and meet the 44px minimum mobile touch target. "
                "Evaluate CTA text from cta_buttons field; contrast and size require visual inspection — "
                "flag with [VISUAL INSPECTION REQUIRED] if images are unavailable."
            ),
        },
        "scoring": {
            1: "H1 uses abstract brand name only. No sub-headline. Cost-per-serving missing. No reviews ATF. CTA absent or buried.",
            2: "H1 names product but not category. Sub-headline generic. Cost-per-serving missing. Rating present but no count.",
            3: "H1 has product + category. Outcome sub-headline present. Cost-per-serving missing. Rating + count present. CTA visible but low contrast.",
            4: "All five elements present. Cost-per-serving OR CTA contrast is suboptimal.",
            5: "All five elements fully met: descriptive H1, outcome sub-headline, cost-per-serving visible, rating + review count, high-contrast 44px+ CTA.",
        },
        "sources": [
            "Baymard #577 — ATF product page clarity",
            "NNG — 50ms first impression research",
            "CXL — 4-9 second engagement window study",
            "Google Mobile UX — 44px touch target guideline",
        ],
    },
    "benefit_hierarchy": {
        "description": (
            "Product descriptions must be structured to maximise scannability and prioritise "
            "functional outcomes over raw biological features. Consumers scan for 'informational scent' "
            "rather than reading linearly — unformatted ingredient lists create a 'wall of specs' "
            "that causes severe user friction. Evaluate four architectural elements independently."
        ),
        "elements": {
            "outcome_first_framing": (
                "Headlines and bullet points must lead with the biological or lifestyle benefit the customer "
                "cares about (e.g. 'Energy that lasts', 'Supports stress management'). "
                "Raw biochemical features (e.g. 'Contains 500mg Ashwagandha') must never lead messaging — "
                "they are permitted only as substantiating evidence that follows the outcome. "
                "Check bullet_points and h2s for feature-led vs outcome-led language."
            ),
            "three_part_highlight_architecture": (
                "Core benefits should use a 3-part visual layout: bespoke icon or image + short confirmatory "
                "headline + single brief paragraph (3-4 sentences max). This structure slows scanning users "
                "and guides the eye. Evaluate using image_alts (icons present?), h2s/h3s (confirmatory headlines?), "
                "and paragraph length. Flag [VISUAL INSPECTION REQUIRED] if images unavailable."
            ),
            "highlight_count_discipline": (
                "Core benefit highlights must be strictly limited to 2-6 features. "
                "Highlighting 20+ features equally dilutes the value proposition and causes cognitive overload. "
                "Count the number of distinct benefit highlights in bullet_points and h2s/h3s. "
                "A page with 15+ equally weighted bullets scores 1 on this element regardless of copy quality."
            ),
            "semantic_grouping_of_details": (
                "Deep nutritional data (amino acid profiles, micronutrient tables, full ingredient breakdowns) "
                "must not interrupt the primary persuasive narrative. It must be separated from the persuasive copy "
                "via one of these valid patterns — all score PASS for this element: "
                "(1) a collapsible accordion section on the page, "
                "(2) a popup or modal triggered by a 'Nutritional Information' button, "
                "(3) a dedicated lower-page table under sub-section headers. "
                "A popup modal is the highest-quality pattern — it completely removes spec data from the persuasive flow. "
                "IMPORTANT — scraper limitation: nutritional tables inside JavaScript-rendered accordions, popups, or "
                "modals are often NOT captured by the scraper. "
                "If structured.nutritional_information is populated, use it to assess table quality. "
                "If structured.nutritional_information is null, apply this decision tree: "
                "1. If layout_signals.nutrition.in_popup_or_modal is true → PASS, note [NUTRITION IN POPUP — scraper cannot access modal content]. "
                "2. If body_copy or structured fields mention vitamins, minerals, per-serving values, 'Essential Vitamins', 'full spectrum', ingredient weights in mg/g → the product HAS nutritional data. Check whether it is inline (FAIL) or referenced via a separate section/popup/link (PASS). "
                "3. If the page is a supplement product with ingredient highlights listing specific nutrients, it almost certainly has a nutrition table accessible via a button or popup — do NOT score as absent without evidence. Note: [NUTRITION TABLE LIKELY IN POPUP — visual inspection required]. "
                "Only score as absent if the page has no nutritional claims, no ingredient weights, and no structured.nutritional_information."
            ),
        },
        "scoring": {
            1: "Feature-led copy throughout. No outcome framing. Ingredient wall as primary content. No structure.",
            2: "Some outcome language present but features still lead. 10+ undifferentiated bullets. No grouping.",
            3: "Outcome-first language in headlines. Bullets still mix benefits and specs. No 3-part layout. Nutrition inline.",
            4: "Outcome-first framing consistent. 2-6 highlights present. Partial 3-part layout. Nutritional data mostly separated.",
            5: "All four elements met: outcome-first throughout, 2-6 highlights in 3-part layout, zero spec walls in persuasive copy, nutrition in separated single-column table.",
        },
        "sources": [
            "NNG — F-pattern and scanning behaviour research",
            "Baymard #588 — product page content hierarchy",
            "CXL — outcome-first copywriting framework",
            "Nielsen Norman Group — cognitive load and information scent",
        ],
    },
    "product_positioning": {
        "description": (
            "The page must answer the consumer's active comparison-shopping question: "
            "'Why should I choose this specific product over an alternative, and does it fit my lifestyle?' "
            "Generic claims like 'high quality protein powder' score 1 — they apply equally to every competitor "
            "and provide zero differentiation. The copy must act as a sophisticated sales assistant that "
            "contextualises the product within the user's daily routine. "
            "Evaluate five strategic elements independently."
        ),
        "elements": {
            "target_identity_articulation": (
                "The copy must explicitly state who the product is designed for — a defined persona or lifestyle "
                "(e.g. 'for active and busy lifestyles', 'for endurance athletes', 'for women over 40'). "
                "Vague audience language ('anyone who wants to feel better') scores 1. "
                "Check paragraphs, bullet_points, and h2s for identity-specific language."
            ),
            "consumption_occasion_definition": (
                "The page must specify the precise behavioural occasion or temporal moment for consumption "
                "(e.g. 'Start your day with Clean Greens', 'Drink between meals', 'Take 30 minutes pre-workout'). "
                "Defining the occasion shifts the product from a generic supplement into a lifestyle-integrated utility. "
                "Check paragraphs and bullet_points for occasion-anchoring language."
            ),
            "problem_resolution_focus": (
                "The copy must address a specific physiological or lifestyle pain point the target market faces — "
                "e.g. afternoon energy crashes, gut discomfort, post-workout recovery, stress, poor sleep. "
                "The problem must be named explicitly before the solution is offered "
                "(e.g. 'Bloating can be a common problem... we add enzymes to aid natural digestion'). "
                "Implied benefits without a named problem score partial. Check paragraphs and bullet_points."
            ),
            "internal_catalog_differentiation": (
                "For brands offering multiple variants of a similar product (e.g. Superblend vs Performance vs Peakblend), "
                "the page must include a comparison table or matrix explaining the differences between options "
                "so the user can accurately self-select without frustration. "
                "If the product has no variants, mark as N/A (does not affect score). "
                "Check structured_data and bullet_points for comparison content; flag [VISUAL INSPECTION REQUIRED] "
                "to confirm table presence."
            ),
            "proprietary_jargon_clarification": (
                "If the brand uses scientific terminology or proprietary marketing words "
                "(e.g. 'Superblend', 'Adaptogen Complex', 'Informed Sport Matrix'), "
                "each term must be immediately followed by a plain-language definition. "
                "Undefined jargon cognitively alienates non-expert users. "
                "Scan h1, h2s, bullet_points, and paragraphs for proprietary or technical terms, "
                "then check whether a definition follows within the same section."
            ),
        },
        "scoring": {
            1: "Generic claims only. No audience identity, no occasion, no pain point named. Jargon undefined.",
            2: "Broad audience language present (e.g. 'fitness enthusiasts'). One pain point vaguely implied. No occasion or variant matrix.",
            3: "Specific audience and one pain point named. No consumption occasion. Jargon present but not all defined. No variant matrix if applicable.",
            4: "Audience, occasion, and problem all explicitly stated. Most jargon defined. Variant matrix present if applicable.",
            5: "All five elements fully met: precise audience identity, consumption occasion defined, named problem + solution, variant matrix if applicable, all jargon defined in plain language.",
        },
        "sources": [
            "CXL positioning framework",
            "NNG — comparison shopping behaviour research",
            "Jobs-to-be-Done framework (Christensen)",
        ],
    },
    "objection_handling": {
        "description": (
            "The PDP must proactively identify and resolve common purchase anxieties before the user "
            "reaches checkout. Global cart abandonment averages 70.19% — anticipating friction points "
            "on the page itself is a primary conversion lever. "
            "Evaluate six specific hesitation triggers independently."
        ),
        "elements": {
            "price_and_hidden_costs": (
                "Hidden fees and unexpected shipping costs cause 39% of cart abandonments — the single largest "
                "UX-related drop-off reason. Check that total costs and shipping thresholds are stated explicitly "
                "near the primary Buy Box (not buried in footer or a separate FAQ). "
                "Also verify a clear toggle between one-time purchase and subscription pricing to prevent "
                "price confusion at checkout. "
                "Check pricing.prices_found, body_copy for shipping language, and cta_buttons for subscription toggle signals."
            ),
            "taste_and_texture_reassurance": (
                "Ingestible supplements carry negative industry stereotypes (chalky, gritty, artificial aftertaste). "
                "Check that the copy explicitly counters sensory objections with specific reassurance language "
                "(e.g. 'Smooth and delicious, even with just water', 'No chalky texture', 'Naturally flavoured'). "
                "Generic 'great taste' claims without specificity score partial. "
                "Scan paragraphs and bullet_points for sensory/texture language."
            ),
            "subscription_flexibility_messaging": (
                "Fear of being locked into a difficult-to-cancel subscription is a significant purchase barrier. "
                "Check the area immediately adjacent to the Subscribe & Save toggle for prominent flexibility "
                "messaging: 'Cancel anytime', 'Skip a delivery', 'No commitment'. "
                "This language must appear near the subscription option — not only in T&Cs or footer. "
                "Check cta_buttons and body_copy near subscription-related terms."
            ),
            "efficacy_and_risk_reversal": (
                "Efficacy scepticism raises the psychological threshold for purchase. "
                "Verify the presence of an explicit, visually prominent risk reversal — typically a "
                "'30 Day Money Back Guarantee' badge or a clear return policy summary near the Buy Box. "
                "A return policy linked only in the footer scores 1. A prominent guarantee badge near the CTA scores 5. "
                "Check trust_signals and body_copy for guarantee language."
            ),
            "usage_and_technical_faq": (
                "Check for a contextual accordion-style FAQ directly in the user's path on the PDP "
                "(not a detached footer link). The FAQ must address product-specific technical questions: "
                "allergens, caffeine content, age suitability, mixing instructions, or contraindications. "
                "Generic shipping/returns-only FAQs score 1. Product-specific FAQ in-page scores 5. "
                "Infer from h2s, h3s, and bullet_points whether FAQ content is present and product-specific."
            ),
            "variant_selection_friction": (
                "Complex variant selection causes 18% of users to abandon carts. "
                "Verify that size and flavour selectors use visible button-style selectors rather than "
                "hidden dropdown menus. Dropdowns require two interactions (click + select) and hide options "
                "from view, increasing cognitive load. "
                "Infer from structured_data (product variants) and body_copy whether selection UI is described; "
                "flag [VISUAL INSPECTION REQUIRED] as this requires screenshot review to confirm button vs dropdown."
            ),
        },
        "scoring": {
            1: "No product-specific objection handling. Generic FAQ or none. No guarantee. No shipping clarity near Buy Box.",
            2: "Shipping cost stated. One product objection addressed (taste or efficacy). No subscription flexibility. No FAQ in-path.",
            3: "3 of 6 elements present. Guarantee or risk reversal present. Subscription messaging absent or in footer only.",
            4: "4-5 of 6 elements present. In-path FAQ with product-specific content. Guarantee near CTA. Subscription flexibility stated.",
            5: "All 6 elements fully met: shipping clarity + toggle near Buy Box, taste reassurance, subscription flexibility adjacent to toggle, guarantee badge near CTA, in-path product FAQ, button-style variant selectors.",
        },
        "sources": [
            "Baymard Institute — cart abandonment research (70.19% global average)",
            "Baymard — hidden costs as #1 abandonment reason (39%)",
            "Baymard — complex checkout causes 18% abandonment",
        ],
    },
    "trust_signals": {
        "description": (
            "The page must systematically combat consumer scepticism endemic to the food supplement industry. "
            "Efficacy claims are routinely doubted — trust signals serve as psychological shortcuts that lower "
            "the cognitive barrier to purchase. Evaluate five elements across two categories: "
            "subjective social proof and objective authoritative validation."
        ),
        "elements": {
            "prominent_aggregate_ratings": (
                "A star rating and total review count must be placed immediately below the H1. "
                "The review count threshold of 50+ reviews is empirically linked to a 4.6% conversion uplift. "
                "Check reviews.rating and reviews.review_count. "
                "Rating present but below H1, or review count below 50, scores partial."
            ),
            "authenticity_and_negative_review_visibility": (
                "Consumers actively seek 1-2 star reviews to assess worst-case scenarios. "
                "Aggressively filtering negative reviews damages credibility. "
                "Check for: interactive review filtering (by star rating), 'verified buyer' badges, "
                "and visibility of negative reviews. "
                "Infer from review_snippets and body_copy whether filtering tools or verification badges are present. "
                "Flag [VISUAL INSPECTION REQUIRED] to confirm interactive filter UI."
            ),
            "independent_certifications": (
                "Visible third-party certification badges (Informed Sport, B-Corp, Organic, Non-GMO, GMP, ISO) "
                "must be positioned near the ATF section or nutritional panel — not only in the footer. "
                "Informed Sport is particularly critical for athletes and military personnel (prohibited substance assurance). "
                "Check trust_signals and image_alts for certification keywords. "
                "Location (ATF vs footer) requires [VISUAL INSPECTION REQUIRED]."
            ),
            "batch_testing_transparency": (
                "Explicit reassurance about testing for heavy metals (lead, arsenic, mercury), pesticides, "
                "and contaminants elevates scientific credibility. "
                "Best practice: direct links to view actual batch test certificates. "
                "Check trust_signals, body_copy, and paragraphs for batch testing, heavy metal, or contaminant language. "
                "Mention of testing without certificate links scores partial; linked certificates score full marks."
            ),
            "expert_and_authority_endorsements": (
                "Credentialed expert involvement — clinical nutritionists, registered dietitians, medical doctors — "
                "lends scientific weight and differentiates from generic supplement brands. "
                "Check paragraphs, bullet_points, and trust_signals for formulator credentials, "
                "expert advisory boards, or named professional endorsements. "
                "Named credentials with a title (e.g. 'Formulated by Dr. X, RD') score higher than "
                "generic 'nutritionist approved' claims."
            ),
        },
        "scoring": {
            1: "No trust signals present. No rating, no certifications, no expert endorsement, no batch testing.",
            2: "Star rating present below H1. Review count below 50 or absent. No certifications or expert content.",
            3: "Rating + 50+ reviews. One certification visible. No batch testing or expert endorsement.",
            4: "Rating + 50+ reviews + 1+ ATF certification + review filter or verified badges. Batch testing or expert endorsement present.",
            5: "Full trust stack: 50+ reviews with filter/verified badges near H1, 1+ ATF certification (ideally Informed Sport), batch test transparency with certificate link, named expert credentials.",
        },
        "sources": [
            "BrightLocal Consumer Review Survey",
            "Baymard #593 — trust signal placement research",
            "Spiegel Research Centre — review count and conversion (4.6% uplift at 50 reviews)",
            "Informed Sport — prohibited substance certification standard",
        ],
    },
    "claims_compliance": {
        "description": (
            "Act as a strict regulatory auditor. For UK DTC supplement brands, compliance is the most legally "
            "precarious dimension of the PDP — failure represents an existential legal threat (ASA enforcement, "
            "MHRA product reclassification, or ban from sale). "
            "The foundational legal principle: supplements are classified as FOOD, not medicine. "
            "The agent must not invent claims. Every flagged sentence must be quoted verbatim from "
            "compliance_flags.flagged_sentences or body_copy. Never flag language not present in the data. "
            "Evaluate six risk categories independently."
        ),
        "risk_categories": {
            "medicinal_claims": (
                "ABSOLUTE ZERO TOLERANCE. Any language stating or implying the product prevents, treats, "
                "or cures a human disease or clinical condition. Qualifier words ('may help', 'supports') "
                "do not make a prohibited claim compliant if the underlying meaning is medicinal. "
                "Flag any reference to: named diseases (diabetes, cancer, heart disease, osteoporosis), "
                "clinical conditions (anxiety disorder, insomnia, ADHD, depression), "
                "or pharmacological actions (anti-inflammatory, anti-bacterial, anti-viral). "
                "These trigger immediate MHRA/ASA enforcement action. "
                "Source: MHRA 'A guide to what is a medicinal product'; ASA CAP Code 15.6."
            ),
            "testimonial_loopholes": (
                "A brand cannot use customer reviews to bypass the medicinal/health claims ban. "
                "A displayed review stating 'This cured my anxiety' or 'Fixed my IBS' carries the same "
                "legal weight as a brand-authored claim and is equally prohibited. "
                "Check review_snippets for medicinal language. Flag any review that makes a disease, "
                "treatment, or cure claim. "
                "Source: ASA CAP Code 15 — applies equally to testimonials."
            ),
            "nutrient_attribution_failures": (
                "Health benefits cannot legally be attributed to the product name or brand. "
                "The benefit must be explicitly linked to a specific active nutrient. "
                "PROHIBITED: 'Daily Greens improves your focus'. "
                "REQUIRED: 'Contains Iron, which contributes to normal cognitive function' "
                "(using verbatim GB NHC Register wording). "
                "Flag any sentence where a health benefit is attributed to the product, blend, or brand "
                "name rather than to a named, registered nutrient. "
                "Source: UK Regulation 1924/2006 Art. 13."
            ),
            "ghc_shc_adjacency_failures": (
                "ASA enforces a strict distinction between General Health Claims (GHCs) and "
                "Specific Health Claims (SHCs). A GHC (e.g. 'superfood', 'detoxifier', 'promotes a healthy heart', "
                "'supports your immune system') is only lawful if placed immediately adjacent to a relevant "
                "authorised SHC from the GB NHC Register. "
                "Flag any GHC that stands alone without an adjacent registered SHC. "
                "Common unlawful standalone GHCs: 'superfood', 'detox', 'gut health', 'brain health', "
                "'energy booster', 'hormone balance'. "
                "Source: ASA CAP Code 15.1; EC Regulation 1924/2006 Art. 10(3)."
            ),
            "normal_phrasing_violations": (
                "Registered SHCs use precise statutory language — particularly the word 'normal'. "
                "Replacing 'normal' with marketing language is a direct compliance breach. "
                "PROHIBITED substitutions: "
                "'boosts your metabolism' (registered: 'contributes to normal energy-yielding metabolism'), "
                "'improves immune function' (registered: 'contributes to the normal function of the immune system'), "
                "'increases energy' (registered: 'contributes to the reduction of tiredness and fatigue'). "
                "Flag any sentence where a registered claim's statutory wording has been altered, "
                "abbreviated, or substituted with marketing language. "
                "Source: GB NHC Register — verbatim wording is mandatory."
            ),
            "high_risk_buzzwords_and_novel_foods": (
                "The following popular marketing terms are legally considered specific health claims "
                "requiring registered substantiation and must not be used without it: "
                "'Adaptogen', 'Nootropic', 'Antioxidant' (when used as a health claim rather than a descriptor). "
                "Additionally, flag any ingredient in bullet_points or body_copy that may be an unauthorised "
                "novel food or pharmacological substance: Turkey Tail mushroom, Lion's Mane, CBD, "
                "Kratom, Kava, high-dose melatonin, or any substance not on the GB Novel Food Register. "
                "Novel food violations risk MHRA reclassification of the entire product as a medicine. "
                "Source: UK Novel Food Regulation (retained EU 2015/2283); MHRA enforcement guidance."
            ),
        },
        "scoring": {
            1: "One or more medicinal claims or novel food violations present. Immediate enforcement risk.",
            2: "No medicinal claims but multiple GHC/SHC failures, nutrient attribution errors, or high-risk buzzwords without substantiation.",
            3: "Some claims use registered wording; others have phrasing drift or standalone GHCs. No medicinal claims. Testimonials not checked or minor risk present.",
            4: "All claims use registered wording or are factual. Minor adjacency or attribution issue. No medicinal claims or novel food flags.",
            5: "Full compliance: zero medicinal claims, all benefits attributed to named registered nutrients, GHCs adjacent to registered SHCs, verbatim statutory phrasing, no high-risk buzzwords without substantiation, no novel food flags.",
        },
        "mandatory_output_rules": (
            "1. Quote every flagged sentence verbatim from the page data — never paraphrase. "
            "2. State which risk category applies and cite the specific regulation breached. "
            "3. If no risk is found in a category, do not mention that category in flagged_claims. "
            "4. If no risk is found overall, set no_risk_found: true and state this explicitly in reasoning. "
            "5. Do not flag claims not present in the scraped data. "
            "6. All flags are risk indicators for mandatory human review — not confirmed violations."
        ),
        "regulation_urls": {
            "ASA CAP Code Section 15": "https://www.asa.org.uk/type/non_broadcast/code_section/15.html",
            "MHRA Blue Guide": "https://www.gov.uk/guidance/borderline-products-how-to-tell-if-your-product-is-a-medicine",
            "GB NHC Register": "https://www.gov.uk/government/publications/great-britain-nutrition-and-health-claims-nhc-register",
            "UK Regulation 1924/2006": "https://www.legislation.gov.uk/eur/2006/1924/contents",
            "UK Novel Food Regulation": "https://www.food.gov.uk/business-guidance/regulated-products/novel-foods-guidance",
        },
        "sources": [
            "UK Regulation 1924/2006 (retained in GB law post-Brexit)",
            "ASA CAP Code Section 15 — Health, Beauty and Slimming",
            "MHRA — A guide to what is a medicinal product (MHRA Blue Guide)",
            "GB Nutrition and Health Claims Register",
            "UK Novel Food Regulation (retained EU Regulation 2015/2283)",
            "EC Regulation 1924/2006 Art. 10(3) — GHC/SHC adjacency requirement",
        ],
    },
    "seo": {
        "description": (
            "Verify the page is technically optimised to capture high-intent, long-tail organic queries "
            "(e.g. 'vegan organic greens powder UK') using publicly visible on-page elements and structured data. "
            "Evaluate five elements using only scraped fields — no internal keyword data required."
        ),
        "elements": {
            "metadata_and_url_structure": (
                "The meta title must incorporate target keywords that accurately reflect the product "
                "(product name + category + key modifier, e.g. 'AI Greens Supergreens Powder | Protein Works'). "
                "The URL must be concise and use hyphens as word separators — not underscores, not parameters. "
                "Check meta.meta_title for keyword presence and specificity. "
                "Check meta.canonical_url for URL structure (hyphens, no query strings, no stop words). "
                "Also verify meta.og_title and meta.og_description are populated."
            ),
            "header_hierarchy": (
                "The H1 must be reserved exclusively for the primary product name — not a slogan, not the brand name alone. "
                "H2 and H3 tags must be used sequentially to structure benefits and ingredient sections "
                "without skipping hierarchy levels (H1 → H2 → H3 in order; no H3 before H2 has been used). "
                "Check headings.h1, headings.h2s, and headings.h3s. "
                "Flag any H1 that is a slogan or brand name only, and any hierarchy level skips."
            ),
            "product_schema_and_merchant_listings": (
                "Modern e-commerce SEO requires comprehensive JSON-LD Product schema to generate rich snippets. "
                "Check structured_data for a Product schema with all required fields mapped: "
                "name, brand, offers.price, offers.priceCurrency, offers.availability. "
                "Crucially, also check for nested Merchant listing schema detailing return policy and delivery "
                "specifications — required to qualify for Google Shopping tab advanced features. "
                "Flag any missing required fields. Flag absence of Merchant listing schema as a missed opportunity."
            ),
            "aggregate_rating_schema_compliance": (
                "Incorrect AggregateRating schema can cause severe manual penalties and loss of rich snippets. "
                "Check structured_data for AggregateRating schema. If present, verify: "
                "(1) ratingValue and reviewCount in schema exactly match the visible numbers in reviews.rating and reviews.review_count — any mismatch is a penalty risk; "
                "(2) schema reflects only reviews from this specific product page, not store-wide or aggregated third-party ratings; "
                "(3) the reviews marked up in schema must be explicitly visible to the user in the page HTML. "
                "Flag store-wide review aggregation or third-party rating markup as a manual penalty risk."
            ),
            "breadcrumb_navigation": (
                "Breadcrumb navigation helps search engines understand site hierarchy and displays a meaningful "
                "trail in search results (e.g. Home > Sports Nutrition > Greens Powders > AI Greens). "
                "Check structured_data for BreadcrumbList schema. "
                "Also infer from body_copy and headings whether visible breadcrumb text is present on the page. "
                "Schema without visible breadcrumbs scores partial — both are required for full marks."
            ),
        },
        "scoring": {
            1: "Primary keyword absent from meta title and H1. No Product schema. No breadcrumbs.",
            2: "Keyword in meta title but not H1. Basic Product schema present but fields incomplete. No AggregateRating schema or Merchant listing.",
            3: "Primary keyword in H1 and meta title. Product schema with required fields. No Merchant listing. No breadcrumbs or AggregateRating schema.",
            4: "Keyword-optimised meta title and H1. Full Product schema + AggregateRating schema matching visible reviews. Breadcrumbs present. Merchant listing absent.",
            5: "All five elements fully met: keyword-rich meta + canonical + OG tags, sequential header hierarchy, complete Product schema + Merchant listing, AggregateRating schema matching visible page data, BreadcrumbList schema + visible breadcrumbs.",
        },
        "sources": [
            "Google Search Central — Product structured data requirements",
            "Google Search Central — Merchant listing schema",
            "Google Search Central — AggregateRating guidelines and manual action triggers",
            "Google Search Central — BreadcrumbList schema",
            "Moz On-Page SEO guide",
        ],
    },
    "visual_gallery": {
        "description": "Image alt text is descriptive; gallery covers key product angles.",
        "scoring": {
            1: "All images have empty or generic alt text.",
            2: "Product name in alt text only.",
            3: "Alt text includes product + flavour/format.",
            4: "Alt text keyword-rich. Gallery has 3+ angles.",
            5: "Alt text follows pattern 'Product – Angle – Key Benefit'. 5+ gallery images with lifestyle shot.",
        },
        "sources": ["Baymard image gallery study #602", "WebAIM alt text guidance"],
    },
    "dtc_benchmark": {
        "description": (
            "Page meets best-practice DTC e-commerce benchmarks across four absolute sub-criteria. "
            "Each sub-criterion is scored independently against defined best-practice standards — "
            "no competitor comparison required. Score is the mean of sub-criteria scores, rounded."
        ),
        "sub_criteria": {
            "above_fold_completeness": {
                "description": (
                    "Top of mobile screen anchors user expectations within milliseconds — "
                    "zero scrolling needed to grasp core value. "
                    "Check: descriptive H1, outcome-driven sub-headline, unit price per serving "
                    "(e.g. '£1.50/serving'), star rating visible, primary CTA with strong contrast, "
                    "sticky Add-to-Cart bar present."
                ),
                "scoring": {
                    1: "H1 generic or missing. No price, rating, or CTA above fold.",
                    2: "H1 present but no benefit framing. Price or rating missing. No sticky bar.",
                    3: "H1 + price + CTA present. Per-serving price or rating absent. No sticky bar.",
                    4: "H1, sub-headline, per-serving price, and rating all present. CTA visible. Sticky bar absent.",
                    5: "All six elements present: H1, outcome sub-headline, per-serving price, star rating, high-contrast CTA, sticky Add-to-Cart bar.",
                },
            },
            "scannability": {
                "description": (
                    "Page avoids walls of spec text. Core features use a 3-part layout: "
                    "icon/image + short outcome headline + brief paragraph. "
                    "Nutritional data is in a single-column table, not inline prose. "
                    "Penalise dense paragraph blocks for primary product benefits."
                ),
                "scoring": {
                    1: "Benefits presented as dense paragraphs only. No visual pairing. Nutrition inline.",
                    2: "Some bullets present but no icon/image pairing. Nutrition still in prose.",
                    3: "Bullet-point benefits. Partial icon pairing. Nutrition table present.",
                    4: "Clear 3-part layout for 3+ features. Nutrition table. Minimal prose walls.",
                    5: "Full 3-part layout (icon + headline + blurb) for all core features. Nutrition in table. Zero spec prose walls.",
                },
            },
            "mobile_structure": {
                "description": (
                    "All content readable single-column without horizontal scrolling. "
                    "Image gallery uses visible thumbnails beneath main image "
                    "(not dot indicators, which cause users to miss supplementary images). "
                    "FAQs and spec sheets are single-column. "
                    "Evaluate using the actual images provided — look for thumbnail strip vs dot navigation."
                ),
                "scoring": {
                    1: "Multi-column layout visible. Dots only for gallery. Horizontal scroll required.",
                    2: "Single-column text but gallery uses dots. No thumbnail strip.",
                    3: "Single-column. Gallery has some thumbnail visibility but dots dominant.",
                    4: "Single-column throughout. Thumbnail strip present but small.",
                    5: "Fully single-column. Gallery has clearly visible full thumbnails. No dots.",
                },
            },
            "page_performance": {
                "description": (
                    "Technical performance signals: LCP target < 2.5s (load > 3s causes 53% mobile abandonment). "
                    "CLS target < 0.1 (high-res images must not shift layout on load). "
                    "NOTE: LCP and CLS cannot be measured from HTML scraping alone — "
                    "score this dimension based on observable proxies: "
                    "presence of lazy-loading attributes, image dimensions specified in HTML, "
                    "use of CDN URLs, absence of render-blocking inline scripts. "
                    "Flag this dimension with [REQUIRES LIGHTHOUSE TEST] in reasoning."
                ),
                "scoring": {
                    1: "No lazy loading. No image dimensions. Inline blocking scripts visible. CDN absent.",
                    2: "Some lazy loading but dimensions missing. Mixed CDN usage.",
                    3: "Lazy loading present. Most images have dimensions. CDN URLs used.",
                    4: "Full lazy loading, dimensions set, CDN URLs, minimal inline scripts.",
                    5: "All performance proxies optimal. [REQUIRES LIGHTHOUSE TEST] to confirm LCP/CLS.",
                },
            },
        },
        "scoring": {
            1: "3 or more sub-criteria at score 1–2. Page fails basic DTC standards.",
            2: "2 sub-criteria at score 1–2. Significant gaps in ATF completeness or scannability.",
            3: "All sub-criteria at score 3 or mixed 2/4. Meets basic standards but not best practice.",
            4: "All sub-criteria at score 3–4. One sub-criterion at 2 maximum.",
            5: "All four sub-criteria score 4 or 5. Page meets full DTC best-practice standard.",
        },
        "sources": [
            "Baymard mobile UX research",
            "Google Core Web Vitals (LCP < 2.5s, CLS < 0.1)",
            "NNG mobile scrolling behaviour",
        ],
    },
}

ACTOR_SYSTEM_INSTRUCTIONS = """
You are a senior conversion rate optimisation analyst specialising in UK direct-to-consumer sports nutrition and wellness brands regulated under UK food supplement law.

Your job is NOT to rewrite the page.
Your job is NOT to generate plausible-sounding copy.

Your job is to produce a prioritised, structured set of commercially useful recommendations that a marketer can act on immediately — recommendations that will actually move conversion, not surface-level nitpicks.

Every recommendation must earn its place by answering three questions:
  1. What specifically is wrong on THIS page?
  2. Why does it matter commercially?
  3. What exactly should the marketer do about it?

STRICT RULES — NEVER VIOLATED

RULE 1 — EVIDENCE REQUIRED
Every finding must cite specific evidence. Use the right format for each context:

  In element_checks: plain English description of what was found.
    
  In recommendation evidence field: use citation tags.
    Format: [DATA: field_name | "exact value"] for page data
    Format: [VISUAL: exact observation] for screenshot observations

If you cannot cite evidence, you cannot make the claim. Never invent or assume page content.

RULE 2 — COMPLIANCE FLAGGING ONLY
For health and efficacy claims:
  Flag: yes — always flag sentences with health language
  Rule: never — you may never rule a claim legal or illegal
  Format: ⚠ COMPLIANCE FLAG — human review required against GB NHC Register before any change
Do not add interpretations beyond this.

RULE 3 — SPECIFICITY REQUIRED
Every recommendation must be specific to THIS page. If the recommendation could apply to any ecommerce page without changing a single word, it is too generic. Reject it and find the page-specific version instead.

RULE 4 — BEFORE/AFTER REQUIRED FOR COPY CHANGES
Any recommendation that involves changing words on the page must include:
  BEFORE: exact current text from the page
  AFTER: specific suggested replacement
The AFTER text must use only authorised GB NHC Register claim phrasing for any health or efficacy language.

RULE 5 — TRIAGE EVERY RECOMMENDATION
Every recommendation must be categorised as either:
  SAFE TO ACTION — no compliance or brand risk, marketer can implement immediately
  NEEDS SIGN-OFF — involves health claims, brand voice changes, pricing, or customer-facing copy that requires human approval

SCORING — EVERY DIMENSION MUST BE SCORED

Score each dimension 1-5 using the rubric provided.
For every dimension write:
  aligned: what the page does well (cite evidence — do not leave blank)
  misaligned: what the page fails at (cite evidence — be precise)
  score: 1-5
  score_rationale: one-two sentence explaining the score

PRIORITISATION LOGIC

Rank recommendations using this formula:
  Priority score = impact_score × 2 + (6 - effort_score)

Where priority scores are equal:
  Compliance flags rank above non-compliance findings.
  Above-fold issues rank above below-fold issues.
  Missing elements rank above improvable elements.

The single highest-ranked recommendation is your HEADLINE FINDING.

QUALITY BAR — BEFORE RETURNING OUTPUT ASK YOURSELF:
1. Does every recommendation have a BEFORE and AFTER?
2. Does every finding cite specific evidence?
3. Have I flagged every compliance-sensitive sentence?
4. Is my rank 1 recommendation genuinely the highest value finding?
5. Would a marketer read this and know exactly what to do tomorrow morning?
"""

_RUBRIC_ABBREVIATIONS = [
    ("above-the-fold", "ATF"),
    ("above the fold", "ATF"),
    ("call to action", "CTA"),
    ("call-to-action", "CTA"),
    ("search engine optimisation", "SEO"),
    ("search engine optimization", "SEO"),
    ("direct-to-consumer", "DTC"),
    ("direct to consumer", "DTC"),
    ("product detail page", "PDP"),
    ("Great Britain Nutrition and Health Claims Register", "GB NHC Register"),
    ("Advertising Standards Authority", "ASA"),
    ("Committee of Advertising Practice", "CAP Code"),
    ("Nielsen Norman Group", "NNG"),
]

def _abbreviate(text: str) -> str:
    for full, short in _RUBRIC_ABBREVIATIONS:
        text = text.replace(full, short)
        text = text.replace(full.title(), short)
    return text

ACTOR_RUBRIC_TEXT = _abbreviate("RUBRIC:\n" + json.dumps(RUBRIC, indent=2))


def _slim_for_actor(scraped_json: dict) -> dict:
    s = scraped_json.get("structured", {})
    full_md = scraped_json.get("full_markdown", "")

    import re as _re

    # Shipping/subscription signals for objection_handling evaluation
    shipping_hits = _re.findall(
        r"[^\n]{0,80}(?:deliver|shipping|dispatch|subscribe|subscription|cancel anytime|skip|free over|save \d+%)[^\n]{0,80}",
        full_md, _re.IGNORECASE
    ) if full_md else []
    body_copy_excerpt = "\n".join(dict.fromkeys(shipping_hits[:10]))

    # Body copy — strip markdown links and pass full page text so the actor
    # can quote accurately and find content that Firecrawl missed in structured fields
    # (e.g. nutritionist quotes, expert names, trust signals buried in body copy).
    # Cap at 5000 chars (~1250 tokens) starting from the H1.
    body_copy_for_quoting = ""
    if full_md:
        h1 = s.get("h1", "")
        start = full_md.find(h1) if h1 else 500
        if start == -1:
            start = 500
        raw = full_md[start:start + 5000]
        raw = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw)
        body_copy_for_quoting = raw.strip()

    return {
        "url": scraped_json.get("url"),
        "body_copy": body_copy_for_quoting or None,
        "structured": {
            "product_name": s.get("product_name"),
            "h1": s.get("h1"),
            "meta_title": s.get("meta_title"),
            "meta_description": s.get("meta_description"),
            "price": s.get("price"),
            "price_per_serving": s.get("price_per_serving"),
            "flavours_available": s.get("flavours_available", []),
            "key_benefits": s.get("key_benefits", [])[:8],
            "ingredient_highlights": s.get("ingredient_highlights", [])[:8],
            "review_rating": s.get("review_rating"),
            "review_count": s.get("review_count"),
            "review_snippets": s.get("review_snippets", [])[:3],
            "trust_signals": s.get("trust_signals", [])[:5],
            "cta_buttons": s.get("cta_buttons", [])[:4],
            "faq_items": s.get("faq_items", []),
            "image_descriptions": s.get("image_descriptions", [])[:10],
            "image_alts": scraped_json.get("gallery_image_alts", []),
            "nutritionist_quote": s.get("nutritionist_quote"),
            "compliance_sensitive_sentences": s.get("compliance_sensitive_sentences", []),
            "nutritional_information": s.get("nutritional_information"),
            "ingredients_list": s.get("ingredients_list"),
            "shipping_info": s.get("shipping_info"),
            "subscription_info": s.get("subscription_info"),
            "shipping_subscription_signals": body_copy_excerpt or None,
        },
    }


def _fetch_product_images_from_urls(urls: list[str]) -> list:
    """Fetch a list of image URLs directly and return as LangChain content blocks."""
    import base64 as _b64
    import requests as _req

    _IMG_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.theproteinworks.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    lc_parts = []
    for url in urls:
        try:
            resp = _req.get(url, timeout=8, headers=_IMG_HEADERS)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                ct = "image/jpeg"
            b64 = _b64.b64encode(resp.content).decode()
            lc_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{ct};base64,{b64}"},
            })
        except Exception as e:
            print(f"  ⚠ Could not fetch image {url}: {e}")
    return lc_parts


def _fetch_product_images(full_markdown: str, max_images: int = 5) -> list:
    """Extract product gallery image URLs from markdown and fetch as Gemini inline image parts."""
    import re as _re
    import base64 as _b64
    import requests as _req

    # Extract image URLs — filter to product/gallery images, skip tiny icons/badges
    all_urls = _re.findall(
        r'https?://[^\s\)\"\']+'
        r'\.(?:jpg|jpeg|png|webp)'
        r'[^\s\)\"\',]*',
        full_markdown, _re.IGNORECASE
    )
    # Deduplicate preserving order; use images as-is (don't rewrite CDN sizes)
    seen = set()
    product_urls = []
    for url in all_urls:
        base = url.split("?")[0]
        if base in seen:
            continue
        seen.add(base)
        # Skip very small thumbnails (w≤120) — keep everything else including w=300, w=560 etc.
        w_match = _re.search(r'[?&]w=(\d+)', url)
        if w_match and int(w_match.group(1)) <= 120:
            continue
        product_urls.append(url)
        if len(product_urls) >= max_images:
            break

    _IMG_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.theproteinworks.com/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    # Return LangChain-format (Gemini API) image content blocks
    lc_parts = []
    for url in product_urls:
        try:
            resp = _req.get(url, timeout=8, headers=_IMG_HEADERS)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                ct = "image/jpeg"
            b64 = _b64.b64encode(resp.content).decode()
            lc_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{ct};base64,{b64}"},
            })
        except Exception as e:
            print(f"  ⚠ Could not fetch image {url}: {e}")
    return lc_parts


def _build_user_message(
    target_json: dict,
    reflection: str | None,
    pass_number: int,
) -> str | list:
    template = _jinja.get_template("actor_prompt.j2")
    layout_signals = target_json.get("layout_signals")
    atf_b64 = target_json.get("atf_screenshot_base64")
    has_atf_screenshot = bool(atf_b64)

    # Fetch product gallery images as LangChain content blocks
    # Prefer scraper-captured gallery URLs (clicked through thumbnails); fall back to markdown extraction
    gallery_urls = target_json.get("gallery_image_urls", [])
    if gallery_urls:
        image_blocks = _fetch_product_images_from_urls(gallery_urls)
        print(f"  ✓ {len(image_blocks)} gallery image(s) fetched from scraper (thumbnail clicks)")
    else:
        full_md = target_json.get("full_markdown", "")
        image_blocks = _fetch_product_images(full_md) if full_md else []
        if image_blocks:
            print(f"  ✓ {len(image_blocks)} product image(s) fetched from markdown (fallback)")
    has_images = bool(image_blocks)

    text = template.render(
        target_json=json.dumps(_slim_for_actor(target_json), indent=2),
        layout_signals_json=json.dumps(layout_signals, indent=2) if layout_signals and "error" not in layout_signals else None,
        reflection=reflection,
        pass_number=pass_number,
        has_images=has_images,
        has_atf_screenshot=has_atf_screenshot,
    )

    if not atf_b64 and not image_blocks:
        return text

    # Build LangChain multimodal content list
    content = []

    if atf_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{atf_b64}"},
        })

    prefix = (
        ("Above is the ATF screenshot (390×844px mobile, no scroll). "
         "Use it to score headline_clarity and dtc_benchmark/above_fold_completeness based on what is ACTUALLY VISIBLE.\n\n"
         if atf_b64 else "") +
        (f"The following {len(image_blocks)} product gallery image(s) are attached. "
         "Use them to evaluate visual_gallery (angles, lifestyle shots, benefit messaging, alt text accuracy) "
         "and dtc_benchmark/mobile_structure (thumbnail vs dot navigation).\n\n"
         if image_blocks else "")
    )
    content.append({"type": "text", "text": prefix + text})
    content.extend(image_blocks)
    return content


@traceable(name="actor_audit", metadata={"model": ACTOR_MODEL})
def run_actor(
    target_json: dict,
    rubric: dict,
    reflection: str | None = None,
    pass_number: int = 1,
) -> dict:
    system_prompt = ACTOR_RUBRIC_TEXT + "\n\n" + ACTOR_SYSTEM_INSTRUCTIONS
    user_message = _build_user_message(target_json, reflection, pass_number)

    # Token counting only works on plain text — skip if multimodal
    if isinstance(user_message, str):
        token_count = count_gemini_tokens(system_prompt, user_message, ACTOR_MODEL)
        print(f"  Actor input tokens (pass {pass_number}): {token_count:,}")
    else:
        print(f"  Actor input tokens (pass {pass_number}): multimodal — screenshot included")

    response_text = call_gemini(
        system_prompt=system_prompt,
        user_message=user_message,
        model_name=ACTOR_MODEL,
        max_tokens=65000,
        require_json=True,
        caller=f"actor/pass{pass_number}",
    )

    audit = parse_json_response(response_text)

    # Guard: if the model nested the real audit inside page_audited (a known failure mode),
    # unwrap it so downstream code always gets the expected top-level structure.
    if isinstance(audit.get("page_audited"), dict) and "dimension_scores" in audit.get("page_audited", {}):
        audit = audit["page_audited"]

    return audit


