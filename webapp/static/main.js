let editor;

require(["vs/editor/editor.main"], function () {
  const saved = localStorage.getItem('editor_code');
  editor = monaco.editor.create(document.getElementById("editor"), {
    value: saved || `# Paste your code here and click Analyze\n\nprint('hello world')\n`,
    language: "python",
    theme: "vs-dark",
    automaticLayout: true,
  });
  editor.onDidChangeModelContent(() => {
    try { localStorage.setItem('editor_code', editor.getValue()); } catch {}
  });
});

async function analyze() {
  const code = editor.getValue();
  const filename = document.getElementById('filename').value;
  const model = document.getElementById('model').value;
  const timeout = document.getElementById('timeout').value;
  showNotify('Analyzing… Debugger is analyzing the code');
  const res = await fetch('/api/analyze', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, filename, model, timeout })
  });
  const data = await res.json();
  hideNotify();
  if (!data.ok) {
    alert('Analyze error: ' + data.error);
    return;
  }
  document.getElementById('edits').value = JSON.stringify(data.result, null, 2);

  // Auto-apply edits to show corrected code; tests remain hidden
  const edits = (data.result && data.result.edits) ? data.result.edits : [];
  if (Array.isArray(edits) && edits.length > 0) {
    showNotify('Applying fixes…');
    try {
      const res2 = await fetch('/api/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, edits })
      });
      const data2 = await res2.json();
      if (data2.ok) {
        editor.setValue(data2.code);
        // Immediately cross-check the corrected code
        showNotify('Cross-checking… validating with hidden tests');
        const res3 = await fetch('/api/crosscheck', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: data2.code, model, timeout })
        });
        const data3 = await res3.json();
        if (data3.ok) {
          renderCrosscheck(data3);
        }
      }
    } catch (e) {
      // non-fatal
    } finally {
      hideNotify();
    }
  } else {
    // No edits proposed; still perform cross-check on current code
    showNotify('Cross-checking… validating with hidden tests');
    try {
      const res3 = await fetch('/api/crosscheck', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, model, timeout })
      });
      const data3 = await res3.json();
      if (data3.ok) {
        renderCrosscheck(data3);
      }
    } catch (e) {
      // ignore
    } finally {
      hideNotify();
    }
  }
}

async function applyFixes() {
  const code = editor.getValue();
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById('edits').value || '{}');
  } catch (e) {
    alert('Invalid JSON in Edits.');
    return;
  }
  const edits = parsed.edits || [];
  const res = await fetch('/api/apply', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, edits })
  });
  const data = await res.json();
  if (!data.ok) {
    alert('Apply error: ' + data.error);
    return;
  }
  editor.setValue(data.code);

  // After applying, cross-check the corrected code; summary only
  const model = document.getElementById('model').value;
  const timeout = document.getElementById('timeout').value;
  showNotify('Cross-checking… validating with hidden tests');
  try {
    const res2 = await fetch('/api/crosscheck', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: data.code, model, timeout })
    });
    const data2 = await res2.json();
    if (data2.ok) {
      renderCrosscheck(data2);
    }
  } catch (e) {
    // ignore non-fatal
  } finally {
    hideNotify();
  }
}

document.getElementById('analyze').addEventListener('click', analyze);
document.getElementById('apply').addEventListener('click', applyFixes);
document.getElementById('run').addEventListener('click', runCode);
// Vision buttons
document.getElementById('visionSubmit').addEventListener('click', visionSubmit);
document.getElementById('visionShot').addEventListener('click', captureScreenShot);
// fixFromCross is now triggered from Analyze/Apply flows only; no button in modal
document.getElementById('visionAnalyze').addEventListener('click', analyzeWithVision);
// Modal open/close helpers
function openCrossModal() {
  const overlay = document.getElementById('modalOverlay');
  if (overlay) overlay.classList.remove('hidden');
}
function closeCrossModal() {
  const overlay = document.getElementById('modalOverlay');
  if (overlay) overlay.classList.add('hidden');
  try {
    const editorEl = document.getElementById('editor');
    if (editorEl) editorEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (editor && editor.focus) editor.focus();
  } catch {}
}
document.addEventListener('click', (e) => {
  const t = e.target;
  if (!t) return;
  if (t.id === 'crossClose') closeCrossModal();
  if (t.id === 'modalOverlay') closeCrossModal();
});

