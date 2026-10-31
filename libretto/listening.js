// Local, deterministic audition synthesis. MIDI timing is never quantized.
let comparison = null, auditionContext = null, auditionNodes = [];
let auditionSerial = 0, auditionTimer = null, auditionEnd = null;
const heardSlots = new Set();

function listenEsc(value) {
  return String(value).replace(/[&<>"]/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;'}[c]));
}

function renderGroove(g) {
  if (!g || !g.available) return '';
  const swing = g.swing
    ? `Charley : ${g.swing.long_percent} % / ${100 - g.swing.long_percent} % · ${g.swing.pairs} paires`
    : 'Swing : matière insuffisante ou mesure composée';
  return `<details class="groove"><summary>Groove · ${g.drum_notes} frappes</summary>
    <p>${listenEsc(swing)}</p>
    <div class="groovevoices">${g.voices.map(v => `<div>
      <b>${listenEsc(v.name)}</b><span>${v.median_offset_ms >= 0 ? '+' : ''}${v.median_offset_ms} ms</span>
      <small>dispersion ${v.spread_ms} ms · vélocités ${v.velocity_min}–${v.velocity_max}</small>
      ${v.grids.map(grid => { const max = Math.max(1, ...grid.hits);
        return `<div class="groovegrid" style="grid-template-columns:repeat(${grid.hits.length},1fr)"
          aria-label="Empreinte ${listenEsc(grid.meter)}" title="Mesure ${listenEsc(grid.meter)}">`
          + grid.hits.map((n, i) => `<i style="opacity:${.12 + .88 * n / max}"
             title="pas ${i + 1} : ${n} frappes"></i>`).join('') + '</div>';
      }).join('')}</div>`).join('')}</div>
    <p class="listeninghint">Référence double-croche ; + = retard, − = avance.
    Dispersion = écart absolu médian. ${listenEsc(g.notice)}</p></details>`;
}

function stopPreview() {
  auditionSerial++;
  clearTimeout(auditionTimer); clearTimeout(auditionEnd);
  auditionNodes.forEach(node => { try { node.stop(); } catch (_) {} });
  auditionNodes = [];
  document.querySelectorAll('[data-audition]').forEach(b => {
    b.classList.remove('isplaying'); b.textContent = '▶ Écouter ' + b.dataset.audition;
  });
}

function synthesizePreview(ctx, notes) {
  const master = ctx.createGain(); master.gain.value = .22;
  const limiter = ctx.createDynamicsCompressor();
  master.connect(limiter); limiter.connect(ctx.destination);
  const noise = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * .3), ctx.sampleRate);
  const samples = noise.getChannelData(0);
  let seed = 123456789;
  for (let i = 0; i < samples.length; i++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    samples[i] = seed / 2147483648 - 1;
  }
  const t0 = ctx.currentTime + .06;
  const nodes = [];
  for (const [start, duration, pitch, velocity, drum] of notes) {
    const t = t0 + start, v = velocity / 127;
    const gain = ctx.createGain(), filter = ctx.createBiquadFilter();
    let source, end;
    if (drum && (pitch === 35 || pitch === 36)) {
      source = ctx.createOscillator(); source.frequency.setValueAtTime(130, t);
      source.frequency.exponentialRampToValueAtTime(45, t + .12);
      filter.type = 'lowpass'; filter.frequency.value = 800; end = t + .18;
    } else if (drum) {
      source = ctx.createBufferSource(); source.buffer = noise;
      filter.type = pitch >= 42 ? 'highpass' : 'bandpass';
      filter.frequency.value = pitch >= 42 ? 5500 : 1800;
      end = t + (pitch >= 42 ? .07 : .16);
    } else {
      source = ctx.createOscillator(); source.type = 'triangle';
      const hz = 440 * Math.pow(2, (pitch - 69) / 12);
      source.frequency.value = hz;
      filter.type = 'lowpass'; filter.frequency.value = Math.min(12000, hz * (2 + 8 * v * v));
      end = t + Math.max(.03, duration);
    }
    gain.gain.setValueAtTime(.0001, t);
    gain.gain.linearRampToValueAtTime(.1 + .5 * v * v, t + .004);
    gain.gain.exponentialRampToValueAtTime(.0001, end);
    source.connect(filter); filter.connect(gain); gain.connect(master);
    source.onended = () => { source.disconnect(); filter.disconnect(); gain.disconnect(); };
    source.start(t); source.stop(end + .01); nodes.push(source);
  }
  const last = Math.max(0, ...notes.map(n => n[0] + n[1]));
  setTimeout(() => { master.disconnect(); limiter.disconnect(); }, (last + 1) * 1000);
  return nodes;
}

