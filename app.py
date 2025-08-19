# app.py
import os, time, json
from flask import Flask, request, jsonify, Response, render_template, make_response
from yt_dlp.utils import DownloadError
from downloader import (
    JOBS, create_job, cancel_job, get_job,
    probe_url_meta, STORAGE_DIR,
)

app = Flask(__name__, template_folder="templates", static_folder=None)

# ---- Inline static JS so it never 404s ----
APP_JS = r"""
const el = (id) => document.getElementById(id);

// Inputs and controls
const urlInput = el('url');
const outputDirInput = el('outputDir');
const probeBtn = el('probeBtn');
const probeCancelBtn = el('probeCancelBtn');
const probeSpinner = el('probeSpinner');
const probeBox = el('probeBox');

const titleEl = el('title');
const kindEl = el('kind');
const thumbEl = el('thumb');

const mediaTypeSel = el('mediaType');
const videoQBox = el('videoQBox');
const audioQBox = el('audioQBox');
const videoHeightSel = el('videoHeight');
const audioBitrateSel = el('audioBitrate');

const downloadBtn = el('downloadBtn');
const playlistBox = el('playlistBox');
const playlistList = el('playlistList');
const selectAll = el('selectAll');
const selectionCount = el('selectionCount');

// Job/Stats UI
const jobBox = el('jobBox');
const jobTitle = el('jobTitle');
const jobPlan  = el('jobPlan');
const destPath = el('destPath');
const statusBadge = el('statusBadge');
const cancelBtn = el('cancelBtn');

const bar = el('bar');
const percent = el('percent');
const speed = el('speed');
const eta = el('eta');
const items = el('items');
const message = el('message');

let currentProbe = null;
let currentJobId = null;
let es = null;
let probeController = null;

function setProbeLoading(isLoading){
  if (isLoading){
    probeBtn.disabled = true;
    probeCancelBtn.classList.remove('hidden');
    probeSpinner.classList.remove('hidden');
  } else {
    probeBtn.disabled = false;
    probeCancelBtn.classList.add('hidden');
    probeSpinner.classList.add('hidden');
  }
}

function resetProbeUI(){
  // Clear previous probe results before a new probe
  probeBox.classList.add('hidden');
  titleEl.textContent = '';
  kindEl.textContent = '';
  thumbEl.src = '';
  videoHeightSel.innerHTML = '<option value=\"\">Best</option>';
  playlistBox.classList.add('hidden');
  playlistList.innerHTML = '';
  selectionCount.textContent = '0 selected';
  downloadBtn.disabled = false;
}

mediaTypeSel.addEventListener('change', () => {
  if (mediaTypeSel.value === 'audio') {
    audioQBox.classList.remove('hidden');
    videoQBox.classList.add('hidden');
  } else {
    videoQBox.classList.remove('hidden');
    audioQBox.classList.add('hidden');
  }
});

function updateSelectionCount() {
  const checked = playlistList.querySelectorAll('input[type="checkbox"]:checked').length;
  selectionCount.textContent = `${checked} selected`;
  downloadBtn.disabled = (checked === 0 && (currentProbe?.kind === 'playlist'));
}

selectAll.addEventListener('change', () => {
  const boxes = playlistList.querySelectorAll('input[type="checkbox"]');
  boxes.forEach(b => { b.checked = selectAll.checked; });
  updateSelectionCount();
});

probeBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) return alert('Paste a YouTube URL first.');

  // clear old data and start fresh
  resetProbeUI();
  setProbeLoading(true);
  probeController = new AbortController();

  try {
    const res = await fetch('/api/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
      signal: probeController.signal,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Probe failed');
    currentProbe = data;

    probeBox.classList.remove('hidden');
    titleEl.textContent = data.title || '';
    kindEl.textContent = `${(data.kind||'').toUpperCase()}`;
    if (data.thumbnail) thumbEl.src = data.thumbnail;

    // Populate heights
    videoHeightSel.innerHTML = '<option value=\"\">Best</option>';
    (data.availableHeights || []).forEach(h => {
      const opt = document.createElement('option');
      opt.value = String(h);
      opt.textContent = `${h}p`;
      videoHeightSel.appendChild(opt);
    });

    // Playlist UI
    if (data.kind === 'playlist') {
      playlistBox.classList.remove('hidden');
      playlistList.innerHTML = '';
      (data.entries || []).forEach((e) => {
        const row = document.createElement('div');
        row.className = 'flex items-center gap-3 py-1 border-b border-slate-800';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = e.url;
        cb.className = 'w-4 h-4';
        cb.addEventListener('change', updateSelectionCount);

        const meta = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'text-sm';
        title.textContent = `${e.index}. ${e.title}`;
        const sub = document.createElement('div');
        sub.className = 'text-xs text-slate-400';
        sub.textContent = e.duration ? `Duration: ${e.duration}` : 'Video';

        meta.appendChild(title);
        meta.appendChild(sub);
        row.appendChild(cb);
        row.appendChild(meta);

        playlistList.appendChild(row);
      });
      selectAll.checked = false;
      updateSelectionCount();
      downloadBtn.textContent = 'Download Selected';
    } else {
      playlistBox.classList.add('hidden');
      downloadBtn.textContent = 'Start Download';
      downloadBtn.disabled = false;
    }
  } catch (e) {
    if (e.name === 'AbortError') {
      // user canceled probe
    } else {
      alert(String(e));
    }
  } finally {
    setProbeLoading(false);
    probeController = null;
  }
});

probeCancelBtn.addEventListener('click', () => {
  if (probeController) { try { probeController.abort(); } catch {} }
  setProbeLoading(false);
});

downloadBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) return alert('Paste a YouTube URL first.');
  const mediaType = mediaTypeSel.value;
  const videoHeight = videoHeightSel.value ? Number(videoHeightSel.value) : null;
  const audioBitrate = audioBitrateSel.value;
  const outputDir = outputDirInput.value.trim();

  let selectedUrls = [];
  if (currentProbe && currentProbe.kind === 'playlist') {
    selectedUrls = Array.from(playlistList.querySelectorAll('input[type="checkbox"]:checked'))
      .map(cb => cb.value);
    if (selectedUrls.length === 0) {
      return alert('Please select at least one video from the playlist.');
    }
  }

  // Friendly header
  jobTitle.textContent = currentProbe?.title || 'Download';
  const quality =
    mediaType === 'audio'
      ? (audioBitrate === 'best' ? 'Best audio' : `${audioBitrate} kbps`)
      : (videoHeight ? `${videoHeight}p` : 'Best video');
  jobPlan.textContent = `${mediaType.toUpperCase()} • ${quality}`;
  destPath.textContent = outputDir || '(Downloads bucket)';

  // Reset stats UI
  setStatusBadge('queued');
  bar.style.width = '0%';
  percent.textContent = '0%';
  items.textContent = '0 / 0';
  speed.textContent = '';
  eta.textContent = '';
  message.textContent = '';

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, mediaType, videoHeight, audioBitrate, selectedUrls, outputDir }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Job create failed');
    const jobId = data.jobId;
    currentJobId = jobId;
    jobBox.classList.remove('hidden');
    startStream(jobId);
  } catch (e) {
    alert(String(e));
  }
});

cancelBtn.addEventListener('click', async () => {
  if (!currentJobId) return;
  try { await fetch(`/api/jobs/${currentJobId}`, { method: 'DELETE' }); } catch {}
});

function setStatusBadge(status){
  statusBadge.textContent = status.toUpperCase();
  statusBadge.className = 'px-2 py-0.5 rounded text-xs font-medium';
  if (status === 'queued') statusBadge.classList.add('bg-slate-700','text-slate-100');
  else if (status === 'running') statusBadge.classList.add('bg-indigo-600','text-white');
  else if (status === 'done') statusBadge.classList.add('bg-emerald-600','text-white');
  else if (status === 'error') statusBadge.classList.add('bg-red-600','text-white');
  else if (status === 'canceled') statusBadge.classList.add('bg-zinc-600','text-white');
  else statusBadge.classList.add('bg-slate-700','text-slate-100');
}

function startStream(jobId) {
  if (es) es.close();
  es = new EventSource(`/api/stream/${jobId}`);
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.status) setStatusBadge(data.status);
    if (data.finalDir) destPath.textContent = data.finalDir;

    percent.textContent = data.percent || '0%';
    bar.style.width = `${Math.floor(data.progress || 0)}%`;
    speed.textContent = data.speed || '';
    eta.textContent = data.eta || '';
    items.textContent = `${data.currentItem || 0} / ${data.totalItems || 0}`;
    message.textContent = data.currentTitle || data.message || '';

    if (['done', 'error', 'canceled'].includes(data.status)) {
      es.close();
    }
  };
  es.onerror = () => {};
}
"""

