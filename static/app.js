// pub-trivia-night — vanilla JS front-end
// Talks to /api/* for analyze, sessions list/get, export, health.

const VERDICT_BANDS = {
  ring_the_bell:    { label: "ring the bell",    cls: "v-ship",   tone: "green"  },
  mostly_ready:     { label: "mostly ready",     cls: "v-mostly", tone: "blue"   },
  needs_polish:     { label: "needs polish",     cls: "v-polish", tone: "yellow" },
  tight_but_usable: { label: "tight but usable", cls: "v-tight",  tone: "orange" },
  needs_rework:     { label: "needs rework",     cls: "v-rework", tone: "red"    },
};

const DIFFICULTY_COLOR = {
  easy:   "green",
  medium: "yellow",
  hard:   "red",
};

const SEVERITY_COLOR = {
  high:   "red",
  medium: "yellow",
  low:    "blue",
};

// ---- helpers ---------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtDate(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  const d0 = new Date(d); d0.setHours(0, 0, 0, 0);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (d0.getTime() === today.getTime()) return `Today ${hh}:${mm}`;
  if (d0.getTime() === yest.getTime()) return `Yday  ${hh}:${mm}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${hh}:${mm}`;
}

async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error(`${url} → HTTP ${res.status}`);
  return res.json();
}

// ---- form ------------------------------------------------------------

function readForm() {
  return {
    brief: $("#brief").value,
    theme: $("#theme").value,
    n_rounds: parseInt($("#n_rounds").value, 10) || 8,
    qpr: parseInt($("#qpr").value, 10) || 8,
    duration_min: parseInt($("#duration_min").value, 10) || 90,
    n_teams: parseInt($("#n_teams").value, 10) || 6,
    vibe: $("#vibe").value,
  };
}

function writeForm(v) {
  $("#brief").value = v.brief || "";
  $("#theme").value = v.theme || "general";
  $("#vibe").value = v.vibe || "mixed";
  $("#n_rounds").value = v.n_rounds || 8;
  $("#qpr").value = v.qpr || 8;
  $("#duration_min").value = v.duration_min || 90;
  $("#n_teams").value = v.n_teams || 6;
}

// ---- render ----------------------------------------------------------

