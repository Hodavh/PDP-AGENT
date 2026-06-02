
const STAGES = [
  "Scraping target page",
  "Detecting product category",
  "Finding competitor URLs",
  "Scraping competitor pages",
  "Running Reflexion audit",
  "Rewriting page copy",
  "Done",
];

let _pollTimer = null;
let _activeRunId = null;
let _activeRunUrl = null;

// ── Panel helpers ──────────────────────────────────────────────────────────────

function showPanel(name) {
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.getElementById("panel-" + name).classList.add("active");
}

function showNewAudit() {
  showPanel("new");
  document.querySelectorAll(".audit-item").forEach(i => i.classList.remove("active"));
}

// ── Start audit ────────────────────────────────────────────────────────────────

async function startAudit() {
  const input = document.getElementById("url-input");
  const url = input.value.trim();
  const errEl = document.getElementById("url-error");

  if (!url || !url.startsWith("http")) {
    errEl.classList.remove("hidden");
    return;
  }
  errEl.classList.add("hidden");

  const res = await fetch("/api/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const { run_id } = await res.json();
  _activeRunId = run_id;
  _activeRunUrl = url;

  localStorage.setItem("pw_active_run", JSON.stringify({ run_id, url }));

  document.getElementById("progress-url").textContent = url;
  document.getElementById("progress-title").textContent = "Auditing page…";
  showPanel("progress");
  injectRunningItem(run_id, url);
  pollStatus(run_id);
}

// ── Polling ────────────────────────────────────────────────────────────────────

function pollStatus(run_id) {
  clearInterval(_pollTimer);
  _pollTimer = setInterval(async () => {
    const res = await fetch(`/api/audit/${run_id}/status`);
    const data = await res.json();

    const stageIdx = STAGES.indexOf(data.stage);
    const pct = stageIdx < 0 ? 5 : Math.round(((stageIdx + 1) / STAGES.length) * 100);
    document.getElementById("progress-bar").style.width = pct + "%";
    document.getElementById("progress-stage").textContent = data.stage || "Starting…";
    updateRunningStage(data.stage || "Starting…");

    if (data.status === "complete") {
      clearInterval(_pollTimer);
      localStorage.removeItem("pw_active_run");
      removeRunningItem();
      const resultRes = await fetch(`/api/audit/${run_id}/result`);
      const result = await resultRes.json();
      renderResults(result);
      loadSidebar();
    } else if (data.status === "error") {
      clearInterval(_pollTimer);
      localStorage.removeItem("pw_active_run");
      removeRunningItem();
      document.getElementById("progress-title").textContent = "Error";
      document.getElementById("progress-stage").textContent = data.error || "Unknown error";
    }
  }, 8000);
}

// ── Running item in sidebar ────────────────────────────────────────────────────

function injectRunningItem(run_id, url) {
  const list = document.getElementById("audit-list");
  const shortUrl = url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
  const existing = document.getElementById("running-item");
  if (existing) existing.remove();
  const div = document.createElement("div");
  div.className = "audit-item audit-item-running";
  div.id = "running-item";
  div.innerHTML = `
    <div class="audit-item-main">
      <div class="audit-item-name" title="${shortUrl}">${shortUrl}</div>
      <div class="audit-item-meta">
        <span class="running-stage">Starting…</span>
        <span class="audit-item-score running-badge">Running</span>
      </div>
    </div>`;
  list.prepend(div);
  const emptyEl = list.querySelector(".audit-list-empty");
  if (emptyEl) emptyEl.remove();
}

function updateRunningStage(stage) {
  const el = document.querySelector("#running-item .running-stage");
  if (el) el.textContent = stage || "Running…";
}

function removeRunningItem() {
  const el = document.getElementById("running-item");
  if (el) el.remove();
  _activeRunId = null;
  _activeRunUrl = null;
}

// ── Sidebar ────────────────────────────────────────────────────────────────────

async function loadSidebar() {
  const res = await fetch("/api/audits");
  const audits = await res.json();
  const list = document.getElementById("audit-list");

  list.innerHTML = (audits.length ? "" : '<div class="audit-list-empty">No audits yet</div>') + audits.map(a => {
    const overall = a.scores_json?.overall ?? "—";
    const cls = scoreClass(overall);
    const date = new Date(a.run_at).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    const shortUrl = a.url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
    const displayName = a.product_name || shortUrl;
    return `
      <div class="audit-item" data-id="${a.id}">
        <div class="audit-item-main" onclick="loadAudit(${a.id})">
          <div class="audit-item-name" title="${displayName}">${displayName}</div>
          <div class="audit-item-meta">
            <span>${date}</span>
            <span class="audit-item-score ${cls}">${typeof overall === "number" ? overall.toFixed(1) : overall}/5</span>
          </div>
        </div>
        <button class="audit-delete-btn" data-delete-id="${a.id}" data-delete-name="${(displayName).replace(/"/g, '&quot;').replace(/'/g, '&#39;')}" title="Delete audit" onclick="confirmDelete(this)">✕</button>
      </div>`;
  }).join("");

  // Re-inject running item on top if an audit is still active
  if (_activeRunId) injectRunningItem(_activeRunId, _activeRunUrl);
}

async function loadAudit(id) {
  document.querySelectorAll(".audit-item").forEach(i =>
    i.classList.toggle("active", i.dataset.id == id)
  );
  const res = await fetch(`/api/audits/${id}`);
  const record = await res.json();

  renderResults({
    db_id: record.id,
    url: record.url,
    product_name: record.target_json?.structured?.product_name || record.target_json?.structured?.h1 || "",
    run_at: record.run_at,
    scores: record.scores_json,
    audit: record.audit_json,
    rewrite: record.rewrite_json,
    atf_screenshot: record.target_json?.atf_screenshot_base64 || null,
    structured: record.target_json?.structured || {},
  });
}

// ── Radar chart ───────────────────────────────────────────────────────────────

let _radarChart = null;

const RADAR_LABELS = [
  "Headline Clarity",
  "Benefit Hierarchy",
  "Product Positioning",
  "Objection Handling",
  "Trust Signals",
  "Claims Compliance",
  "SEO",
  "Visual Gallery",
  "DTC Benchmark",
];

const RADAR_KEYS = [
  "headline_clarity",
  "benefit_hierarchy",
  "product_positioning",
  "objection_handling",
  "trust_signals",
  "claims_compliance",
  "seo",
  "visual_gallery",
  "dtc_benchmark",
];

function renderRadarChart(audit) {
  const dims = audit?.dimension_scores || audit?.dimensions || {};
  const scores = RADAR_KEYS.map(k => dims[k]?.score ?? 0);

  const isDark = getComputedStyle(document.documentElement)
    .getPropertyValue("--main-bg").trim() === "#000000";

  const textColor   = isDark ? "#888888" : "#555555";
  const gridColor   = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)";
  const fillColor   = isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.05)";
  const strokeColor = isDark ? "rgba(255,255,255,0.7)"  : "rgba(0,0,0,0.7)";
  const pointColor  = isDark ? "#ffffff" : "#000000";

  if (_radarChart) {
    _radarChart.destroy();
    _radarChart = null;
  }

  const ctx = document.getElementById("radar-chart").getContext("2d");
  _radarChart = new Chart(ctx, {
    type: "radar",
    data: {
      labels: RADAR_LABELS,
      datasets: [{
        data: scores,
        backgroundColor: fillColor,
        borderColor: strokeColor,
        borderWidth: 1.5,
        pointBackgroundColor: pointColor,
        pointRadius: 3,
        pointHoverRadius: 5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: { legend: { display: false } },
      layout: { padding: { top: 0, bottom: 0, left: 0, right: 0 } },
      scales: {
        r: {
          min: 0,
          max: 5,
          ticks: {
            stepSize: 1,
            color: textColor,
            font: { size: 10 },
            backdropColor: "transparent",
            showLabelBackdrop: false,
          },
          grid: { color: gridColor },
          angleLines: { color: gridColor },
          pointLabels: {
            color: textColor,
            font: { size: 11, weight: "600" },
          },
        },
      },
    },
  });
}

// ── ATF screenshot ────────────────────────────────────────────────────────────

function renderATFScreenshot(b64) {
  const screenshotCol = document.getElementById("section-atf");
  const img = document.getElementById("atf-screenshot-img");
  if (!b64) {
    screenshotCol.style.display = "none";
    return;
  }
  img.src = `data:image/png;base64,${b64}`;
  screenshotCol.style.display = "flex";
}

// ── Render results ─────────────────────────────────────────────────────────────

function renderResults(result) {
  showPanel("results");

  const overall = result.scores?.overall ?? result.audit?.overall_score ?? 0;

  const nameEl = document.getElementById("res-product-name");
  if (nameEl) nameEl.textContent = result.product_name || result.url;
  const linkEl = document.getElementById("res-link");
  if (linkEl) linkEl.href = result.url;
  document.getElementById("res-meta").textContent =
    `Audited ${new Date(result.run_at).toLocaleString("en-GB")}`;

  const scoreEl = document.getElementById("res-score");
  scoreEl.textContent = typeof overall === "number" ? overall.toFixed(1) : overall;
  scoreEl.className = "res-score-num score-overall-" + (overall >= 4 ? "green" : overall >= 3 ? "yellow" : "red");

  renderATFScreenshot(result.atf_screenshot);
  renderRadarChart(result.audit);
  renderScoresGrid(result.audit, result.structured || {});
  renderTopRecs(result.audit);
  renderRewrite(result.rewrite);
}

function scoreClass(s) {
  return s >= 4 ? "score-green" : s >= 3 ? "score-yellow" : "score-red";
}

function checkIcon(val) {
  const v = (val || "").toLowerCase();
  if (v.startsWith("pass")) return '<span class="check-icon pass">✓</span>';
  if (v.startsWith("fail")) return '<span class="check-icon fail">✗</span>';
  return '<span class="check-icon partial">~</span>';
}

function toggleScoreCard(key) {
  const all = document.querySelectorAll(".score-card");
  const target = document.getElementById("scard-" + key);
  const isOpen = target?.classList.contains("open");
  all.forEach(c => c.classList.remove("open"));
  if (!isOpen && target) target.classList.add("open");
}

function renderScoresGrid(audit, structured = {}) {
  const dims = audit?.dimension_scores || audit?.dimensions || {};
  const LABELS = {
    headline_clarity: "Headline Clarity",
    benefit_hierarchy: "Benefit Hierarchy",
    product_positioning: "Product Positioning",
    objection_handling: "Objection Handling",
    trust_signals: "Trust Signals",
    claims_compliance: "Claims Compliance",
    seo: "SEO",
    visual_gallery: "Visual Gallery",
    dtc_benchmark: "DTC Benchmark",
  };

  // Index flat recommendations by dimension for display in each card
  const recsByDim = {};
  (audit?.recommendations || []).forEach(r => {
    const d = r.dimension?.toLowerCase().replace(/ /g, "_") || "";
    if (!recsByDim[d]) recsByDim[d] = [];
    recsByDim[d].push(r);
  });

  const grid = document.getElementById("scores-grid");
  grid.innerHTML = Object.entries(LABELS).map(([key, label]) => {
    const dim = dims[key] || {};
    const score = dim.score ?? 0;
    const cls = scoreClass(score);

    // Element checks (pass/fail/partial per criterion)
    let evidenceHtml = "";
    if (dim.element_checks && Object.keys(dim.element_checks).length) {
      evidenceHtml = `<div class="element-checks">` +
        Object.entries(dim.element_checks).map(([eName, eVal]) => {
          const detail = (eVal || "").replace(/^(pass|fail|partial|n\/a)\s*[—\-–]\s*/i, "");
          const cleanName = eName.replace(/_/g, " ");
          return `<div class="element-check">
            ${checkIcon(eVal)}
            <span class="check-name">${cleanName}</span>
            <span class="check-detail">${detail}</span>
          </div>`;
        }).join("") + `</div>`;
    }

    // Sub-scores (dtc_benchmark) with per-criterion rationale
    let subScoresHtml = "";
    if (dim.sub_scores) {
      const subRationale = dim.sub_rationale || {};
      subScoresHtml = `<div class="element-checks">` +
        Object.entries(dim.sub_scores).map(([sName, sScore]) => {
          const cls = scoreClass(sScore);
          const rationale = subRationale[sName] || "";
          return `<div class="element-check">
            <span class="score-pill ${cls}" style="font-size:10px;padding:1px 6px;min-width:32px;text-align:center">${sScore}/5</span>
            <span class="check-name">${sName.replace(/_/g, " ")}</span>
            ${rationale ? `<span class="check-detail">${rationale}</span>` : ""}
          </div>`;
        }).join("") + `</div>`;
    }

    // Compliance flags from top-level compliance_flags array
    let flagsHtml = "";
    if (key === "claims_compliance") {
      const flags = audit?.compliance_flags || [];
      if (!flags.length) {
        flagsHtml = `<div style="font-size:11px;color:var(--green);background:var(--green-bg);padding:6px 10px;border-radius:6px;margin-bottom:10px">No compliance risk identified</div>`;
      } else {
        flagsHtml = `<div class="card-flags">` +
          flags.map(f => `<div class="card-flag">
            <div class="card-flag-cat">${(f.risk_level || "")}: ${(f.verbatim_quote || "").substring(0, 80)}…</div>
            <div class="card-flag-reason">${f.risk_reason || ""}</div>
          </div>`).join("") + `</div>`;
      }
    }

    // Recommendations for this dimension from flat list
    const dimRecs = recsByDim[key] || [];
    let recsHtml = "";
    if (dimRecs.length) {
      recsHtml = `<div class="card-recs">
        <div class="card-recs-label">Recommendations</div>
        ${dimRecs.map(r => `<div class="card-rec-item">
          <span class="card-rec-dot"></span>
          <span>${r.finding || r.text || ""}</span>
        </div>`).join("")}
      </div>`;
    }

    // Dot row for collapsed state
    let dotsHtml = "";
    if (dim.element_checks) {
      dotsHtml = Object.values(dim.element_checks).map(v => {
        const s = (v || "").toLowerCase();
        const dc = s.startsWith("pass") ? "dot-pass" : s.startsWith("fail") ? "dot-fail" : s.startsWith("n/a") ? "dot-na" : "dot-partial";
        return `<span class="dot ${dc}"></span>`;
      }).join("");
    } else if (dim.sub_scores) {
      dotsHtml = Object.values(dim.sub_scores).map(sv => {
        const dc = sv >= 4 ? "dot-pass" : sv >= 3 ? "dot-partial" : "dot-fail";
        return `<span class="dot ${dc}"></span>`;
      }).join("");
    }

    const scoreRationale = dim.score_rationale || dim.reasoning || "";
    const summary = scoreRationale.split(".")[0] + (scoreRationale.includes(".") ? "." : "");

    return `
      <div class="score-card" id="scard-${key}">
        <div class="score-card-header" onclick="toggleScoreCard('${key}')">
          <div class="score-card-left">
            <span class="score-pill ${cls}">${score}/5</span>
            <span class="score-card-name">${label}</span>
            <span class="score-card-summary">${summary}</span>
          </div>
          <div class="score-card-right">
            <div class="score-card-dots">${dotsHtml}</div>
            <span class="score-card-chevron">▼</span>
          </div>
        </div>
        <div class="score-card-body">
          ${scoreRationale ? `<p class="score-reasoning">${scoreRationale}</p>` : ""}
          ${key === "seo" ? renderSeoMetadata(structured) : ""}
          ${subScoresHtml}
          ${evidenceHtml}
          ${flagsHtml}
          ${recsHtml}
        </div>
      </div>`;
  }).join("");
}


function renderSeoMetadata(structured) {
  const rows = [
    ["Meta Title", structured.meta_title],
    ["Meta Description", structured.meta_description],
    ["H1", structured.h1],
    ["Review Rating", structured.review_rating],
    ["Review Count", structured.review_count],
  ].filter(([, v]) => v);

  if (!rows.length) return "";

  return `<div class="seo-meta-block">
    <div class="seo-meta-label">Current page metadata</div>
    ${rows.map(([k, v]) => `
      <div class="seo-meta-row">
        <span class="seo-meta-key">${k}</span>
        <span class="seo-meta-val">${v}</span>
      </div>`).join("")}
  </div>`;
}

function renderTopRecs(audit) {
  // New schema: flat recommendations array ranked by priority_rank
  const recs = audit?.recommendations || [];

  if (!recs.length) {
    document.getElementById("top-recs").innerHTML =
      `<tr><td colspan="5" style="padding:16px;color:var(--text-muted);text-align:center">No recommendations generated.</td></tr>`;
    document.getElementById("recs-show-all-row").style.display = "none";
    return;
  }

  const sorted = [...recs].sort((a, b) => {
    const impactDiff = (b.impact_score || 0) - (a.impact_score || 0);
    if (impactDiff !== 0) return impactDiff;
    return (a.effort_score || 99) - (b.effort_score || 99);
  });
  const topRecs = sorted.slice(0, 5);
  const restRecs = sorted.slice(5);

  function impactBar(n) {
    const filled = Math.round(n);
    return Array.from({length: 5}, (_, i) =>
      `<span class="score-dot ${i < filled ? "dot-filled" : "dot-empty"}"></span>`
    ).join("");
  }

  function effortLabel(n) {
    if (!n) return "—";
    if (n <= 1) return '<span class="effort-tag effort-low">Low</span>';
    if (n <= 2) return '<span class="effort-tag effort-medium-low">Easy</span>';
    if (n <= 3) return '<span class="effort-tag effort-medium">Medium</span>';
    if (n <= 4) return '<span class="effort-tag effort-high">Hard</span>';
    return '<span class="effort-tag effort-very-high">Major</span>';
  }

  function triageTag(t) {
    if (!t) return "";
    return t === "SAFE TO ACTION"
      ? `<span class="effort-tag effort-low" style="font-size:9px">${t}</span>`
      : `<span class="effort-tag effort-high" style="font-size:9px">SIGN-OFF</span>`;
  }

  const rows = sorted.map((r, i) => {
    const isTop = i < 5;
    const highlight = isTop ? "rec-row-top" : "rec-row-rest";
    const hidden = isTop ? "" : "rec-extra hidden";
    const text = r.finding || r.text || "—";
    const detail = r.after ? `<div style="margin-top:4px;font-size:11px;color:var(--text-muted)">→ ${r.after}</div>` : "";
    return `
      <tr class="rec-row ${highlight} ${hidden}">
        <td class="rec-priority">${isTop ? `<span class="rec-num">${i + 1}</span>` : ""}</td>
        <td class="rec-dim-cell">${(r.dimension || "").replace(/_/g, " ")} ${triageTag(r.triage)}</td>
        <td class="rec-text-cell">${text}${detail}</td>
        <td class="rec-impact-cell">${r.impact_score ? impactBar(r.impact_score) : "—"}</td>
        <td class="rec-effort-cell">${effortLabel(r.effort_score)}</td>
      </tr>`;
  }).join("");

  document.getElementById("top-recs").innerHTML = rows;

  const extraCount = restRecs.length;
  const showAllRow = document.getElementById("recs-show-all-row");
  if (extraCount > 0) {
    showAllRow.style.display = "";
    document.getElementById("recs-show-all-btn").textContent =
      `Show all ${sorted.length} recommendations ▼`;
  } else {
    showAllRow.style.display = "none";
  }
}

function toggleAllRecs() {
  const extras = document.querySelectorAll(".rec-extra");
  const btn = document.getElementById("recs-show-all-btn");
  const isHidden = extras[0]?.classList.contains("hidden");
  extras.forEach(el => el.classList.toggle("hidden", !isHidden));
  btn.textContent = isHidden
    ? "Show fewer ▲"
    : `Show all ${document.querySelectorAll(".rec-row").length} recommendations ▼`;
}

function renderCompliance(audit) {
  const flags = audit?.compliance_flags || [];
  const body = document.getElementById("compliance-body");

  if (!flags.length) {
    body.innerHTML = `<div class="compliance-clean">No compliance risk identified in scraped content.</div>`;
    return;
  }

  body.innerHTML = `<div class="compliance-flags">` +
    flags.map(f => `
      <div class="compliance-flag">
        <div class="flag-category">${(f.risk_level || "")} — ${(f.verbatim_quote || "").substring(0, 120)}${(f.verbatim_quote||"").length > 120 ? "…" : ""}</div>
        <div class="flag-quote">"${f.verbatim_quote || ""}"</div>
        <div class="flag-reason">${f.risk_reason || ""}</div>
        ${f.suggested_compliant_alternative && f.suggested_compliant_alternative !== "null" ? `<div class="flag-reg" style="color:var(--green)">✓ Suggested: ${f.suggested_compliant_alternative}</div>` : ""}
      </div>`
    ).join("") + `</div>`;
}

function renderRewrite(rw) {
  if (!rw) { document.getElementById("rewrite-body").innerHTML = ""; return; }

  const blocks = [
    { label: "Meta Title", value: rw.meta_title },
    { label: "H1", value: rw.h1 },
    { label: "Sub-Headline", value: rw.sub_headline },
    {
      label: "Benefit Highlights",
      value: rw.benefit_highlights?.length
        ? `<ul>${rw.benefit_highlights.map(b => `<li>${b}</li>`).join("")}</ul>`
        : null,
    },
    {
      label: "FAQ — Objection Handling",
      value: rw.faq_objection_handling?.length
        ? rw.faq_objection_handling.map(qa =>
            `<p><strong>Q: ${qa.q}</strong></p><p style="margin-bottom:8px;color:var(--text-muted)">A: ${qa.a}</p>`
          ).join("")
        : null,
    },
    { label: "Trust Signal Block", value: rw.trust_signal_block },
  ].filter(b => b.value);

  let html = blocks.map(b => `
    <div class="rewrite-block">
      <div class="rewrite-label">${b.label}</div>
      <div class="rewrite-value">${b.value}</div>
    </div>`).join("");

  if (rw.rewrite_summary) {
    html += `<div class="rewrite-block rewrite-summary-block">
      <div class="rewrite-label">What Changed & Why</div>
      <div class="rewrite-value" style="font-style:italic;color:var(--text-muted)">${rw.rewrite_summary}</div>
    </div>`;
  }

  if (rw.compliance_review_required?.length) {
    html += `<div class="rewrite-assumptions" style="border-color:var(--red,#ef4444)">
      <div class="assumptions-title" style="color:var(--red,#ef4444)">🔴 Compliance review required (${rw.compliance_review_required.length} sentences)</div>
      <ul class="assumptions-list">${rw.compliance_review_required.map(a => `<li>${a}</li>`).join("")}</ul>
    </div>`;
  }

  if (rw.assumptions_flagged?.length) {
    html += `<div class="rewrite-assumptions">
      <div class="assumptions-title">⚠ Assumptions flagged for human review (${rw.assumptions_flagged.length})</div>
      <ul class="assumptions-list">${rw.assumptions_flagged.map(a => `<li>${a}</li>`).join("")}</ul>
    </div>`;
  }

  document.getElementById("rewrite-body").innerHTML = html;
}


// ── Guide ──────────────────────────────────────────────────────────────────────

const DIMENSIONS = [
  {
    key: "headline_clarity",
    name: "Headline Clarity",
    tagline: "Does the ATF viewport communicate identity, outcome & price in under 5 seconds?",
    description: "The above-the-fold mobile viewport must instantly communicate product identity, functional outcome, and unit price without scrolling. First impressions form in 50ms — cognitive overload from jargon or missing anchors causes scroll abandonment within 4–9 seconds.",
    elements: [
      { name: "Descriptive H1", desc: "H1 must state both the product name AND its fundamental category explicitly (e.g. 'Daily Greens Supergreens Powder'). Abstract brand nomenclature that requires prior knowledge scores 1." },
      { name: "Outcome sub-headline", desc: "The text immediately below the H1 must state who the product is for and what it functionally does — in one concise sentence. Marketing slogans without functional meaning score 1." },
      { name: "Price visibility", desc: "Both total price AND cost-per-serving must be visible ATF. Price-shock abandonment increases sharply when per-serving cost is hidden." },
      { name: "Visual & trust anchors", desc: "A clear unobstructed product packaging image AND a star rating with review count must be visible ATF to establish immediate social proof and visual context." },
      { name: "Prominent CTA", desc: "Primary 'Add to Cart' CTA must contrast sharply with the background and meet the 44px minimum mobile touch target." },
    ],
    scoring: {
      1: "H1 uses abstract brand name only. No sub-headline. Price hidden. No reviews ATF. CTA absent or buried.",
      2: "H1 names product but not category. Sub-headline generic. Total price only (no per-serving). Rating present but no count.",
      3: "H1 has product + category. Outcome sub-headline present. Total price shown. Rating + count present. CTA visible but low contrast.",
      4: "All five elements present. Per-serving price OR CTA contrast is suboptimal.",
      5: "All five fully met: descriptive H1, outcome sub-headline, total + per-serving price, rating + review count, high-contrast 44px+ CTA.",
    },
    sources: ["Baymard #577", "NNG 50ms first impression research", "CXL 4–9 second engagement window", "Google Mobile UX 44px touch target"],
  },
  {
    key: "benefit_hierarchy",
    name: "Benefit Hierarchy",
    tagline: "Is content structured to maximise scannability and lead with outcomes, not features?",
    description: "Consumers scan for 'informational scent' rather than reading linearly. Unformatted ingredient lists create a 'wall of specs' causing severe friction. Content must be structured to guide scanning eyes toward outcomes before substantiating with evidence.",
    elements: [
      { name: "Outcome-first framing", desc: "Headlines and bullets must lead with the biological or lifestyle benefit (e.g. 'Energy that lasts'). Raw biochemical features (e.g. 'Contains 500mg Ashwagandha') must follow as supporting evidence, never lead." },
      { name: "3-part highlight architecture", desc: "Core benefits should use a 3-part visual layout: bespoke icon + short confirmatory headline + single brief paragraph (3–4 sentences max). This structure slows scanning users and guides the eye." },
      { name: "Highlight count discipline", desc: "Core benefit highlights must be strictly limited to 2–6 features. Highlighting 20+ features equally dilutes the value proposition and causes cognitive overload." },
      { name: "Semantic grouping of details", desc: "Deep nutritional data (amino acid profiles, micronutrient tables) must be relegated lower into a single-column specification table under collapsible sub-section headers — not mixed into the persuasive narrative." },
    ],
    scoring: {
      1: "Feature-led copy throughout. No outcome framing. Ingredient wall as primary content. No structure.",
      2: "Some outcome language present but features still lead. 10+ undifferentiated bullets. No grouping.",
      3: "Outcome-first language in headlines. Bullets still mix benefits and specs. No 3-part layout. Nutrition inline.",
      4: "Outcome-first framing consistent. 2–6 highlights present. Partial 3-part layout. Nutritional data mostly separated.",
      5: "All four elements met: outcome-first throughout, 2–6 highlights in 3-part layout, zero spec walls, nutrition in separated single-column table.",
    },
    sources: ["NNG F-pattern and scanning behaviour research", "Baymard #588", "CXL outcome-first copywriting framework"],
  },
  {
    key: "product_positioning",
    name: "Product Positioning",
    tagline: "Does the page answer 'Why this product over an alternative, for me specifically?'",
    description: "Generic claims like 'high quality protein powder' fail during active comparison shopping because they apply equally to every competitor. The copy must act as a sophisticated sales assistant that contextualises the product within the user's daily routine.",
    elements: [
      { name: "Target identity articulation", desc: "The copy must explicitly state who the product is designed for — a defined persona or lifestyle (e.g. 'for active and busy lifestyles', 'for endurance athletes'). Vague audience language scores 1." },
      { name: "Consumption occasion definition", desc: "The page must specify the precise behavioural occasion or temporal moment for consumption (e.g. 'Start your day with Clean Greens', 'Drink between meals'). This shifts the product from generic supplement to lifestyle-integrated utility." },
      { name: "Problem resolution focus", desc: "The copy must address a specific physiological or lifestyle pain point — afternoon energy crashes, gut discomfort, post-workout recovery. The problem must be named explicitly before the solution is offered." },
      { name: "Internal catalogue differentiation", desc: "For brands with multiple variants, a comparison table or matrix explaining the differences between options must be present so users can self-select accurately. Marked N/A if no variants exist." },
      { name: "Proprietary jargon clarification", desc: "Scientific terminology or proprietary marketing words (e.g. 'Superblend', 'Adaptogen Complex') must be immediately followed by a plain-language definition. Undefined jargon cognitively alienates non-expert users." },
    ],
    scoring: {
      1: "Generic claims only. No audience identity, no occasion, no pain point named. Jargon undefined.",
      2: "Broad audience language present. One pain point vaguely implied. No occasion or variant matrix.",
      3: "Specific audience and one pain point named. No consumption occasion. Jargon present but not all defined.",
      4: "Audience, occasion, and problem all explicitly stated. Most jargon defined. Variant matrix present if applicable.",
      5: "All five elements fully met: precise audience, consumption occasion, named problem + solution, variant matrix, all jargon defined.",
    },
    sources: ["CXL positioning framework", "NNG comparison shopping behaviour", "Jobs-to-be-Done framework"],
  },
  {
    key: "objection_handling",
    name: "Objection Handling",
    tagline: "Does the page proactively resolve purchase anxieties before checkout?",
    description: "Global cart abandonment averages 70.19%. Anticipating friction points on the page itself is a primary conversion lever. Six specific hesitation triggers are evaluated.",
    elements: [
      { name: "Price & hidden costs", desc: "Hidden fees and unexpected shipping cause 39% of cart abandonments. Total costs and shipping thresholds must be stated near the Buy Box. A clear toggle between one-time purchase and subscription pricing prevents price confusion." },
      { name: "Taste & texture reassurance", desc: "Ingestible supplements carry negative industry stereotypes. The copy must explicitly counter sensory objections with specific language (e.g. 'Smooth and delicious, even with just water', 'No chalky texture')." },
      { name: "Subscription flexibility", desc: "Fear of being locked into a hard-to-cancel subscription is a significant barrier. 'Cancel anytime' or 'Skip a delivery' must appear adjacent to the Subscribe & Save toggle — not only in T&Cs or footer." },
      { name: "Efficacy & risk reversal", desc: "Efficacy scepticism raises the psychological purchase threshold. An explicit, visually prominent risk reversal — '30 Day Money Back Guarantee' badge — must appear near the Buy Box, not only linked in the footer." },
      { name: "Usage & technical FAQ", desc: "A contextual accordion-style FAQ must be directly in the user's path on the PDP — not a detached footer link. It must address product-specific questions: allergens, caffeine content, age suitability." },
      { name: "Variant selection friction", desc: "Complex variant selection causes 18% of users to abandon. Size and flavour selectors must use visible button-style selectors rather than hidden dropdown menus. [VISUAL INSPECTION REQUIRED]" },
    ],
    scoring: {
      1: "No product-specific objection handling. Generic FAQ or none. No guarantee. No shipping clarity near Buy Box.",
      2: "Shipping cost stated. One product objection addressed. No subscription flexibility. No FAQ in-path.",
      3: "3 of 6 elements present. Guarantee or risk reversal present. Subscription messaging absent or in footer only.",
      4: "4–5 of 6 elements present. In-path FAQ with product-specific content. Guarantee near CTA. Subscription flexibility stated.",
      5: "All 6 elements fully met: shipping clarity + toggle, taste reassurance, subscription flexibility adjacent to toggle, guarantee near CTA, in-path product FAQ, button-style variant selectors.",
    },
    sources: ["Baymard cart abandonment research (70.19% global average)", "Baymard hidden costs #1 abandonment reason (39%)", "Baymard complex checkout causes 18% abandonment"],
  },
  {
    key: "trust_signals",
    name: "Trust Signals",
    tagline: "Does the page effectively combat supplement industry scepticism?",
    description: "Efficacy claims in the supplement industry are routinely viewed with suspicion. Trust signals serve as psychological shortcuts that lower the cognitive barrier to purchase. Five elements are evaluated across subjective social proof and objective authoritative validation.",
    elements: [
      { name: "Prominent aggregate ratings", desc: "A star rating and total review count must be placed immediately below the H1. A review count threshold of 50+ reviews is empirically linked to a 4.6% conversion uplift (Spiegel Research Centre)." },
      { name: "Authenticity & negative review visibility", desc: "Consumers actively seek 1–2 star reviews to assess worst-case scenarios. Interactive review filtering by star rating, 'verified buyer' badges, and negative review visibility must be present. Aggressively filtering negative reviews damages credibility." },
      { name: "Independent certifications", desc: "Third-party certification badges (Informed Sport, B-Corp, Organic, GMP) must be positioned near the ATF section — not footer-only. Informed Sport is particularly critical for athletes and military personnel (prohibited substance assurance)." },
      { name: "Batch testing transparency", desc: "Explicit reassurance about testing for heavy metals (lead, arsenic, mercury), pesticides, and contaminants elevates scientific credibility. Direct links to batch test certificates score highest." },
      { name: "Expert & authority endorsements", desc: "Credentialed expert involvement — clinical nutritionists, registered dietitians, medical doctors — lends scientific weight. Named credentials with title score higher than generic 'nutritionist approved' claims." },
    ],
    scoring: {
      1: "No trust signals present. No rating, no certifications, no expert endorsement, no batch testing.",
      2: "Star rating present below H1. Review count below 50 or absent. No certifications or expert content.",
      3: "Rating + 50+ reviews. One certification visible. No batch testing or expert endorsement.",
      4: "Rating + 50+ reviews + 1+ ATF certification + review filter or verified badges. Batch testing or expert endorsement present.",
      5: "Full trust stack: 50+ reviews with filter/verified badges near H1, 1+ ATF certification (ideally Informed Sport), batch test transparency with certificate link, named expert credentials.",
    },
    sources: ["BrightLocal Consumer Review Survey", "Baymard #593", "Spiegel Research Centre (4.6% uplift at 50 reviews)", "Informed Sport prohibited substance standard"],
  },
  {
    key: "claims_compliance",
    name: "Claims & Compliance",
    tagline: "Are all health and efficacy claims legally compliant with UK food supplement regulations?",
    description: "For UK DTC supplement brands, compliance is the most legally precarious dimension. Failure represents an existential legal threat — ASA enforcement action, MHRA product reclassification, or a ban from sale. The foundational legal principle: supplements are classified as FOOD, not medicine. The agent flags risk based only on language actually present in the scraped content — it never invents claims.",
    elements: [
      { name: "Medicinal claims", desc: "ABSOLUTE ZERO TOLERANCE. Any language stating or implying the product prevents, treats, or cures a human disease or clinical condition. Qualifier words ('may help') do not make a prohibited claim compliant if the underlying meaning is medicinal." },
      { name: "Testimonial loopholes", desc: "A brand cannot use customer reviews to bypass the medicinal claims ban. A displayed review stating 'This cured my anxiety' carries the same legal weight as a brand-authored claim and is equally prohibited." },
      { name: "Nutrient attribution failures", desc: "Health benefits cannot be attributed to the product name or brand. The benefit must be explicitly linked to a specific active nutrient using verbatim GB NHC Register wording (e.g. 'Contains Iron, which contributes to normal cognitive function')." },
      { name: "GHC/SHC adjacency failures", desc: "A General Health Claim (e.g. 'superfood', 'detoxifier', 'promotes a healthy heart') is only lawful if placed immediately adjacent to a relevant authorised Specific Health Claim from the GB NHC Register. Standalone GHCs are prohibited." },
      { name: "Normal phrasing violations", desc: "Registered claims use precise statutory language — particularly the word 'normal'. Replacing 'normal' with marketing language is a direct breach (e.g. 'boosts your metabolism' vs the registered 'contributes to normal energy-yielding metabolism')." },
      { name: "High-risk buzzwords & novel foods", desc: "'Adaptogen', 'Nootropic', 'Antioxidant' (as health claims) require registered substantiation. Ingredients like Turkey Tail mushroom, Lion's Mane, or CBD may be unauthorised novel foods — triggering MHRA reclassification risk." },
    ],
    scoring: {
      1: "One or more medicinal claims or novel food violations present. Immediate enforcement risk.",
      2: "No medicinal claims but multiple GHC/SHC failures, nutrient attribution errors, or high-risk buzzwords without substantiation.",
      3: "Some claims use registered wording; others have phrasing drift or standalone GHCs. No medicinal claims. Minor risk present.",
      4: "All claims use registered wording or are factual. Minor adjacency or attribution issue. No medicinal claims or novel food flags.",
      5: "Full compliance: zero medicinal claims, all benefits attributed to named registered nutrients, GHCs adjacent to registered SHCs, verbatim statutory phrasing, no novel food flags.",
    },
    sources: ["UK Regulation 1924/2006", "ASA CAP Code Section 15", "MHRA Blue Guide", "GB NHC Register", "UK Novel Food Regulation (EU 2015/2283)"],
  },
  {
    key: "seo",
    name: "SEO",
    tagline: "Is the page technically optimised to capture high-intent long-tail organic queries?",
    description: "Evaluated using only publicly visible on-page elements and structured data — no internal keyword data required. Five elements covering metadata, header structure, Product schema, rating schema integrity, and breadcrumb navigation.",
    elements: [
      { name: "Metadata & URL structure", desc: "Meta title must incorporate product name + category + key modifier (e.g. 'AI Greens Supergreens Powder | Protein Works'). URL must be concise, hyphen-separated, no query strings. OG tags must be populated." },
      { name: "Header hierarchy", desc: "H1 must be reserved exclusively for the primary product name — not a slogan. H2 and H3 must be used sequentially without skipping hierarchy levels (H1 → H2 → H3 in strict order)." },
      { name: "Product schema & Merchant listings", desc: "JSON-LD Product schema must include all required fields: name, brand, price, priceCurrency, availability. Merchant listing schema (return policy, delivery specs) is required for Google Shopping tab eligibility." },
      { name: "AggregateRating schema compliance", desc: "Schema ratingValue and reviewCount must exactly match the visible numbers on the page. Incorrect schema can cause severe manual penalties and loss of rich snippets. Store-wide review aggregation in product schema is flagged as a penalty risk." },
      { name: "Breadcrumb navigation", desc: "BreadcrumbList schema must be present AND visible breadcrumb text must exist in the HTML. Schema without visible breadcrumbs scores partial only." },
    ],
    scoring: {
      1: "Primary keyword absent from meta title and H1. No Product schema. No breadcrumbs.",
      2: "Keyword in meta title but not H1. Basic Product schema present but fields incomplete. No AggregateRating or Merchant listing.",
      3: "Primary keyword in H1 and meta title. Product schema with required fields. No Merchant listing. No breadcrumbs or AggregateRating schema.",
      4: "Keyword-optimised meta + H1. Full Product schema + AggregateRating matching visible reviews. Breadcrumbs present. Merchant listing absent.",
      5: "All five elements met: keyword-rich meta + canonical + OG, sequential headers, complete Product schema + Merchant listing, AggregateRating matching page data, BreadcrumbList + visible breadcrumbs.",
    },
    sources: ["Google Search Central Product structured data", "Google Shopping Merchant listing schema", "Google AggregateRating guidelines", "Moz On-Page SEO guide"],
  },
  {
    key: "visual_gallery",
    name: "Visual Gallery",
    tagline: "Do images and gallery slides carry key messages for skim-readers?",
    description: "Evaluated using actual product images passed to the vision model (when available) — not just alt text. The model assesses whether images reinforce benefits, show the product in use, and support skim-reading. When no images are loaded, scores are alt-text based only and flagged accordingly.",
    elements: [
      { name: "Alt text descriptiveness", desc: "Alt text must follow the pattern 'Product – Angle – Key Benefit' rather than generic filenames. Evaluated from image_alts field. Accurate and keyword-rich alt text also serves SEO." },
      { name: "Gallery depth & angles", desc: "Gallery must cover the full purchase information set: pack shot, ingredients/nutrition panel, lifestyle/in-use shot, benefit callout. Inferred from alt text count and keywords; confirmed visually when images are loaded." },
      { name: "Benefit messaging on images", desc: "Gallery slides and hero images must carry benefit copy or callouts visible to skim-readers — not just plain product photography. Evaluated using vision model on actual images." },
      { name: "Lifestyle & in-use imagery", desc: "At least one lifestyle or in-use shot must be present to contextualise the product in the customer's daily routine. Detected from alt text keywords ('lifestyle', 'shaker', 'athlete') and confirmed visually." },
    ],
    scoring: {
      1: "All images have empty or generic alt text. Single pack shot. No benefit messaging on images.",
      2: "Product name in alt text only. 2–3 images. No lifestyle shot. No benefit callouts visible.",
      3: "Alt text includes product + flavour/format. Gallery has 3+ angles. No lifestyle shot.",
      4: "Alt text keyword-rich. Gallery has 3+ angles including lifestyle shot. Partial benefit messaging.",
      5: "Alt text follows 'Product – Angle – Benefit' pattern. 5+ gallery images. Lifestyle shot. Benefit callouts on hero and gallery slides.",
    },
    sources: ["Baymard image gallery study #602", "WebAIM alt text guidance"],
  },
  {
    key: "dtc_benchmark",
    name: "DTC Benchmark",
    tagline: "Does the page meet best-practice DTC e-commerce standards across four absolute criteria?",
    description: "Scored against four absolute best-practice standards — no competitor comparison. Each sub-criterion is evaluated independently from the scraped page data and product images. The dimension score is the mean of the four sub-scores (rounded). These criteria are drawn from mobile UX research and web performance standards, not relative to any other brand.",
    elements: [
      { name: "Above-fold completeness", desc: "The top of the mobile screen must anchor the user's expectations within milliseconds — zero scrolling needed. Checks for: descriptive H1, outcome-driven sub-headline, unit price per serving (e.g. '£1.17/serving'), star rating with review count, high-contrast CTA (44px minimum touch target), and a persistent sticky Add-to-Cart bar. All six must be present for a score of 5." },
      { name: "Scannability", desc: "Page must avoid text-heavy layouts that create a 'wall of specs'. Core features (2–6 only) should use a 3-part layout: bespoke icon or image + short outcome headline (e.g. 'Energy that lasts') + brief paragraph. Deep nutritional data must be in a single-column table lower on the page, not inline prose. Dense paragraph blocks for primary benefits are penalised." },
      { name: "Mobile structure", desc: "All text blocks, spec sheets, and FAQs must be formatted for single-column mobile reading — no horizontal scrolling. Image gallery must use fully visible thumbnails beneath the main image, not dot indicators. Dot indicators cause mobile users to miss supplementary images entirely. Evaluated from actual product images when available; flagged [VISUAL INSPECTION REQUIRED] otherwise." },
      { name: "Page performance", desc: "Evaluated from observable HTML proxy signals for LCP (target < 2.5s) and CLS (target < 0.1). Checks: presence of lazy-loading attributes, image dimensions specified in HTML, CDN URLs in use, absence of render-blocking inline scripts. Always flagged [REQUIRES LIGHTHOUSE TEST] — LCP and CLS cannot be measured from HTML scraping alone." },
    ],
    scoring: {
      1: "3 or more sub-criteria at score 1–2. Page fails basic DTC best-practice standards.",
      2: "2 sub-criteria at score 1–2. Significant gaps in ATF completeness or scannability.",
      3: "All sub-criteria at score 3 or mixed 2/4. Meets basic standards but not best practice.",
      4: "All sub-criteria at score 3–4. One sub-criterion at 2 maximum.",
      5: "All four sub-criteria score 4 or 5. Page meets full DTC best-practice standard.",
    },
    sources: ["Baymard mobile UX research", "Google Core Web Vitals (LCP < 2.5s, CLS < 0.1)", "NNG mobile scrolling behaviour", "Google Mobile UX 44px touch target guideline"],
  },
];

function renderGuide() {
  const container = document.getElementById("guide-dimensions");
  container.innerHTML = DIMENSIONS.map((dim, i) => `
    <div class="guide-dim" id="gdim-${dim.key}">
      <div class="guide-dim-header" onclick="toggleGuide('${dim.key}')">
        <div class="guide-dim-left">
          <div class="guide-dim-num">${i + 1}</div>
          <div>
            <div class="guide-dim-name">${dim.name}</div>
            <div class="guide-dim-tagline">${dim.tagline}</div>
          </div>
        </div>
        <div class="guide-dim-chevron">▼</div>
      </div>
      <div class="guide-dim-body">
        <p class="guide-dim-desc">${dim.description}</p>

        ${dim.elements ? `
          <div class="guide-elements-label">What the agent evaluates</div>
          <div class="guide-elements">
            ${dim.elements.map(el => `
              <div class="guide-element">
                <div class="guide-element-name">${el.name}</div>
                <div class="guide-element-desc">${el.desc}</div>
              </div>`).join("")}
          </div>` : ""}

        <div class="guide-scoring-label">Scoring criteria (1–5)</div>
        <div class="guide-scoring">
          ${Object.entries(dim.scoring).map(([n, text]) => `
            <div class="guide-score-row">
              <div class="guide-score-num sn-${n}">${n}</div>
              <div class="guide-score-text">${text}</div>
            </div>`).join("")}
        </div>

        <div class="guide-sources">Sources: <span>${dim.sources.join(" · ")}</span></div>
      </div>
    </div>`).join("");
}

function toggleGuide(key) {
  const el = document.getElementById("gdim-" + key);
  el.classList.toggle("open");
}

// ── Delete ────────────────────────────────────────────────────────────────────

function confirmDelete(btn) {
  const id = btn.dataset.deleteId;
  const name = btn.dataset.deleteName;
  document.getElementById("delete-modal-body").textContent =
    `"${name}" will be permanently removed from your audit history.`;
  document.getElementById("modal-confirm-btn").onclick = () => executeDelete(id);
  document.getElementById("delete-modal").classList.remove("hidden");
}

function closeDeleteModal() {
  document.getElementById("delete-modal").classList.add("hidden");
}

async function executeDelete(id) {
  closeDeleteModal();
  await fetch(`/api/audits/${id}`, { method: "DELETE" });
  // If the deleted audit is currently shown, go back to new audit screen
  const active = document.querySelector(`.audit-item[data-id="${id}"]`);
  if (active?.classList.contains("active")) showNewAudit();
  loadSidebar();
}

// Close modal on backdrop click
document.addEventListener("click", e => {
  if (e.target.id === "delete-modal") closeDeleteModal();
});

// ── Trace Agent Dashboard ─────────────────────────────────────────────────────

let _dashCharts = {};
let _dashLoaded = false;
let _activeDashTab = "overview";

async function initTraceAgent() {
  try {
    const res = await fetch("/api/langsmith/recent-runs?limit=5");
    const data = await res.json();
    const badge = document.getElementById("trace-agent-run-count");
    if (badge) badge.textContent = (data.runs || []).length + " runs";
  } catch (e) {}
}

function openTraceAgent() {
  showPanel("trace");
  if (!_dashLoaded) {
    document.getElementById("trace-summary-cards").innerHTML =
      `<div class="trace-loading" style="grid-column:1/-1;padding:20px">Loading LangSmith data…</div>`;
    loadDashboard();
    _dashLoaded = true;
  }
}

function switchDashTab(tab) {
  _activeDashTab = tab;
  ["overview","tokens","steps","traces"].forEach(t => {
    document.getElementById(`dash-tab-${t}`).style.display = t === tab ? "block" : "none";
    document.querySelectorAll(".trace-dash-tab").forEach(btn => {
      btn.classList.toggle("active-dash-tab", btn.textContent.toLowerCase().startsWith(tab.split(" ")[0]));
    });
  });
}

async function loadDashboard() {
  const [metricsRes, runsRes, embedRes] = await Promise.all([
    fetch("/api/langsmith/metrics"),
    fetch("/api/langsmith/recent-runs?limit=20"),
    fetch("/api/langsmith/embed-url"),
  ]);

  // Parse all three responses in parallel
  const [embed, metrics, runsData] = await Promise.all([
    embedRes.json(), metricsRes.json(), runsRes.json(),
  ]);
  const { runs = [] } = runsData;

  // Update "Open LangSmith" link with real project URL
  try {
    const link = document.getElementById("open-langsmith-link");
    if (link && embed.embed_url) link.href = embed.embed_url;
  } catch (e) {}

  if (metrics.error) {
    document.getElementById("trace-summary-cards").innerHTML =
      `<div class="trace-loading" style="color:var(--red);padding:20px">LangSmith error: ${metrics.error}</div>`;
    return;
  }

  // Summary cards
  const s = metrics.summary;
  document.getElementById("trace-summary-cards").innerHTML = [
    ["Total Runs", s.total_runs, "#6366f1"],
    ["Total Cost", "$" + s.total_cost, "#f59e0b"],
    ["Total Tokens", s.total_tokens.toLocaleString(), "#22c55e"],
    ["Success Rate", s.success_rate + "%", s.success_rate > 80 ? "#22c55e" : "#f59e0b"],
    ["Avg Cost/Run", "$" + s.avg_cost_per_run, "#94a3b8"],
    ["Avg Tokens/Run", s.avg_tokens_per_run.toLocaleString(), "#94a3b8"],
  ].map(([label, val, color]) => `
    <div class="trace-stat-card">
      <div class="trace-stat-label">${label}</div>
      <div class="trace-stat-val" style="color:${color}">${val}</div>
    </div>`).join("");

  const chartDefaults = {
    plugins: { legend: { labels: { color: "#888", font: { size: 11 } } } },
    scales: {
      x: { ticks: { color: "#666", font: { size: 10 } }, grid: { color: "#222" } },
      y: { ticks: { color: "#666", font: { size: 10 } }, grid: { color: "#222" } },
    },
  };

  function destroyChart(id) { if (_dashCharts[id]) { _dashCharts[id].destroy(); delete _dashCharts[id]; } }

  // Latency chart — one bar per product page run, tooltip shows breakdown
  destroyChart("latency");
  _dashCharts["latency"] = new Chart(document.getElementById("chart-latency"), {
    type: "bar",
    data: {
      labels: metrics.latency_trend.map(d => d.label),
      datasets: [{
        label: "Total Latency",
        data: metrics.latency_trend.map(d => d.overall),
        backgroundColor: "#6366f1",
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      ...chartDefaults,
      plugins: {
        ...chartDefaults.plugins,
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => {
              const d = metrics.latency_trend[ctx.dataIndex];
              const lines = [`  Total: ${ctx.parsed.y}s`];
              if (d.reflexion_loop != null) lines.push(`  Reflexion Loop: ${d.reflexion_loop}s`);
              if (d.rewriter != null)       lines.push(`  Rewriter: ${d.rewriter}s`);
              return lines;
            },
          },
          backgroundColor: "#1a1a2e",
          borderColor: "#2d2d4e",
          borderWidth: 1,
          titleColor: "#e2e8f0",
          bodyColor: "#94a3b8",
          padding: 10,
        },
      },
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: v => v + "s" } },
      },
    },
  });

  // Tokens per run — single bar (total), tooltip shows prompt / completion breakdown
  destroyChart("tokens-run");
  _dashCharts["tokens-run"] = new Chart(document.getElementById("chart-tokens-run"), {
    type: "bar",
    data: {
      labels: metrics.token_trend.map(d => d.label),
      datasets: [{
        label: "Total Tokens",
        data: metrics.token_trend.map(d => (d.prompt || 0) + (d.completion || 0)),
        backgroundColor: "#6366f1",
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      ...chartDefaults,
      plugins: {
        ...chartDefaults.plugins,
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => {
              const d = metrics.token_trend[ctx.dataIndex];
              const total = (d.prompt || 0) + (d.completion || 0);
              return [
                `  Total: ${total.toLocaleString()}`,
                `  Prompt: ${(d.prompt || 0).toLocaleString()}`,
                `  Completion: ${(d.completion || 0).toLocaleString()}`,
              ];
            },
          },
          backgroundColor: "#1a1a2e", borderColor: "#2d2d4e", borderWidth: 1,
          titleColor: "#e2e8f0", bodyColor: "#94a3b8", padding: 10,
        },
      },
    },
  });

  // Cost per run — single bar, tooltip shows cost value
  destroyChart("cost");
  _dashCharts["cost"] = new Chart(document.getElementById("chart-cost"), {
    type: "bar",
    data: {
      labels: metrics.cost_trend.map(d => d.label),
      datasets: [{
        label: "Cost (USD)",
        data: metrics.cost_trend.map(d => d.cost),
        backgroundColor: "#f59e0b",
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      ...chartDefaults,
      plugins: {
        ...chartDefaults.plugins,
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => `  Cost: $${ctx.parsed.y.toFixed(5)}`,
          },
          backgroundColor: "#1a1a2e", borderColor: "#2d2d4e", borderWidth: 1,
          titleColor: "#e2e8f0", bodyColor: "#94a3b8", padding: 10,
        },
      },
      scales: {
        ...chartDefaults.scales,
        y: { ...chartDefaults.scales.y, ticks: { ...chartDefaults.scales.y.ticks, callback: v => `$${v}` } },
      },
    },
  });

  // Token split chart (Tokens & Cost tab) — same single-bar-with-tooltip treatment
  destroyChart("tokens-split");
  _dashCharts["tokens-split"] = new Chart(document.getElementById("chart-tokens-split"), {
    type: "bar",
    data: {
      labels: metrics.token_trend.map(d => d.label),
      datasets: [{
        label: "Total Tokens",
        data: metrics.token_trend.map(d => (d.prompt || 0) + (d.completion || 0)),
        backgroundColor: "#6366f1",
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      ...chartDefaults,
      plugins: {
        ...chartDefaults.plugins,
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => {
              const d = metrics.token_trend[ctx.dataIndex];
              const total = (d.prompt || 0) + (d.completion || 0);
              return [
                `  Total: ${total.toLocaleString()}`,
                `  Prompt: ${(d.prompt || 0).toLocaleString()}`,
                `  Completion: ${(d.completion || 0).toLocaleString()}`,
              ];
            },
          },
          backgroundColor: "#1a1a2e", borderColor: "#2d2d4e", borderWidth: 1,
          titleColor: "#e2e8f0", bodyColor: "#94a3b8", padding: 10,
        },
      },
    },
  });

  // Steps table
  document.getElementById("steps-table").innerHTML = `
    <table class="trace-table">
      <thead><tr>${["Step","Calls","Avg Tokens","Avg Latency","Avg Cost","Error Rate","Total Cost"].map(h => `<th>${h}</th>`).join("")}</tr></thead>
      <tbody>${(metrics.steps || []).map(s => `<tr>
        <td style="color:#6366f1">${s.name}</td>
        <td>${s.calls}</td><td>${s.avg_tokens.toLocaleString()}</td>
        <td>${s.avg_latency_ms}ms</td>
        <td style="color:#f59e0b">$${s.avg_cost}</td>
        <td style="color:${s.error_rate > 10 ? "#ef4444" : "#22c55e"}">${s.error_rate}%</td>
        <td style="color:#f59e0b">$${s.total_cost}</td>
      </tr>`).join("")}</tbody>
    </table>`;

  // Runs list for trace view
  document.getElementById("trace-runs-list").innerHTML = runs.map(r => `
    <div class="trace-run-item" onclick="loadTraceDetail('${r.id}', this)" data-name="${r.name}">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
        <span style="font-size:10px;font-weight:700;color:${r.name==='reflexion_loop'?'#6366f1':'#22c55e'};text-transform:uppercase;letter-spacing:.04em">${r.name}</span>
        <span style="color:${r.error?"#ef4444":"#22c55e"};font-size:10px">${r.error?"✗ error":"✓"}</span>
      </div>
      <div class="trace-run-url" title="${r.url}">${(r.url||"").replace(/^https?:\/\/(www\.)?/,"").slice(0,30) || r.id.slice(0,16)+"…"}</div>
      <div style="display:flex;gap:8px;font-size:10px;margin-top:3px;color:#555">
        <span>${r.latency_s ? r.latency_s+"s" : "—"}</span>
        <span>${r.total_tokens ? r.total_tokens.toLocaleString()+" tok" : "—"}</span>
      </div>
    </div>`).join("");

  // Update badge
  const badge = document.getElementById("trace-agent-run-count");
  if (badge) badge.textContent = runs.length + " runs";
}