@app.route("/static/app.js")
def static_app_js():
    resp = make_response(APP_JS)
    resp.mimetype = "application/javascript"
    return resp

@app.get("/")
def home():
    return render_template("index.html")

# ------------------ API -----------------
@app.post("/api/probe")
def api_probe():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400
    try:
        meta = probe_url_meta(url)
        return jsonify(meta)
    except DownloadError:
        return jsonify({"error": "Probe failed: video/playlist unavailable or requires login (try cookies.txt)."}), 404
    except Exception as e:
        return jsonify({"error": f"Probe failed: {e}"}), 400

@app.post("/api/jobs")
def api_create_job():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url", "").strip()
    media_type = data.get("mediaType")
    video_height = data.get("videoHeight")
    audio_bitrate = data.get("audioBitrate")
    selected_urls = data.get("selectedUrls") or []
    output_dir = data.get("outputDir") or None

    if not url or media_type not in ("video", "audio"):
        return jsonify({"error": "Invalid payload"}), 400

    job_id = create_job(url, media_type, video_height, audio_bitrate, selected_urls, output_dir)
    return jsonify({"jobId": job_id})

@app.get("/api/jobs/<job_id>")
def api_job_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    return jsonify(job)

@app.delete("/api/jobs/<job_id>")
def api_cancel_job(job_id):
    if cancel_job(job_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.get("/api/stream/<job_id>")
def api_stream(job_id):
    def gen():
        last_payload = None
        while True:
            job = get_job(job_id)
            if not job:
                yield "event: error\ndata: {}\n\n"
                break
            payload = json.dumps(job, ensure_ascii=False)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if job["status"] in ("done", "error", "canceled"):
                break
            time.sleep(0.7)
    return Response(gen(), mimetype="text/event-stream")

if __name__ == "__main__":
    os.makedirs(STORAGE_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=8000, debug=True)