function renderResult(p) {
  const r = $("#result");
  $("#empty-state").classList.add("hidden");
  r.classList.remove("hidden");

  const sc = p.score;
  const verdict = VERDICT_BANDS[sc.verdict] || VERDICT_BANDS.mostly_ready;

  // score header
  const html = [];
  html.push(`
    <div class="score-header">
      <div class="score-big ${verdict.cls}">
        ${sc.overall}
        <span class="verdict-pill">${verdict.label}</span>
      </div>
      <div class="score-summary">
        <h3>${escapeHtml(p.theme_label)}</h3>
        <p class="host-note">${escapeHtml(p.theme_description)}</p>
        <p class="host-note">${escapeHtml(p.host_guide_note)}</p>
        <p class="host-note">
          <strong>${p.n_rounds} rounds × ${p.qpr} Qs</strong> ·
          ~${p.duration_min} min · ${p.n_teams} teams · vibe: ${escapeHtml(p.vibe)}
        </p>
        <div class="axes">
          ${Object.entries(sc.axes).map(([k, v]) =>
            `<span class="axis-pill ${axisColor(v)}">
               <span class="a-name">${k}</span><span class="a-val">${v}</span>
             </span>`).join("")}
        </div>
      </div>
    </div>
  `);

  // risk flags
  if (p.risk_flags && p.risk_flags.length) {
    html.push(`<div class="risk-block">
      <h3>Risk flags (${p.risk_flags.length})</h3>
      ${p.risk_flags.map(f => `
        <div class="risk-row">
          <div class="sev-cell"><span class="sev sev-${f.severity}">${f.severity}</span></div>
          <div>
            <div class="risk-id">${escapeHtml(f.id)}</div>
            <div class="evidence">${escapeHtml(f.evidence)}</div>
            <div class="fix">${escapeHtml(f.fix)}</div>
          </div>
        </div>`).join("")}
    </div>`);
  } else {
    html.push(`<div class="risk-block">
      <h3>Risk flags</h3>
      <p class="muted small">No risk flags triggered — a clean quiz night.</p>
    </div>`);
  }

  // host script
  html.push(`<div class="host-script">
    <h3>Host script</h3>
    ${p.host_script.map(hs => `
      <div class="hs-row">
        <div class="hs-num">R${hs.round_no}</div>
        <div>
          <div class="hs-name">${escapeHtml(hs.round_name)}</div>
          <div class="hs-cue">${escapeHtml(hs.host_cue)}</div>
          <div class="hs-score">${escapeHtml(hs.score_after_round)}</div>
        </div>
        <div class="hs-min">${hs.estimated_min} min</div>
      </div>`).join("")}
  </div>`);

  // rounds / questions
  html.push(`<div>
    <h3 style="margin-bottom:10px">Questions (${p.n_rounds} rounds)</h3>
    ${p.rounds.map((rm, idx) => `
      <div class="round${idx === 0 ? ' open' : ''}">
        <div class="round-header" data-round-toggle="${idx}">
          <span class="round-name">R${idx + 1}: ${escapeHtml(rm.round)}</span>
          <span class="round-meta">${rm.questions.length} Qs · ${escapeHtml(p.host_script[idx].estimated_min + ' min')}<span class="toggle" style="margin-left:8px">▸</span></span>
        </div>
        <div class="round-body">
          ${rm.questions.map((q, qi) => `
            <div class="q-row">
              <div class="q-text">${qi + 1}. ${escapeHtml(q.q)}</div>
              <div class="choices">
                ${q.choices.map((c, ci) => `
                  <div class="choice ${ci === q.answer ? 'correct' : ''}">
                    ${"ABCD"[ci]}. ${escapeHtml(c)}
                  </div>`).join("")}
              </div>
              <div class="meta">
                <span class="diff-pill ${DIFFICULTY_COLOR[q.difficulty] || 'blue'}">${q.difficulty}</span>
                <span>·</span>
                <span>tag: ${escapeHtml(q.tag)}</span>
              </div>
              ${q.hint ? `<div class="hint">${escapeHtml(q.hint)}</div>` : ''}
            </div>`).join("")}
        </div>
      </div>`).join("")}
  </div>`);

  // tiebreaker
  html.push(`<div class="util-card">
    <h3>Tiebreaker math (if needed)</h3>
    <p>${escapeHtml(p.tiebreaker.problem)}</p>
    <p class="ans">answer = <strong>${p.tiebreaker.answer}</strong></p>
    ${p.handicap_notes && p.handicap_notes.length ? `
      <h3 style="margin-top:12px">Team balance tips</h3>
      <ul>${p.handicap_notes.map(h => `<li>${escapeHtml(h)}</li>`).join("")}</ul>` : ''}
  </div>`);

  // exports + self-rate
  html.push(`<div class="util-card">
    <h3>Save & export</h3>
    <div class="export-row" style="margin-bottom:10px">
      <a href="/api/export/${p.session_id}?fmt=md" target="_blank" class="ghost">⬇ Markdown</a>
      <a href="/api/export/${p.session_id}?fmt=json" target="_blank" class="ghost">⬇ JSON</a>
    </div>
    <div class="rating-block">
      <span class="muted small">Self-rate this night:</span>
      ${[1, 2, 3, 4, 5].map(i =>
        `<button class="star ${(p.self_rating || 0) >= i ? 'on' : ''}"
                 data-star="${i}" data-session="${p.session_id}">★</button>`).join("")}
      <input class="save-note" type="text"
             placeholder="Notes (how did the night go?)"
             value="${escapeHtml(p.notes || '')}"
             data-session="${p.session_id}" />
    </div>
  </div>`);

  r.innerHTML = html.join("\n");

  // wire up round toggles
  $$(".round-header").forEach(h => {
    h.addEventListener("click", () => {
      h.parentElement.classList.toggle("open");
    });
  });

  // wire up rating + notes
  $$(".star").forEach(b => {
    b.addEventListener("click", async () => {
      const rating = parseInt(b.dataset.star, 10);
      const sid = b.dataset.session;
      await fetchJSON(`/api/sessions/${sid}`, {
        method: "PATCH",
        body: JSON.stringify({ self_rating: rating }),
      });
      p.self_rating = rating;
      renderResult(p);
      loadHistory();
    });
  });
  const noteEl = r.querySelector(".save-note");
  if (noteEl) {
    let debounce;
    noteEl.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const sid = noteEl.dataset.session;
        await fetchJSON(`/api/sessions/${sid}`, {
          method: "PATCH",
          body: JSON.stringify({ notes: noteEl.value }),
        });
        loadHistory();
      }, 600);
    });
  }
}

function axisColor(v) {
  if (v >= 85) return "green";
  if (v >= 70) return "blue";
  if (v >= 55) return "yellow";
  if (v >= 40) return "orange";
  return "red";
}

// ---- history ---------------------------------------------------------

async function loadHistory() {
  const data = await fetchJSON("/api/sessions");
  const wrap = $("#history-rows");
  if (!data.sessions.length) {
    wrap.innerHTML = `<p class="muted small">No previous nights yet.</p>`;
    return;
  }
  wrap.innerHTML = data.sessions.map(s => {
    const stars = s.self_rating ? "★".repeat(s.self_rating) + "☆".repeat(5 - s.self_rating) : "☆☆☆☆☆";
    return `<div class="history-row" data-id="${s.id}">
      <div>
        <div><strong>${escapeHtml(s.theme)}</strong> · ${s.n_rounds}×${s.qpr} · ${s.n_teams} teams</div>
        <div class="meta">${fmtDate(s.created_at)} · vibe: ${escapeHtml(s.vibe)}</div>
      </div>
      <div class="stars">${stars}</div>
    </div>`;
  }).join("");

  $$(".history-row").forEach(r => {
    r.addEventListener("click", async () => {
      const sess = await fetchJSON(`/api/sessions/${r.dataset.id}`);
      writeForm(sess);
      renderResult(sess);
    });
  });
}

// ---- bootstrap -------------------------------------------------------

document.addEventListener("DOMContentLoaded", async () => {
  $("#brief-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#btn-forge");
    btn.disabled = true;
    btn.textContent = "Forging…";
    try {
      const payload = readForm();
      const res = await fetchJSON("/api/analyze", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      renderResult(res);
      loadHistory();
    } catch (err) {
      alert("Forge failed: " + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Forge quiz night →";
    }
  });

  $("#btn-sample").addEventListener("click", async () => {
    const s = await fetchJSON("/api/sample");
    writeForm(s);
  });

  $("#btn-clear").addEventListener("click", () => {
    writeForm({});
    $("#empty-state").classList.remove("hidden");
    $("#result").classList.add("hidden");
  });

  loadHistory();
});
