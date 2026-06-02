# PW PDP Optimisation Agent

An AI-powered product page audit system for Protein Works. Give it any live PDP URL and it returns a scored audit across 9 conversion-critical dimensions, a compliance risk check against UK food supplement regulations, and a full suggested page rewrite — in minutes, on any page, repeatedly.

---

## What It Does

**Stage 1 — Scrape.** Firecrawl extracts ~25 structured fields from the rendered page: product name, H1, meta tags, price, per-serving cost, flavours, key benefits, ingredients, reviews, trust signals, CTAs, FAQ items, compliance-sensitive sentences, and a 390×844 px above-the-fold screenshot. A post-processing guard corrects cases where the scraper returns the meta title in the H1 field. Product images are fetched with browser-like headers (including `Referer`) and passed as base64 content blocks to the Actor. Tavily is the fallback if Firecrawl fails.

**Stage 2 — Reflexion audit.** A Reflexion loop runs the audit. The **Actor** (Gemini 2.5 Flash via LangChain) scores the page across 9 dimensions using a structured rubric. The **Evaluator** (also Gemini 2.5 Flash) checks every recommendation against 4 binary quality criteria, including a special guard against ATF visibility checks that quote structured data instead of the screenshot. The **Reflector** (pure Python, no LLM) generates targeted critique where criteria fail. The Actor revises on a second pass if needed. Maximum 2 passes.

**Stage 3 — Rewrite.** **Claude Sonnet 4.6** (Anthropic) generates a full page rewrite: meta title, H1, sub-headline, benefit highlights, objection-handling FAQ, and trust signal block. The system prompt injects Protein Works brand voice examples and anti-hallucination rules that forbid inventing gram amounts, serving counts, or prices not present verbatim in the scraped data. Every compliance-sensitive sentence is flagged `[HUMAN REVIEW REQUIRED]`. The rewriter runs **concurrently** with Pass 2 of the reflexion loop via a `threading.Event` signal, saving 30–120 s on single-pass audits.

**Every run** is traced end-to-end in LangSmith (token usage reported automatically via LangChain) and saved to a local SQLite database. Token counts (Gemini + Claude) are accumulated per run via a thread-local store and persisted in `scores_json._tokens` for the Observability Dashboard.

---

## The 9 Audit Dimensions

| Dimension | What it evaluates |
|---|---|
| **Headline Clarity** | ATF mobile viewport: descriptive H1, outcome sub-headline, per-serving price, star rating, high-contrast CTA, 44px touch target |
| **Benefit Hierarchy** | Outcome-first framing, 3-part icon/headline/paragraph layout, 2–6 highlights, nutritional detail separated from persuasive copy |
| **Product Positioning** | Target audience identity, consumption occasion, named pain point + solution, variant comparison, proprietary jargon defined |
| **Objection Handling** | Shipping costs near Buy Box, taste/texture reassurance, subscription flexibility, money-back guarantee, in-path FAQ, variant selector UX |
| **Trust Signals** | 50+ reviews with filter/verified badges near H1, Informed Sport or equivalent cert ATF, batch test transparency, named expert credentials |
| **Claims Compliance** | Medicinal claims, testimonial loopholes, nutrient attribution failures, GHC/SHC adjacency, statutory phrasing violations, novel foods |
| **SEO** | Meta title + URL structure, header hierarchy, Product schema, AggregateRating schema vs visible reviews, BreadcrumbList |
| **Visual Gallery** | Alt text descriptiveness, gallery depth and angles, benefit messaging on images, lifestyle shot presence |
| **DTC Benchmark** | Above-fold completeness, scannability, mobile structure, page performance proxies |

Recommendations are sorted by highest impact first, lowest effort second.

---

## Project Structure

```
PW-TASK/
├── api.py                  # FastAPI backend — audit endpoints + LangSmith observability
├── main.py                 # Pipeline orchestrator (Reflexion loop + concurrent rewriter)
├── scraper.py              # Firecrawl primary + Tavily fallback + H1/meta fix
├── database.py             # SQLite audit history
├── layers/
│   ├── actor.py            # Reflexion Actor — Gemini 2.5 Flash via LangChain
│   ├── evaluator.py        # Reflexion Evaluator — 4-criteria quality check
│   ├── reflector.py        # Reflexion Reflector — pure Python critique generator
│   └── rewriter.py         # Page copy rewriter — Claude Sonnet 4.6
├── prompts/
│   ├── actor_prompt.j2     # Jinja2 audit prompt (rubric + ATF citation rules)
│   ├── evaluator_prompt.j2 # Evaluator prompt (ATF hallucination guard)
│   └── rewriter_prompt.j2  # Rewriter prompt (brand voice + anti-hallucination)
├── utils/
│   ├── gemini_client.py    # LangChain ChatGoogleGenerativeAI wrapper + retry/fallback
│   └── token_store.py      # Thread-local token accumulator (Gemini + Claude)
├── static/
│   ├── index.html          # Single-page dashboard
│   ├── app.js              # Dashboard JS
│   ├── style.css           # Dashboard styles
│   ├── sample_kpi_data.js  # Sample KPI data for Business Impact section
│   ├── architecture.png    # System architecture diagram
│   ├── roadmap_30days.html # 30-day implementation roadmap (served in iframe)
│   └── pw.png              # Logo
├── data/
│   └── audits.db           # SQLite database (created on first run)
├── .env                    # API keys (never commit)
└── requirements.txt
```

---

## Setup

**1. Install dependencies**

