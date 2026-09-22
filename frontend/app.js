const state = {
  reference: "",
  distorted: "",
  activeBrowseField: null,
  browsePath: "",
  jobId: null,
  pollTimer: null,
};

const $ = (sel) => document.querySelector(sel);

const pathInputs = document.querySelectorAll(".path-input");
const runBtn = $("#run-btn");
const cancelBtn = $("#cancel-btn");
const progressSection = $("#progress-section");
const progressFill = $("#progress-bar-fill");
const progressLabel = $("#progress-label");
const errorSection = $("#error-section");
const resultsSection = $("#results-section");
const scoreCards = $("#score-cards");
const rawMetrics = $("#raw-metrics");
const modelSelect = $("#model-select");
const subsampleSelect = $("#subsample-select");

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let val = bytes;
  let i = 0;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(val < 10 && i > 0 ? 2 : 0)} ${units[i]}`;
}

function fmtNum(n, digits = 3) {
  if (n === null || n === undefined) return "-";
  return Number(n).toFixed(digits);
}

// ---------- Model list ----------
async function loadModels() {
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    modelSelect.innerHTML = "";
    for (const m of data.models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      modelSelect.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to load models", e);
  }
}

// ---------- Path inputs / probing ----------
function updateRunButtonState() {
  runBtn.disabled = !(state.reference && state.distorted);
}

async function setField(field, path) {
  state[field] = path;
  const input = document.querySelector(`.path-input[data-field="${field}"]`);
  if (input) input.value = path;
  updateRunButtonState();
  await probeAndRender(field, path);
}

async function probeAndRender(field, path) {
  const statusEl = document.querySelector(`[data-status="${field}"]`);
  const metaEl = document.getElementById(`metadata-${field}`);
  if (!path) {
    statusEl.textContent = "";
    statusEl.className = "file-status";
    metaEl.innerHTML = `<p class="placeholder">Pick a ${field} file to see its metadata.</p>`;
    return;
  }
  statusEl.textContent = "Probing…";
  statusEl.className = "file-status";
  try {
    const res = await fetch(`/api/probe?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Probe failed" }));
      throw new Error(err.detail || "Probe failed");
    }
    const data = await res.json();
    statusEl.textContent = "OK";
    statusEl.className = "file-status ok";
    renderMetadata(metaEl, data);
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "file-status err";
    metaEl.innerHTML = `<p class="placeholder">Could not read metadata: ${escapeHtml(e.message)}</p>`;
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

const FORMAT_FIELDS = [
  ["filename", "File"],
  ["format_long_name", "Format"],
  ["duration", "Duration (s)"],
  ["size", "Size"],
  ["bit_rate", "Bit rate (bps)"],
  ["nb_streams", "Streams"],
];

const VIDEO_FIELDS = [
  ["codec_long_name", "Codec"],
  ["profile", "Profile"],
  ["width", "Width"],
  ["height", "Height"],
  ["pix_fmt", "Pixel format"],
  ["r_frame_rate", "Frame rate"],
  ["avg_frame_rate", "Avg frame rate"],
  ["bit_rate", "Bit rate (bps)"],
  ["nb_frames", "Frame count"],
  ["color_space", "Color space"],
  ["color_range", "Color range"],
  ["color_transfer", "Color transfer"],
  ["color_primaries", "Color primaries"],
];

const AUDIO_FIELDS = [
  ["codec_long_name", "Codec"],
  ["sample_rate", "Sample rate"],
  ["channels", "Channels"],
  ["channel_layout", "Channel layout"],
  ["bit_rate", "Bit rate (bps)"],
];

function buildTable(obj, fields) {
  let rows = "";
  for (const [key, label] of fields) {
    if (obj[key] === undefined || obj[key] === null || obj[key] === "") continue;
    let value = obj[key];
    if (key === "size") value = `${formatBytes(Number(value))} (${value} bytes)`;
    if (key === "duration") value = Number(value).toFixed(3);
    rows += `<tr><td>${label}</td><td>${escapeHtml(String(value))}</td></tr>`;
  }
  return `<table class="meta-table">${rows}</table>`;
}

function renderMetadata(container, data) {
  const fmt = data.format || {};
  let html = `<div class="meta-block"><div class="meta-block-title">Format</div>${buildTable(fmt, FORMAT_FIELDS)}</div>`;

  const streams = data.streams || [];
  streams.forEach((s, idx) => {
    const type = s.codec_type || "unknown";
    let fields = [];
    if (type === "video") fields = VIDEO_FIELDS;
    else if (type === "audio") fields = AUDIO_FIELDS;
    else fields = [["codec_long_name", "Codec"], ["codec_type", "Type"]];

    html += `<div class="meta-block"><div class="meta-block-title">Stream #${s.index ?? idx} — ${type}</div>${buildTable(s, fields)}</div>`;
  });

  if (data.chapters && data.chapters.length) {
    html += `<div class="meta-block"><div class="meta-block-title">Chapters</div>${data.chapters.length} chapter(s)</div>`;
  }

  container.innerHTML = html;
}