function enablePreferenceButtons() {
  const ready = comparison && !comparison.saved && comparison.slots.every(slot => heardSlots.has(slot));
  document.querySelectorAll('[data-prefer]').forEach(b => b.disabled = !ready);
}

async function audition(slot, button) {
  stopPreview();
  const serial = auditionSerial, active = comparison;
  if (!active) return;
  button.textContent = 'Chargement…';
  const message = document.getElementById('listeningstatus');
  try {
    if (!auditionContext) auditionContext = new (window.AudioContext || window.webkitAudioContext)();
    await auditionContext.resume();
    const r = await fetch('/api/audition', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({comparison:active.id, slot})});
    const data = await r.json();
    if (serial !== auditionSerial) return;
    if (!r.ok) throw new Error(data.error);
    if (!data.notes.length) throw new Error('Aucune note dans les 20 premières secondes.');
    auditionNodes = synthesizePreview(auditionContext, data.notes);
    button.classList.add('isplaying'); button.textContent = '♫ ' + slot;
    message.textContent = `Écoute ${slot} · ${data.duration.toFixed(1)} s`
      + (data.truncated ? ' · extrait limité à 4 000 notes' : '');
    auditionTimer = setTimeout(() => {
      if (serial !== auditionSerial) return;
      heardSlots.add(slot); enablePreferenceButtons();
    }, (data.notes[0][0] + Math.min(1, data.notes[0][1]) + .1) * 1000);
    auditionEnd = setTimeout(stopPreview, (data.duration + .3) * 1000);
  } catch (error) {
    if (serial === auditionSerial) message.textContent = 'Écoute impossible : ' + error.message;
  } finally {
    if (serial === auditionSerial && !button.classList.contains('isplaying'))
      button.textContent = '▶ Écouter ' + slot;
  }
}

async function refreshPreferenceCount() {
  const el = document.getElementById('preferencecount');
  try {
    const r = await fetch('/api/preferences'), data = await r.json();
    el.textContent = r.ok ? `${data.count} choix enregistré(s) sur ce Mac`
      : 'Préférences indisponibles : ' + data.error;
  } catch (_) { el.textContent = 'Préférences indisponibles'; }
}

function startComparison(data) {
  stopPreview(); heardSlots.clear(); comparison = data;
  const panel = document.getElementById('listeningpanel');
  panel.hidden = !data;
  document.getElementById('genresults').hidden = !!data;
  if (!data) return;
  document.getElementById('listeningstatus').textContent =
    'Écoute chaque proposition, puis choisis celle que tu préfères.';
  document.getElementById('listeningchoices').innerHTML = data.slots.map(slot =>
    `<div class="auditionchoice"><strong>${slot}</strong>
      <button class="primary" data-audition="${slot}">▶ Écouter ${slot}</button>
      <button data-prefer="${slot}" disabled>Je préfère ${slot}</button></div>`).join('');
  document.getElementById('preferSame').disabled = true;
  refreshPreferenceCount();
}

async function savePreference(choice) {
  if (!comparison || comparison.saved || !comparison.slots.every(slot => heardSlots.has(slot))) return;
  stopPreview();
  const active = comparison;
  document.querySelectorAll('[data-prefer]').forEach(b => b.disabled = true);
  const message = document.getElementById('listeningstatus');
  try {
    const r = await fetch('/api/preference', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({comparison:active.id, choice})});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    if (comparison !== active) return;
    const reveal = active.slots.map(s => `${s} : ${data.candidates[s].name}`).join(' · ');
    message.textContent = (choice === 'same' ? 'Choix « aucune préférence » enregistré.' : `Préférence ${choice} enregistrée.`)
      + ' ' + reveal;
    comparison.saved = true;
    document.getElementById('genresults').hidden = false;
    refreshPreferenceCount();
  } catch (error) {
    message.textContent = 'Choix non enregistré : ' + error.message;
    enablePreferenceButtons();
  }
}

document.getElementById('listeningpanel').addEventListener('click', event => {
  const listen = event.target.closest('[data-audition]');
  if (listen) { audition(listen.dataset.audition, listen); return; }
  const prefer = event.target.closest('[data-prefer]');
  if (prefer) savePreference(prefer.dataset.prefer);
});
document.getElementById('auditionstop').addEventListener('click', stopPreview);
document.getElementById('skipcomparison').addEventListener('click', () => {
  stopPreview(); comparison = null;
  document.getElementById('listeningpanel').hidden = true;
  document.getElementById('genresults').hidden = false;
});
window.addEventListener('pagehide', stopPreview);