```bash
pip install -r requirements.txt
playwright install chromium
```

**2. Configure API keys**

Create a `.env` file:

```
GOOGLE_API_KEY=
ANTHROPIC_API_KEY=
FIRECRAWL_API_KEY=
TAVILY_API_KEY=
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=pw-pdp-optimisation-agent
LANGCHAIN_TRACING_V2=true
```

**3. Run the web dashboard**

```bash
uvicorn api:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000). Enter any Protein Works URL and click **Run Audit**.

**4. Run from the CLI**

```bash
python main.py https://www.theproteinworks.com/ai-greens
```

---

## Dashboard

Six panels accessible from the sidebar:

### New Audit
URL input form. Submits a page for auditing and shows a live stage-by-stage progress bar (Scraping → Reflexion → Rewrite → Done).

### Audit Results
- Overall score badge and product name
- Mobile above-the-fold screenshot (390×844px)
- Radar chart across all 9 dimensions
- Expandable dimension cards with per-element ✓/✗/~ checks and reasoning
- Recommendations table sorted by impact (high → low) then effort (low → high)
- Compliance flags with verbatim quoted sentences
- Full suggested page rewrite (meta title, H1, benefits, FAQ, trust signals)

### Observability Dashboard (LangSmith)
Live view of all pipeline runs pulled directly from LangSmith:
- **Summary cards** — total runs, cost, tokens, success rate
- **Overview tab** — latency per product evaluation (Reflexion Loop + Rewriter + Overall) shown as a single bar with hover breakdown
- **Tokens & Cost tab** — token counts from SQLite (Gemini + Claude combined) and cost per run, each as a single bar with hover details
- **LLM Calls tab** — per-step aggregates (calls, avg tokens, avg latency, error rate, total cost)
- **Traces tab** — click any run to see a timeline of child spans with durations and a direct link to LangSmith

LangSmith endpoints use per-key TTL caches (embed-url: 1 h, metrics: 5 min, recent-runs: 2 min), pre-warmed in parallel threads 4 s after server startup. Cache is invalidated automatically when a new audit completes.

### Business Impact
Sample-data dashboard demonstrating how PDP optimisations affect conversion KPIs. Shows before/after data for three example products with line charts, KPI cards, and compliance flag counts.

> In production, replace `static/sample_kpi_data.js` with a live endpoint from GA4, Shopify, or your internal BI tool.

### System Architecture
Architecture diagram with annotated description of every pipeline stage.

### 30-Day Roadmap
Interactive implementation roadmap with expandable phases and a sticky progress tracker.

---

## Compliance

The system flags compliance risk — it does not auto-publish or automatically action any recommendation. Every health and efficacy claim is checked against:

- UK Regulation 1924/2006 (retained in GB law post-Brexit)
- ASA CAP Code Section 15 — Health, Beauty and Slimming
- MHRA Blue Guide — A guide to what is a medicinal product
- GB Nutrition and Health Claims Register
- UK Novel Food Regulation (retained EU Regulation 2015/2283)

Flagged sentences are routed to mandatory human review. The rewriter never invents wording not on the GB NHC Register, and anti-hallucination rules prevent fabricating specific numbers (gram amounts, serving counts, derived prices).

---

## Observability

Every pipeline run is traced in LangSmith with nested spans:

```
reflexion_loop
  ├── actor_audit        (pass 1)
  ├── evaluator          (pass 1)
  ├── actor_audit        (pass 2, conditional)
  └── evaluator          (pass 2, conditional)
rewriter                 (runs concurrently with pass 2)
```

Token usage (input + output) is reported automatically to LangSmith by LangChain's `ChatGoogleGenerativeAI` wrapper for Gemini calls, and captured locally via `utils/token_store.py` for both Gemini and Claude — stored in SQLite at audit completion.

---

## Key Design Decisions

**Concurrent rewriter** — the Rewriter starts as soon as Pass 1 actor finishes, running in a `ThreadPoolExecutor` worker thread signalled by a `threading.Event`. On single-pass audits (the majority) this saves 30–120 s. On two-pass audits the rewriter re-runs with the corrected audit.

**LangChain wrapper** — Gemini calls use `langchain-google-genai`'s `ChatGoogleGenerativeAI` instead of the raw `google-genai` SDK. This enables automatic token reporting to LangSmith without any manual `update_run` calls.

**Thread-local token store** — `utils/token_store.py` uses `threading.local()` to accumulate Gemini and Claude tokens per pipeline run. Each `ThreadPoolExecutor` worker calls `set_current_run(run_id)` at start so tokens are correctly attributed. Totals are persisted in `scores_json._tokens` in SQLite.

**Split model strategy** — Actor and Evaluator use Gemini 2.5 Flash (long context, multimodal, structured JSON). Rewriter uses Claude Sonnet 4.6 (superior constrained creative writing, better brand voice compliance, immune to Gemini 503 overload errors).

**Fallback on 503** — if Gemini 2.5 Flash returns a 503 (high demand), `call_gemini` immediately switches to `gemini-2.5-flash` as fallback without waiting, then continues the retry loop.

**ATF grounding rules** — the Actor prompt mandates screenshot-based citations for all ATF element checks. The Evaluator flags any ATF `pass` that quotes a structured data value (e.g. a price from `structured.price`) rather than a screenshot observation as a false positive.

**Per-key cache TTLs** — LangSmith endpoints use different TTLs: embed-url (1 h), metrics (5 min), recent-runs (2 min). Cache is invalidated on audit completion so the Observability Dashboard always reflects the latest run.