pathInputs.forEach((input) => {
  input.addEventListener("change", () => setField(input.dataset.field, input.value.trim()));
});

// ---------- Browse modal ----------
const modal = $("#browse-modal");
const browseEntries = $("#browse-entries");
const browseCurrentPath = $("#browse-current-path");
const browseModalTitle = $("#browse-modal-title");
const browseUpBtn = $("#browse-up-btn");

document.querySelectorAll(".browse-btn").forEach((btn) => {
  btn.addEventListener("click", () => openBrowse(btn.dataset.field));
});
$("#browse-modal-close").addEventListener("click", closeBrowse);
modal.addEventListener("click", (e) => {
  if (e.target === modal) closeBrowse();
});

function openBrowse(field) {
  state.activeBrowseField = field;
  browseModalTitle.textContent = `Select ${field} file`;
  modal.classList.remove("hidden");
  const startPath = state[field] ? state[field].substring(0, state[field].lastIndexOf("\\")) : "";
  loadBrowse(startPath);
}

function closeBrowse() {
  modal.classList.add("hidden");
}

async function loadBrowse(path) {
  browseEntries.innerHTML = `<div class="browse-entry"><span class="name">Loading…</span></div>`;
  try {
    const res = await fetch(`/api/browse?path=${encodeURIComponent(path || "")}`);
    if (!res.ok) throw new Error("Could not list directory");
    const data = await res.json();
    state.browsePath = data.path;
    browseCurrentPath.textContent = data.path || "Drives";
    browseUpBtn.disabled = !data.parent && !data.path;

    let html = "";
    if (data.parent !== null && data.parent !== undefined) {
      html += entryRow({ name: "..", path: data.parent, type: "dir" });
    } else if (data.path) {
      html += entryRow({ name: "..", path: "", type: "dir" });
    }
    for (const e of data.entries) {
      html += entryRow(e);
    }
    browseEntries.innerHTML = html || `<div class="browse-entry"><span class="name">(empty)</span></div>`;

    browseEntries.querySelectorAll(".browse-entry[data-type='dir']").forEach((el) => {
      el.addEventListener("click", () => loadBrowse(el.dataset.path));
    });
    browseEntries.querySelectorAll(".browse-entry[data-type='file']").forEach((el) => {
      el.addEventListener("click", () => {
        setField(state.activeBrowseField, el.dataset.path);
        closeBrowse();
      });
    });
  } catch (e) {
    browseEntries.innerHTML = `<div class="browse-entry"><span class="name">Error: ${escapeHtml(e.message)}</span></div>`;
  }
}

function entryRow(entry) {
  const icon = entry.type === "dir" ? "&#128193;" : "&#127909;";
  const size = entry.type === "file" ? `<span class="size">${formatBytes(entry.size)}</span>` : "";
  return `<div class="browse-entry" data-type="${entry.type}" data-path="${escapeHtml(entry.path)}">
    <span class="icon">${icon}</span>
    <span class="name">${escapeHtml(entry.name)}</span>
    ${size}
  </div>`;
}

browseUpBtn.addEventListener("click", () => {
  if (!state.browsePath) return;
  const parent = state.browsePath.substring(0, state.browsePath.replace(/\\$/, "").lastIndexOf("\\") + 1);
  loadBrowse(parent);
});

// ---------- Analysis run ----------
runBtn.addEventListener("click", startAnalysis);
cancelBtn.addEventListener("click", cancelAnalysis);

async function startAnalysis() {
  errorSection.classList.add("hidden");
  resultsSection.classList.add("hidden");
  progressSection.classList.remove("hidden");
  progressFill.style.width = "0%";
  progressLabel.textContent = "Starting ffmpeg…";
  runBtn.disabled = true;
  cancelBtn.classList.remove("hidden");

  const body = {
    reference: state.reference,
    distorted: state.distorted,
    model: modelSelect.value,
    n_subsample: Number(subsampleSelect.value),
  };

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to start analysis" }));
      throw new Error(err.detail || "Failed to start analysis");
    }
    const data = await res.json();
    state.jobId = data.job_id;
    pollJob();
  } catch (e) {
    showError(e.message);
    resetControls();
  }
}

