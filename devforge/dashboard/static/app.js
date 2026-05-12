// devforge dashboard frontend (DEVF-083).
// Vanilla JS. No build step. Talks to /api/* exclusively. Hash routing.
//
// Routes:
//   #/                              -> run list
//   #/runs/<run_id>                 -> run detail
//   #/runs/<run_id>/candidates/<cid> -> candidate detail

"use strict";

const $root = () => document.getElementById("root");
const $crumbs = () => document.getElementById("breadcrumbs");

const escapeHtml = (s) => {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
};

async function fetchJson(url) {
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resp.ok) {
    let body = "";
    try { body = await resp.text(); } catch (_e) { /* ignore */ }
    const err = new Error(`HTTP ${resp.status} ${resp.statusText} from ${url}`);
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return resp.json();
}

async function fetchText(url) {
  const resp = await fetch(url, { headers: { Accept: "text/plain" } });
  if (!resp.ok) {
    const err = new Error(`HTTP ${resp.status} from ${url}`);
    err.status = resp.status;
    throw err;
  }
  return resp.text();
}

function statusCell(value) {
  const cls = `status-${escapeHtml(value || "unknown")}`;
  return `<span class="${cls}">${escapeHtml(value || "—")}</span>`;
}

function setBreadcrumbs(parts) {
  // parts: [{label, href?}]
  $crumbs().innerHTML = parts
    .map((p, i) => {
      const sep = i === 0 ? "" : " / ";
      if (p.href) {
        return `${sep}<a href="${escapeHtml(p.href)}">${escapeHtml(p.label)}</a>`;
      }
      return `${sep}<span>${escapeHtml(p.label)}</span>`;
    })
    .join("");
}

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

async function renderRunsList() {
  setBreadcrumbs([{ label: "runs" }]);
  $root().innerHTML = `<p class="loading">Loading runs…</p>`;
  let payload;
  try {
    payload = await fetchJson("/api/runs?limit=200");
  } catch (e) {
    $root().innerHTML = `<p class="error">Failed to load runs: ${escapeHtml(e.message)}</p>`;
    return;
  }
  const items = payload.items || [];
  if (items.length === 0) {
    $root().innerHTML = `<p class="muted">No runs recorded yet. Run a workflow first.</p>`;
    return;
  }
  const rows = items
    .map(
      (r) => `
      <tr>
        <td><a href="#/runs/${escapeHtml(r.run_id)}">${escapeHtml(r.run_id)}</a></td>
        <td>${escapeHtml(r.workflow)}</td>
        <td>${statusCell(r.status)}</td>
        <td>${escapeHtml(r.started_at || "—")}</td>
        <td>${escapeHtml(r.completed_at || "—")}</td>
        <td>${escapeHtml(r.chosen_candidate || "—")}</td>
      </tr>`,
    )
    .join("");
  $root().innerHTML = `
    <section>
      <h2>Runs (${items.length})</h2>
      <table>
        <thead><tr>
          <th>Run id</th><th>Workflow</th><th>Status</th>
          <th>Started</th><th>Completed</th><th>Chosen candidate</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>
  `;
}