async function loadTraceDetail(runId, el) {
  document.querySelectorAll(".trace-run-item").forEach(i => i.classList.remove("active-trace-run"));
  el.classList.add("active-trace-run");
  const panel = document.getElementById("trace-detail-panel");
  panel.innerHTML = `<div class="trace-loading">Loading trace…</div>`;
  const data = await fetch(`/api/langsmith/runs/${runId}/spans`).then(r => r.json());

  if (data.error) {
    panel.innerHTML = `<div class="trace-loading" style="color:#ef4444">Error: ${data.error}</div>`; return;
  }

  const COLORS = { actor:"#6366f1", evaluator:"#f59e0b", reflector:"#ec4899", rewriter:"#22c55e" };
  const getColor = name => COLORS[Object.keys(COLORS).find(k => name.toLowerCase().includes(k))] || "#94a3b8";

  // If no child spans, synthesise a single span from the parent run itself
  const spans = data.spans?.length
    ? data.spans
    : [{ id: runId, name: el.dataset.name || "run", run_type: "chain",
         status: "success", offset_ms: 0, duration_ms: data.total_ms || 0,
         prompt_tokens: 0, completion_tokens: 0,
         total_tokens: data.total_tokens || 0, total_cost: data.total_cost || 0,
         error: null }];

  const totalMs = Math.max(data.total_ms || 1, ...spans.map(s => s.offset_ms + s.duration_ms));

  panel.innerHTML = `
    <div style="padding:16px;">
      <div style="display:flex;gap:20px;margin-bottom:16px;font-size:12px;color:#888;flex-wrap:wrap;">
        <span style="color:#c8c8e0;font-weight:600">${el.dataset.name || ""}</span>
        <span>Total: ${(totalMs/1000).toFixed(1)}s</span>
        <span>Tokens: ${(data.total_tokens||0).toLocaleString()}</span>
        <span>Cost: $${data.total_cost||0}</span>
        <a href="https://smith.langchain.com/public/${runId}/r" target="_blank"
           style="color:#6366f1;margin-left:auto;text-decoration:none;">View in LangSmith ↗</a>
      </div>
      ${spans.map(s => `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
          <div style="width:110px;font-size:11px;color:#aaa;text-align:right;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${s.name}">${s.name}</div>
          <div style="flex:1;height:26px;background:#1a1a2a;border-radius:4px;position:relative;overflow:hidden">
            <div style="position:absolute;
                        left:${(s.offset_ms/totalMs*100).toFixed(1)}%;
                        width:${Math.max(s.duration_ms/totalMs*100,1).toFixed(1)}%;
                        height:100%;background:${getColor(s.name)};border-radius:4px;
                        opacity:${s.error?0.4:0.85};display:flex;align-items:center;
                        padding-left:6px;box-sizing:border-box;">
              <span style="color:#fff;font-size:10px;white-space:nowrap">${(s.duration_ms/1000).toFixed(2)}s</span>
            </div>
          </div>
          <div style="width:60px;font-size:10px;color:#555;flex-shrink:0;text-align:right">${s.total_tokens} tok</div>
        </div>`).join("")}
      <table class="trace-table" style="margin-top:16px">
        <thead><tr><th>Step</th><th>Status</th><th>Duration</th><th>Prompt</th><th>Completion</th><th>Cost</th></tr></thead>
        <tbody>${spans.map(s => `<tr>
          <td style="color:${getColor(s.name)}">${s.name}</td>
          <td style="color:${s.error?"#ef4444":"#22c55e"}">${s.error?"error":s.status||"ok"}</td>
          <td>${(s.duration_ms/1000).toFixed(2)}s</td>
          <td>${(s.prompt_tokens||0).toLocaleString()}</td>
          <td>${(s.completion_tokens||0).toLocaleString()}</td>
          <td style="color:#f59e0b">$${(s.total_cost||0).toFixed(4)}</td>
        </tr>`).join("")}</tbody>
      </table>
    </div>`;
}