function pollJob() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${state.jobId}`);
      if (!res.ok) throw new Error("Lost track of job");
      const job = await res.json();
      updateProgress(job);
      if (job.status === "done") {
        clearInterval(state.pollTimer);
        showResults(job.result);
        resetControls();
      } else if (job.status === "error") {
        clearInterval(state.pollTimer);
        showError(job.error || "Analysis failed");
        resetControls();
      } else if (job.status === "cancelled") {
        clearInterval(state.pollTimer);
        progressLabel.textContent = "Cancelled.";
        resetControls();
      }
    } catch (e) {
      clearInterval(state.pollTimer);
      showError(e.message);
      resetControls();
    }
  }, 600);
}

function updateProgress(job) {
  const pct = Math.max(0, Math.min(100, job.percent || 0));
  progressFill.style.width = `${pct}%`;
  const frameInfo = job.total_frames
    ? `frame ${job.current_frame}/${job.total_frames}`
    : `frame ${job.current_frame}`;
  const fpsInfo = job.fps ? ` · ${job.fps.toFixed(1)} fps` : "";
  progressLabel.textContent = `${job.stage} — ${pct.toFixed(1)}% (${frameInfo}${fpsInfo})`;
}

async function cancelAnalysis() {
  if (!state.jobId) return;
  try {
    await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
  } catch (e) {
    console.error(e);
  }
}

function resetControls() {
  runBtn.disabled = !(state.reference && state.distorted);
  cancelBtn.classList.add("hidden");
}

function showError(message) {
  errorSection.textContent = message;
  errorSection.classList.remove("hidden");
  progressSection.classList.add("hidden");
}

const METRIC_META = {
  vmaf: { label: "VMAF", digits: 2, goodMin: 90, warnMin: 70 },
  psnr_y: { label: "PSNR (Y)", digits: 2, unit: "dB", goodMin: 40, warnMin: 30 },
  psnr_cb: { label: "PSNR (Cb)", digits: 2, unit: "dB" },
  psnr_cr: { label: "PSNR (Cr)", digits: 2, unit: "dB" },
  float_ssim: { label: "SSIM", digits: 4, goodMin: 0.95, warnMin: 0.85 },
  float_ms_ssim: { label: "MS-SSIM", digits: 4, goodMin: 0.95, warnMin: 0.85 },
};

function scoreClass(value, meta) {
  if (meta.goodMin === undefined) return "";
  if (value >= meta.goodMin) return "good";
  if (value >= meta.warnMin) return "warn";
  return "bad";
}

function showResults(result) {
  resultsSection.classList.remove("hidden");
  progressSection.classList.add("hidden");
  scoreCards.innerHTML = "";

  const htmlLink = document.getElementById("report-html-link");
  const jsonLink = document.getElementById("report-json-link");
  htmlLink.href = `/api/jobs/${state.jobId}/report.html`;
  jsonLink.href = `/api/jobs/${state.jobId}/report.json`;
  jsonLink.setAttribute("download", `vmaf-report-${state.jobId}.json`);

  const pooled = result.raw_pooled_metrics || {};
  // libvmaf also pools its internal elementary features (ADM/VIF/motion) —
  // those are implementation detail, not something the user asked to compare,
  // so only the headline metrics get a card; everything else is in the raw JSON below.
  const order = ["vmaf", "psnr_y", "psnr_cb", "psnr_cr", "float_ssim", "float_ms_ssim"];
  const keys = order.filter((k) => k in pooled);

  for (const key of keys) {
    const stats = pooled[key];
    const meta = METRIC_META[key] || { label: key, digits: 3 };
    const cls = scoreClass(stats.mean, meta);
    const card = document.createElement("div");
    card.className = "score-card";
    card.innerHTML = `
      <div class="metric-name">${escapeHtml(meta.label)}</div>
      <div class="metric-value ${cls ? "score-" + cls : ""}" style="${cls === "good" ? "color:var(--good)" : cls === "warn" ? "color:var(--warn)" : cls === "bad" ? "color:var(--bad)" : ""}">
        ${fmtNum(stats.mean, meta.digits)}${meta.unit ? " " + meta.unit : ""}
      </div>
      <div class="metric-sub">
        <span>min ${fmtNum(stats.min, meta.digits)}</span>
        <span>max ${fmtNum(stats.max, meta.digits)}</span>
        <span>hmean ${fmtNum(stats.harmonic_mean, meta.digits)}</span>
      </div>
    `;
    scoreCards.appendChild(card);
  }

  rawMetrics.textContent = JSON.stringify(pooled, null, 2);
}

// ---------- init ----------
loadModels();
updateRunButtonState();