async function renderRunDetail(runId) {
  setBreadcrumbs([
    { label: "runs", href: "#/" },
    { label: runId },
  ]);
  $root().innerHTML = `<p class="loading">Loading run ${escapeHtml(runId)}…</p>`;
  let payload;
  try {
    payload = await fetchJson(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (e) {
    $root().innerHTML = `<p class="error">Failed to load run: ${escapeHtml(e.message)}</p>`;
    return;
  }
  const { run, steps = [], candidates = [], evaluations = [], provider_status = [] } = payload;

  const overview = `
    <section>
      <h2>Overview</h2>
      <dl class="kv">
        <dt>Workflow</dt><dd>${escapeHtml(run.workflow)}</dd>
        <dt>Status</dt><dd>${statusCell(run.status)}</dd>
        <dt>Started</dt><dd>${escapeHtml(run.started_at || "—")}</dd>
        <dt>Completed</dt><dd>${escapeHtml(run.completed_at || "—")}</dd>
        <dt>Input</dt><dd>${escapeHtml(run.input_ref || "—")}</dd>
        <dt>Chosen candidate</dt><dd>${escapeHtml(run.chosen_candidate || "—")}</dd>
        <dt>Final decision</dt><dd>${escapeHtml(run.final_decision_ref || "—")}</dd>
        <dt>Run root</dt><dd><code>${escapeHtml(run.root_path || "—")}</code></dd>
      </dl>
    </section>`;

  const stepRows = steps.length
    ? steps
        .map(
          (s) => `
          <tr>
            <td>${escapeHtml(s.stage_id)}</td>
            <td>${statusCell(s.status)}</td>
            <td>${escapeHtml(s.started_at || "—")}</td>
            <td>${escapeHtml(s.completed_at || "—")}</td>
            <td>${escapeHtml(s.artifact_ref || "—")}</td>
            <td>${escapeHtml(s.note || "")}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="6" class="muted">no steps recorded</td></tr>`;

  const candidateRows = candidates.length
    ? candidates
        .map(
          (c) => `
          <tr>
            <td><a href="#/runs/${escapeHtml(runId)}/candidates/${escapeHtml(c.candidate_id)}">${escapeHtml(c.candidate_id)}</a></td>
            <td>${escapeHtml(c.provider_id || "—")}</td>
            <td class="num">${c.score === null || c.score === undefined ? "—" : Number(c.score).toFixed(1)}</td>
            <td>${escapeHtml(c.decision || "—")}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="4" class="muted">no candidates</td></tr>`;

  const evalRows = evaluations.length
    ? evaluations
        .map(
          (e) => `
          <tr>
            <td>${escapeHtml(e.candidate_id)}</td>
            <td>${escapeHtml(e.kind)}</td>
            <td>${e.passed === null || e.passed === undefined ? "—" : (e.passed ? "yes" : "no")}</td>
            <td class="num">${e.score === null || e.score === undefined ? "—" : Number(e.score).toFixed(1)}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="4" class="muted">no evaluations</td></tr>`;

  const providerRows = provider_status.length
    ? provider_status
        .map(
          (p) => `
          <tr>
            <td>${escapeHtml(p.provider_id)}</td>
            <td>${statusCell(p.status)}</td>
            <td>${p.healthy ? "yes" : "no"}</td>
            <td>${escapeHtml(p.detail || "")}</td>
          </tr>`,
        )
        .join("")
    : `<tr><td colspan="4" class="muted">no provider status snapshot</td></tr>`;

  $root().innerHTML = `
    ${overview}
    <section>
      <h2>Steps</h2>
      <table>
        <thead><tr><th>Stage</th><th>Status</th><th>Started</th><th>Completed</th><th>Artifact</th><th>Note</th></tr></thead>
        <tbody>${stepRows}</tbody>
      </table>
    </section>
    <section>
      <h2>Candidates</h2>
      <table>
        <thead><tr><th>Candidate</th><th>Provider</th><th>Score</th><th>Decision</th></tr></thead>
        <tbody>${candidateRows}</tbody>
      </table>
    </section>
    <section>
      <h2>Evaluations</h2>
      <table>
        <thead><tr><th>Candidate</th><th>Kind</th><th>Passed</th><th>Score</th></tr></thead>
        <tbody>${evalRows}</tbody>
      </table>
    </section>
    <section>
      <h2>Provider status</h2>
      <table>
        <thead><tr><th>Provider</th><th>Status</th><th>Healthy</th><th>Detail</th></tr></thead>
        <tbody>${providerRows}</tbody>
      </table>
    </section>
  `;
}

async function renderCandidateDetail(runId, candidateId) {
  setBreadcrumbs([
    { label: "runs", href: "#/" },
    { label: runId, href: `#/runs/${runId}` },
    { label: candidateId },
  ]);
  $root().innerHTML = `<p class="loading">Loading candidate ${escapeHtml(candidateId)}…</p>`;
  let detail;
  try {
    detail = await fetchJson(
      `/api/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}`,
    );
  } catch (e) {
    $root().innerHTML = `<p class="error">Failed to load candidate: ${escapeHtml(e.message)}</p>`;
    return;
  }

  let diff = "(no diff)";
  try {
    diff = await fetchText(
      `/api/runs/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/diff`,
    );
  } catch (e) {
    if (e.status !== 404) {
      diff = `Failed to load diff: ${e.message}`;
    } else {
      diff = "(no diff captured)";
    }
  }

  const block = (title, payload) => {
    if (!payload) {
      return `<section><h2>${escapeHtml(title)}</h2><p class="muted">(not produced)</p></section>`;
    }
    return `<section><h2>${escapeHtml(title)}</h2><pre>${escapeHtml(
      JSON.stringify(payload, null, 2),
    )}</pre></section>`;
  };

  $root().innerHTML = `
    ${block("Agent result", detail.agent_result)}
    ${block("Decision", detail.decision)}
    ${block("Score", detail.score)}
    ${block("Review", detail.review)}
    ${block("Policy", detail.policy)}
    ${block("Validation", detail.validation)}
    <section>
      <h2>Diff</h2>
      <pre>${escapeHtml(diff)}</pre>
    </section>
  `;
}

// ---------------------------------------------------------------------------
// Hash routing
// ---------------------------------------------------------------------------

function route() {
  const hash = window.location.hash || "#/";
  // Strip leading '#'.
  const path = hash.slice(1) || "/";
  const parts = path.split("/").filter(Boolean);

  // /runs/<id>/candidates/<cid>
  if (parts[0] === "runs" && parts[1] && parts[2] === "candidates" && parts[3]) {
    return renderCandidateDetail(parts[1], parts[3]);
  }
  // /runs/<id>
  if (parts[0] === "runs" && parts[1]) {
    return renderRunDetail(parts[1]);
  }
  // default
  return renderRunsList();
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