async function loadRecentRuns() {
  try {
    const res = await fetch("/api/langsmith/recent-runs?limit=10");
    const data = await res.json();
    const runs = data.runs || [];
    const badge = document.getElementById("trace-agent-run-count");
    if (badge) badge.textContent = runs.length ? `${runs.length} runs` : "0 runs";
    const container = document.getElementById("recent-runs-table");
    if (!container) return;
    if (!runs.length) {
      container.innerHTML = `<div class="trace-loading">No runs yet — start your first audit above</div>`;
      return;
    }
    container.innerHTML = runs.map(run => {
      const statusCls = run.status === "success" ? "trace-status-ok" : run.status === "error" ? "trace-status-err" : "trace-status-pending";
      const date = run.started_at ? new Date(run.started_at).toLocaleString("en-GB", {day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"}) : "—";
      const shortUrl = (run.url || "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "").slice(0, 30);
      const traceUrl = run.public_trace_url || run.langsmith_url;
      return `<div class="trace-run-row">
        <div class="trace-run-url" title="${run.url}">${shortUrl}</div>
        <span class="trace-run-status ${statusCls}">${run.status || "—"}</span>
        <span class="trace-run-meta">${run.latency_s ? run.latency_s + "s" : "—"}</span>
        <span class="trace-run-meta">${run.total_tokens ? run.total_tokens.toLocaleString() + " tok" : "—"}</span>
        <span class="trace-run-meta">${date}</span>
        ${traceUrl ? `<a href="${traceUrl}" target="_blank" class="trace-run-link">trace ↗</a>` : "<span></span>"}
      </div>`;
    }).join("");
  } catch (err) {
    const container = document.getElementById("recent-runs-table");
    if (container) container.innerHTML = `<div class="trace-loading" style="color:var(--red)">Failed to load: ${err.message}</div>`;
  }
}

// ── Business Impact Dashboard ──────────────────────────────────────────────────

let _impactInited = false;
let _selectedProductId = null;
let _kpiCharts = {};

function initBusinessImpact() {
  if (_impactInited) return;
  _impactInited = true;
  _renderProductSelector();
  _selectProduct(SAMPLE_KPI_DATA.products[0].id);
}

function _renderProductSelector() {
  const container = document.getElementById("product-selector");
  if (!container) return;
  container.innerHTML = SAMPLE_KPI_DATA.products.map(p => `
    <button id="product-btn-${p.id}" class="impact-product-btn"
            onclick="_selectProduct('${p.id}')">${p.name}</button>
  `).join("");
}

function _selectProduct(id) {
  _selectedProductId = id;
  SAMPLE_KPI_DATA.products.forEach(p => {
    const btn = document.getElementById(`product-btn-${p.id}`);
    if (btn) btn.classList.toggle("active", p.id === id);
  });
  const product = SAMPLE_KPI_DATA.products.find(p => p.id === id);
  if (!product) return;
  _renderKPICards(product);
  _renderScoreBar(product);
  _renderAllCharts(product);
  _renderWhatChanged(product);
}

function _calcDelta(before, after, key) {
  const avg = arr => arr.reduce((s, m) => s + m[key], 0) / arr.length;
  const bAvg = avg(before), aAvg = avg(after);
  return { bAvg, aAvg, pct: (((aAvg - bAvg) / bAvg) * 100).toFixed(1) };
}

function _renderKPICards(product) {
  const before = product.monthly_data.filter(m => m.period === "before");
  const after  = product.monthly_data.filter(m => m.period === "after");
  const kpis = [
    { key: "conversion_rate",    label: "Conversion Rate",   fmt: v => `${v.toFixed(1)}%`, good: "up",   color: "#22c55e" },
    { key: "add_to_cart_rate",   label: "Add to Cart Rate",  fmt: v => `${v.toFixed(1)}%`, good: "up",   color: "#6366f1" },
    { key: "revenue_per_session",label: "Revenue / Session", fmt: v => `£${v.toFixed(2)}`, good: "up",   color: "#f59e0b" },
    { key: "bounce_rate",        label: "Bounce Rate",       fmt: v => `${v.toFixed(1)}%`, good: "down", color: "#ec4899" },
  ];
  document.getElementById("kpi-summary-cards").innerHTML = kpis.map(k => {
    const { bAvg, aAvg, pct } = _calcDelta(before, after, k.key);
    const improved = k.good === "up" ? aAvg > bAvg : aAvg < bAvg;
    const dc = improved ? "#22c55e" : "#ef4444";
    const arrow = aAvg > bAvg ? "↑" : "↓";
    return `<div class="impact-kpi-card" style="--kpi-color:${k.color}">
      <div class="impact-kpi-label">${k.label}</div>
      <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:4px">
        <span class="impact-kpi-value">${k.fmt(aAvg)}</span>
        <span class="impact-kpi-delta" style="color:${dc}">${arrow} ${Math.abs(pct)}%</span>
      </div>
      <div class="impact-kpi-compare">Before: ${k.fmt(bAvg)} → <span style="color:${k.color}">After: ${k.fmt(aAvg)}</span></div>
    </div>`;
  }).join("");
}

function _renderScoreBar(product) {
  const { overall_score_before: b, overall_score_after: a, audit_date, url } = product;
  document.getElementById("audit-score-bar").innerHTML = `
    <div style="flex-shrink:0">
      <div style="color:#4a4a6a;font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;font-weight:600">PDP Audit Score</div>
      <div style="display:flex;align-items:center;gap:12px">
        <div style="text-align:center"><div style="color:#ef4444;font-size:30px;font-weight:700;line-height:1">${b}</div><div style="color:#4a4a6a;font-size:10px;margin-top:2px">BEFORE</div></div>
        <div style="color:#4a4a6a;font-size:18px">→</div>
        <div style="text-align:center"><div style="color:#22c55e;font-size:30px;font-weight:700;line-height:1">${a}</div><div style="color:#4a4a6a;font-size:10px;margin-top:2px">AFTER</div></div>
        <div style="background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.3);color:#22c55e;padding:5px 14px;border-radius:20px;font-size:13px;font-weight:700">+${a-b} pts</div>
      </div>
    </div>
    <div style="flex:1;min-width:180px">
      <div style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#4a4a6a;font-size:11px">Before</span><span style="color:#ef4444;font-size:11px">${b}/100</span></div>
        <div style="background:#0a0a12;border-radius:4px;height:8px;overflow:hidden"><div style="width:${b}%;height:100%;background:#ef4444;border-radius:4px"></div></div>
      </div>
      <div>
        <div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="color:#4a4a6a;font-size:11px">After</span><span style="color:#22c55e;font-size:11px">${a}/100</span></div>
        <div style="background:#0a0a12;border-radius:4px;height:8px;overflow:hidden"><div style="width:${a}%;height:100%;background:#22c55e;border-radius:4px"></div></div>
      </div>
    </div>
    <div style="flex-shrink:0;text-align:right">
      <div style="color:#4a4a6a;font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:4px">Audit Date</div>
      <div style="color:#e2e8f0;font-size:13px;font-weight:600">${audit_date}</div>
      <div style="color:#4a4a6a;font-size:11px;margin-top:6px">${url}</div>
    </div>`;
}

function _renderAllCharts(product) {
  Object.values(_kpiCharts).forEach(c => c.destroy());
  _kpiCharts = {};

  const months = product.monthly_data.map(m => m.month);
  const before = product.monthly_data.filter(m => m.period === "before");
  const after  = product.monthly_data.filter(m => m.period === "after");

  const DEFAULTS = { borderWidth: 2, pointRadius: 4, pointHoverRadius: 6, tension: 0.3, fill: true };
  const gridColor = "#16162a";
  const baseOpts = {
    responsive: true,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { labels: { color: "#64748b", font: { size: 11 }, boxWidth: 10 } },
      tooltip: {
        backgroundColor: "#1a1a2e", borderColor: "#2d2d4e", borderWidth: 1,
        titleColor: "#e2e8f0", bodyColor: "#94a3b8",
        callbacks: { label: ctx => ctx.raw === null ? null : ` ${ctx.dataset.label}: ${ctx.dataset._fmt(ctx.raw)}` }
      }
    },
    scales: {
      x: { grid: { color: gridColor }, ticks: { color: "#4a4a6a", font: { size: 10 } } },
      y: { grid: { color: gridColor }, ticks: { color: "#4a4a6a", font: { size: 10 } } }
    }
  };

  function makeChart(containerId, label, key, color, fmt) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const { bAvg, aAvg, pct } = _calcDelta(before, after, key);
    const improved = key !== "bounce_rate" ? aAvg > bAvg : aAvg < bAvg;
    const dc = improved ? "#22c55e" : "#ef4444";
    const arrow = aAvg > bAvg ? "↑" : "↓";
    const height = containerId === "chart-units" ? 130 : 170;
    container.innerHTML = `
      <h4>${label}</h4>
      <div class="chart-delta">
        <span style="color:#4a4a6a">Avg before: ${fmt(bAvg)}</span>
        &nbsp;→&nbsp;<span style="color:#e2e8f0">Avg after: ${fmt(aAvg)}</span>
        &nbsp;<span style="color:${dc};font-weight:700;background:${dc}18;padding:1px 8px;border-radius:12px">${arrow} ${Math.abs(pct)}%</span>
      </div>
      <canvas id="canvas-${containerId}" height="${height}"></canvas>`;
    const beforeData = product.monthly_data.map(m => m.period === "before" ? m[key] : null);
    const afterData  = product.monthly_data.map(m => m.period === "after"  ? m[key] : null);
    const chart = new Chart(document.getElementById(`canvas-${containerId}`), {
      type: "line",
      data: {
        labels: months,
        datasets: [
          { label: "Before", data: beforeData, borderColor: color + "70", backgroundColor: color + "0d",
            borderDash: [5, 4], pointBackgroundColor: color + "70", spanGaps: false, ...DEFAULTS, _fmt: fmt },
          { label: "After",  data: afterData,  borderColor: color, backgroundColor: color + "18",
            pointBackgroundColor: color, spanGaps: false, ...DEFAULTS, _fmt: fmt },
        ]
      },
      options: { ...baseOpts, scales: { ...baseOpts.scales, y: { ...baseOpts.scales.y, ticks: { ...baseOpts.scales.y.ticks, callback: v => fmt(v) } } } }
    });
    _kpiCharts[containerId] = chart;
  }

  makeChart("chart-conversion", "Conversion Rate",    "conversion_rate",    "#22c55e", v => `${v?.toFixed(1)}%`);
  makeChart("chart-atc",        "Add to Cart Rate",   "add_to_cart_rate",   "#6366f1", v => `${v?.toFixed(1)}%`);
  makeChart("chart-revenue",    "Revenue Per Session","revenue_per_session","#f59e0b", v => `£${v?.toFixed(2)}`);
  makeChart("chart-bounce",     "Bounce Rate",        "bounce_rate",        "#ec4899", v => `${v?.toFixed(1)}%`);
  makeChart("chart-units",      "Units Sold Per Month","units_sold",        "#06b6d4", v => Math.round(v)?.toLocaleString());
}

function _renderWhatChanged(product) {
  const recEl = document.getElementById("recommendations-implemented");
  if (recEl) recEl.innerHTML = `
    <div style="color:#4a4a6a;font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px;font-weight:700">✅ Recommendations Implemented</div>
    ${product.recommendations_implemented.map(r => `
      <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:9px;color:#c8c8e0;font-size:13px;line-height:1.45">
        <span style="color:#22c55e;flex-shrink:0;margin-top:1px;font-weight:700">✓</span>${r}
      </div>`).join("")}`;

  const compEl = document.getElementById("compliance-resolved");
  if (compEl) compEl.innerHTML = `
    <div style="color:#4a4a6a;font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:14px;font-weight:700">⚠️ Compliance Flags Resolved</div>
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
      <div style="font-size:52px;font-weight:700;color:#22c55e;line-height:1">${product.compliance_flags_resolved}</div>
      <div>
        <div style="color:#e2e8f0;font-size:14px;font-weight:600">health claim sentences</div>
        <div style="color:#4a4a6a;font-size:12px;margin-top:3px">reviewed and resolved before publishing</div>
      </div>
    </div>
    <div style="background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2);border-radius:6px;padding:10px 14px;color:#22c55e;font-size:12px;line-height:1.55;margin-bottom:16px">
      Zero non-compliant claims published. Every health and efficacy statement reviewed against the GB NHC Register before going live.
    </div>
    <div style="color:#4a4a6a;font-size:10px;text-transform:uppercase;letter-spacing:.07em;margin-bottom:8px;font-weight:700">Risk Reduction</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">
      ${["ASA enforcement risk","MHRA borderline","CAP Code 15 breach","Medicinal claims"].map(r =>
        `<span style="background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.25);color:#ef4444;font-size:11px;padding:2px 9px;border-radius:12px;text-decoration:line-through;opacity:.7">${r}</span>`
      ).join("")}
    </div>`;
}

// ── Init ───────────────────────────────────────────────────────────────────────

loadSidebar();
renderGuide();
initTraceAgent();

// Restore in-progress audit after page refresh
(function restoreActiveRun() {
  const saved = localStorage.getItem("pw_active_run");
  if (!saved) return;
  try {
    const { run_id, url } = JSON.parse(saved);
    fetch(`/api/audit/${run_id}/status`).then(res => {
      if (!res.ok) {
        // 404 — server restarted and lost the run
        localStorage.removeItem("pw_active_run");
        return;
      }
      return res.json();
    }).then(data => {
      if (!data) return;
      if (data.status === "complete" || data.status === "error") {
        localStorage.removeItem("pw_active_run");
        return;
      }
      // Run is still in progress — restore the progress UI
      _activeRunId = run_id;
      _activeRunUrl = url;
      document.getElementById("progress-url").textContent = url;
      document.getElementById("progress-title").textContent = "Auditing page…";
      document.getElementById("progress-stage").textContent = data.stage || "Running…";
      showPanel("progress");
      injectRunningItem(run_id, url);
      pollStatus(run_id);
    }).catch(() => localStorage.removeItem("pw_active_run"));
  } catch (e) {
    localStorage.removeItem("pw_active_run");
  }
})();