// Optional: quick reset to a simple Hello World if needed
function resetToHello() {
  const snippet = "print('hello world')\n";
  editor.setValue(snippet);
}

async function runCode() {
  const code = editor.getValue();
  const res = await fetch('/api/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code })
  });
  const data = await res.json();
  const out = document.getElementById('runout');
  if (!data.ok) {
    out.value = `Error: ${data.error || 'Unknown error'}`;
    return;
  }
  out.value = `Exit: ${data.exitCode}\n\nSTDOUT:\n${data.stdout}\n\nSTDERR:\n${data.stderr}`;
}

function showNotify(msg) {
  const n = document.getElementById('notify');
  if (!n) return;
  n.textContent = msg;
  n.classList.remove('hidden');
}

function hideNotify() {
  const n = document.getElementById('notify');
  if (!n) return;
  n.classList.add('hidden');
}

function renderCrosscheck(payload) {
  // Render into modal and show
  const badge = document.getElementById('overallBadge');
  const countPass = document.getElementById('countPass');
  const countFail = document.getElementById('countFail');
  const summary = document.getElementById('crossSummary');
  const list = document.getElementById('testList');

  // Reset
  list.innerHTML = '';
  badge.className = 'badge';

  const overall = (payload.overall || 'mixed').toLowerCase();
  badge.textContent = overall;
  badge.classList.add(overall === 'pass' ? 'pass' : overall === 'fail' ? 'fail' : 'mixed');

  const counts = payload.counts || { pass: 0, fail: 0 };
  countPass.textContent = `${counts.pass || 0} passed`;
  countFail.textContent = `${counts.fail || 0} failed`;
  summary.textContent = payload.summary || '';

  const tests = Array.isArray(payload.tests) ? payload.tests : [];
  for (const t of tests) {
    const li = document.createElement('li');
    li.className = 'test';
    const status = (t.status || 'fail').toLowerCase();
    li.innerHTML = `
      <span class="dot ${status}"></span>
      <span class="name">${escapeHtml(t.name || 'Test')}</span>
      <span class="desc">${escapeHtml(t.description || '')}</span>
      <span class="reason">${escapeHtml(t.reason || '')}</span>
    `;
    list.appendChild(li);
  }
  openCrossModal();
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[s]));
}

async function fixUsingCross() {
  // Build a cross-check payload from current DOM state
  const overall = document.getElementById('overallBadge').textContent.trim();
  const passTxt = document.getElementById('countPass').textContent.trim();
  const failTxt = document.getElementById('countFail').textContent.trim();
  const summary = document.getElementById('crossSummary').textContent.trim();
  const tests = [];
  document.querySelectorAll('#testList .test').forEach(li => {
    const name = (li.querySelector('.name')?.textContent || '').trim();
    const desc = (li.querySelector('.desc')?.textContent || '').trim();
    const reason = (li.querySelector('.reason')?.textContent || '').trim();
    const dot = li.querySelector('.dot');
    const status = dot && dot.classList.contains('pass') ? 'pass' : 'fail';
    tests.push({ name, description: desc, status, reason });
  });
  const counts = {
    pass: parseInt(passTxt, 10) || 0,
    fail: parseInt(failTxt, 10) || 0,
  };

  const cross = { overall, summary, counts, tests };
  const code = editor.getValue();
  const model = document.getElementById('model').value;
  const timeout = document.getElementById('timeout').value;

  showNotify('Fixing using cross-check…');
  try {
    const res = await fetch('/api/fix_from_crosscheck', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, crosscheck: cross, model, timeout })
    });
    const data = await res.json();
    if (!data.ok) {
      alert('Fix error: ' + (data.error || 'unknown'));
      return;
    }
    // Update editor and edits panel
    if (data.code) editor.setValue(data.code);
    if (data.edits) document.getElementById('edits').value = JSON.stringify(data.edits, null, 2);

    // Re-run cross-check on updated code
    showNotify('Cross-checking updated code…');
    const res2 = await fetch('/api/crosscheck', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: editor.getValue(), model, timeout })
    });
    const data2 = await res2.json();
    if (data2.ok) renderCrosscheck(data2);
  } catch (e) {
    // ignore non-fatal
  } finally {
    hideNotify();
  }
}

