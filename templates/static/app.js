// static/app.js
const el = (id) => document.getElementById(id);

const urlInput = el('url');
const probeBtn = el('probeBtn');
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

const jobBox = el('jobBox');
const jobIdEl = el('jobId');
const cancelBtn = el('cancelBtn');
const bar = el('bar');
const percent = el('percent');
const speed = el('speed');
const eta = el('eta');
const message = el('message');
const filesBox = el('files');
const fileList = el('fileList');

let currentProbe = null;
let currentJobId = null;
let es = null;

mediaTypeSel.addEventListener('change', () => {
  if (mediaTypeSel.value === 'audio') {
    audioQBox.classList.remove('hidden');
    videoQBox.classList.add('hidden');
  } else {
    videoQBox.classList.remove('hidden');
    audioQBox.classList.add('hidden');
  }
});

probeBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) return alert('Paste a YouTube URL first.');
  try {
    const res = await fetch('/api/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Probe failed');
    currentProbe = data;
    probeBox.classList.remove('hidden');
    titleEl.textContent = data.title || '';
    kindEl.textContent = `${data.kind?.toUpperCase()} • choose options below`;
    if (data.thumbnail) thumbEl.src = data.thumbnail; else thumbEl.src = '';
    // Populate heights
    videoHeightSel.innerHTML = '<option value=\"\">Best</option>';
    (data.availableHeights || []).forEach(h => {
      const opt = document.createElement('option');
      opt.value = String(h);
      opt.textContent = `${h}p`;
      videoHeightSel.appendChild(opt);
    });
  } catch (e) {
    alert(String(e));
  }
});

downloadBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) return alert('Paste a YouTube URL first.');
  const mediaType = mediaTypeSel.value;
  const videoHeight = videoHeightSel.value ? Number(videoHeightSel.value) : null;
  const audioBitrate = audioBitrateSel.value;

  try {
    const res = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, mediaType, videoHeight, audioBitrate }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Job create failed');
    currentJobId = data.jobId;
    jobIdEl.textContent = currentJobId;
    jobBox.classList.remove('hidden');
    filesBox.classList.add('hidden');
    fileList.innerHTML = '';
    startStream(currentJobId);
  } catch (e) {
    alert(String(e));
  }
});

cancelBtn.addEventListener('click', async () => {
  if (!currentJobId) return;
  await fetch(`/api/jobs/${currentJobId}`, { method: 'DELETE' });
});

function startStream(jobId) {
  if (es) es.close();
  es = new EventSource(`/api/stream/${jobId}`);
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    percent.textContent = data.percent || '0%';
    bar.style.width = `${Math.floor(data.progress || 0)}%`;
    speed.textContent = data.speed || '';
    eta.textContent = data.eta || '';
    message.textContent = data.message || '';

    if (['done', 'error', 'canceled'].includes(data.status)) {
      es.close();
      fetch(`/api/jobs/${jobId}/files`)
        .then(r => r.json())
        .then(({ files }) => {
          if (files && files.length) {
            filesBox.classList.remove('hidden');
            fileList.innerHTML = '';
            files.forEach(f => {
              const li = document.createElement('li');
              const a = document.createElement('a');
              a.href = `/api/jobs/${jobId}/files/${encodeURI(f)}`;
              a.textContent = f;
              a.className = 'text-indigo-400 hover:underline';
              li.appendChild(a);
              fileList.appendChild(li);
            });
          }
        });
    }
  };
  es.onerror = () => { /* stream closes after completion; ignore */ };
}