async function analyzeWithVision() {
  const code = editor.getValue();
  const model = document.getElementById('model').value;
  const timeout = document.getElementById('timeout').value;
  const prompt = document.getElementById('visionPrompt').value || '';
  const imageUrl = document.getElementById('visionImageUrl').value || '';
  const fileInput = document.getElementById('visionImageFile');

  let imageBase64 = null;
  if (fileInput && fileInput.files && fileInput.files[0]) {
    imageBase64 = await fileToBase64(fileInput.files[0]);
  }

  showNotify('Vision analyzing… comparing image and code');
  try {
    const res = await fetch('/api/vision_analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, prompt, imageUrl, imageBase64, model, timeout })
    });
    const data = await res.json();
    if (!data.ok) {
      alert('Vision analyze error: ' + (data.error || 'unknown'));
      return;
    }
    if (data.edits) document.getElementById('edits').value = JSON.stringify(data.edits, null, 2);
    if (data.code) editor.setValue(data.code);

    // Cross-check updated or original code
    showNotify('Cross-checking (post-vision)…');
    const res2 = await fetch('/api/crosscheck', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: editor.getValue(), model, timeout })
    });
    const data2 = await res2.json();
    if (data2.ok) renderCrosscheck(data2);
  } catch (e) {
    // ignore non-fatal
  } finally {
    hideNotify();
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      const base64 = typeof result === 'string' ? result.split(',')[1] : '';
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// Capture a screenshot of the screen (user chooses display/window)
let lastScreenshotBase64 = null;
async function captureScreenShot() {
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    const track = stream.getVideoTracks()[0];
    const imageCapture = new ImageCapture(track);
    const bitmap = await imageCapture.grabFrame();
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width; canvas.height = bitmap.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(bitmap, 0, 0);
    lastScreenshotBase64 = canvas.toDataURL('image/png').split(',')[1];
    track.stop();
    showNotify('Screenshot captured.');
  } catch (e) {
    alert('Screenshot failed: ' + (e && e.message ? e.message : e));
  }
}

// Submit: run code as-is, then compare with screenshot via vision endpoint; if mismatch, apply edits
async function visionSubmit() {
  const code = editor.getValue();
  const model = document.getElementById('model').value;
  const timeout = document.getElementById('timeout').value;
  const prompt = document.getElementById('visionPrompt').value || '';
  const imageBase64 = lastScreenshotBase64 || null;

  // Run first (no analyze) as requested
  const runRes = await fetch('/api/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code })
  });
  const runData = await runRes.json();
  if (!runData.ok) {
    showNotify('Run error: ' + (runData.error || 'unknown'));
  }

  // Vision compare + suggest edits
  showNotify('Comparing UI with screenshot…');
  const visRes = await fetch('/api/vision_analyze', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, prompt, imageBase64, model, timeout })
  });
  const visData = await visRes.json();
  if (!visData.ok) {
    hideNotify();
    alert('Vision analyze error: ' + (visData.error || 'unknown'));
    return;
  }

  // If edits provided, apply them immediately
  if (visData.edits && Array.isArray(visData.edits.edits) && visData.edits.edits.length) {
    document.getElementById('edits').value = JSON.stringify(visData.edits, null, 2);
    const applyRes = await fetch('/api/apply', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, edits: visData.edits.edits })
    });
    const applyData = await applyRes.json();
    if (applyData.ok && applyData.code) {
      editor.setValue(applyData.code);
      showNotify('Applied vision-based fixes.');
    } else if (!applyData.ok) {
      alert('Apply error: ' + (applyData.error || 'unknown'));
    }
  } else {
    showNotify('No changes suggested from vision.');
  }
  hideNotify();
}
