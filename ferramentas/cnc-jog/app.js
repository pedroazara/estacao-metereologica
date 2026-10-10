'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="panel-token"]').content;
const client = crypto.randomUUID();
let state = null, xyStep = 1, zStep = 0.5, localBusy = false, connecting = false;
let lastLog = 0, toastTimer, pollBusy = false, lastPorts = 0;
let actionError = '', heartbeatBusy = false;
let movementMode = 'step', activePress = null, holdSequence = 0;
let holdStopBusy = 0, modeChanging = false, lastStateRequest = 0, requireStateAfter = 0;
let safetyBlocked = false, safetyGeneration = 0, arming = false;

async function api(path, body, timeout = 2500) {
  const response = await fetch('/api/' + path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {'X-Panel-Token': token, 'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify({...body, client}),
    signal: AbortSignal.timeout(timeout), cache: 'no-store'
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || 'Falha na comunicação com o painel.');
    error.status = response.status;
    throw error;
  }
  return data;
}
function notify(text, error = false) {
  $('toast').textContent = text;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('toast').hidden = true, error ? 7500 : 3000);
  if (error) actionError = text;
}
function format(value) {
  return Number.isFinite(value) ? value.toFixed(3).replace('.', ',') : '—';
}
function controlsReady() {
  return state?.connected && state.ready && state.fresh && state.armed &&
    state.owner === client && state.state === 'Idle' && !state.busy && !localBusy &&
    !safetyBlocked && !arming && !holdStopBusy && !modeChanging && lastStateRequest >= requireStateAfter && !document.hidden;
}
function holdBody(press, includeIntent = true) {
  const body = {epoch: press.epoch, gesture: press.gesture, seq: ++holdSequence};
  if (!includeIntent) return body;
  const {axis, direction, ratio = 1} = press.intent;
  const field = $(axis === 'Z' ? 'z-feed' : 'xy-feed');
  if (!field.value || !field.validity.valid) throw new Error('Revise a velocidade antes de continuar.');
  const minimum = axis === 'Z' ? 30 : 60;
  return {...body, axis, direction, feed: Math.max(minimum, Math.round(Number(field.value) * ratio))};
}
function showHold() {
  const press = activePress;
  const moving = !!press?.startSent && !press.latched && !press.released;
  $('hold-guidance').classList.toggle('moving', moving);
  $('hold-guidance').classList.toggle('latched', !!press?.latched);
  $('hold-status').textContent = press?.latched ? (press.reason || 'Movimento interrompido. Solte antes de tentar novamente.') : moving ? `${press.intent.axis}${press.intent.direction > 0 ? '+' : '−'} · solte para parar` : press ? 'Arraste para fora do centro para começar.' : holdStopBusy ? 'Solicitando parada… aguarde o estado Idle.' : 'Segure um botão; solte para parar.';
  document.querySelectorAll('.jog').forEach(button => button.classList.toggle('held', moving && press.source === button));
  $('joystick').classList.toggle('active', !!press && press.source === $('joystick') && !press.latched);
  $('joystick').setAttribute('aria-disabled', String(!controlsReady() && press?.source !== $('joystick')));
  if (!press || press.source !== $('joystick') || press.latched) {
    $('joystick-knob').style.transform = '';
    $('joystick-readout').textContent = press?.latched && press.source === $('joystick') ? 'Solte para recomeçar' : 'Pressione e arraste';
  }
}
async function stopHold(press) {
  if (!press?.startSent || press.stopSent) return;
  press.stopSent = true;
  holdStopBusy++;
  const body = holdBody(press, false);
  render();
  try {await api('hold/stop', body, 1200);}
  catch (error) {
    safetyBlocked = true; safetyGeneration++;
    if (state?.armed && state.owner === client) {
      leave();
      if (error.status !== 409) notify('Parada sem confirmação. Controles bloqueados; use o desligamento físico se necessário.', true);
    }
  } finally {
    // A status request begun after the stop reply is required before another press.
    requireStateAfter = performance.now();
    holdStopBusy--;
    await poll();
    render();
  }
}
function latchPress(reason) {
  const press = activePress;
  if (!press || press.latched || press.released) return;
  press.latched = true;
  press.reason = reason;
  clearTimeout(press.deadline);
  stopHold(press);
  render();
}
function endPress() {
  const press = activePress;
  if (!press) return;
  press.released = true;
  activePress = null;
  clearTimeout(press.deadline);
  if (press.startSent) $('command-preview').textContent = 'Parada solicitada. Aguarde a confirmação Idle antes de pressionar novamente.';
  // Clear the active press before releasing capture: lostpointercapture may fire now.
  if (press.pointerId !== undefined && press.source.hasPointerCapture?.(press.pointerId)) {
    try {press.source.releasePointerCapture(press.pointerId);} catch (_) {}
  }
  stopHold(press);
  render();
}
async function sendHold(press) {
  if (activePress !== press || press.released || press.latched || press.requestBusy || !press.intent) return;
  if (safetyBlocked || !state?.armed || state.owner !== client || !state.fresh || state.control_epoch !== press.epoch) {
    latchPress('Controles bloqueados. Solte e habilite novamente.'); return;
  }
  let body;
  try {body = holdBody(press);}
  catch (error) {latchPress(error.message); return;}
  const starting = !press.startSent;
  press.requestBusy = true;
  press.startSent = true;
  if (starting) press.deadline = setTimeout(() => {
    if (activePress === press) {latchPress('Limite de 10 segundos. Solte e habilite novamente.'); cancel();}
  }, 10000);
  showHold();
  try {
    const result = await api(starting ? 'hold/start' : 'hold/pulse', body, 1000);
    if (press.released || press.latched || activePress !== press) return;
    if (result.ignored || result.command?.ignored) {
      latchPress('Comando desatualizado bloqueado. Solte antes de recomeçar.'); return;
    }
    if (starting) press.startAcknowledgedAt = performance.now();
    $('command-preview').textContent = `Segurando ${body.axis}${body.direction > 0 ? '+' : '−'} · até ${body.feed} mm/min`;
  } catch (error) {
    if (!press.released && activePress === press && !press.latched) {
      latchPress('Comunicação interrompida. Solte e habilite novamente.');
      notify(error.message, true);
      leave();
    }
  } finally {press.requestBusy = false;}
}
function beginPress(source, input, intent = null) {
  if (movementMode !== 'hold' || activePress || !controlsReady()) return false;
  activePress = {source, ...input, epoch: state.control_epoch, gesture: crypto.randomUUID(), intent,
    startSent: false, stopSent: false, latched: false, released: false, requestBusy: false};
  if (input.pointerId !== undefined) {
    try {source.setPointerCapture(input.pointerId);} catch (_) {activePress = null; return false;}
  }
  if (intent) sendHold(activePress);
  render();
  return true;
}
function reconcileHold(requestStarted) {
  const press = activePress;
  if (!press || press.latched || press.released) return;
  if (!state.armed || state.owner !== client || !state.connected || !state.ready || !state.fresh || state.control_epoch !== press.epoch) {
    $('confirm').checked = false;
    latchPress('Controles bloqueados. Solte e habilite novamente.');
  } else if (press.startAcknowledgedAt && requestStarted >= press.startAcknowledgedAt &&
    (!state.holding || state.hold_gesture !== press.gesture)) {
    latchPress('Movimento encerrado pelo painel. Solte antes de recomeçar.');
  }
}
function render() {
  if (!state) return;
  const online = state.connected && state.ready && state.fresh;
  const mine = state.armed && state.owner === client;
  $('status-dot').className = 'status-dot' + (online ? ' live' : '');
  $('connection-title').textContent = online ? (state.simulation ? 'Simulador conectado' : 'CNC conectada') : state.initializing ? 'Validando comunicação USB' : state.connected ? (state.ready ? 'Aguardando resposta USB' : 'Reconexão necessária') : 'Desconectado';
  $('connection-detail').textContent = state.connected ? `${state.device} · ${state.fresh ? state.state : 'aguardando estado da placa'} · ${state.simulation ? 'sem USB físico' : '115200 baud'}` : 'Selecione a controladora ou a demonstração';
  $('connect').textContent = connecting ? 'Aguarde…' : state.connected ? 'Desconectar' : 'Conectar';
  $('connect').disabled = connecting;
  if (state.connected && [...$('port').options].some(option => option.value === state.device)) $('port').value = state.device;
  $('port').disabled = state.connected || connecting;
  $('refresh').disabled = state.connected || connecting;
  $('demo-banner').hidden = !state.connected || !state.simulation;
  for (const [i, axis] of ['x', 'y', 'z'].entries()) {
    $('pos-' + axis).textContent = online && state.settings['13'] === '0' ? format(state.position?.[i]) : '—';
  }
  $('reference').textContent = state.simulation && online ? 'Posição virtual · demonstração' : 'Referência física não validada';
  $('confirm-label').textContent = state.simulation ? 'Entendo que este modo simula movimentos, sem controlar a máquina.' : 'Estou ao lado da máquina, com área livre e spindle parado.';
  $('confirm').disabled = mine;
  $('arm').textContent = mine ? safetyBlocked ? 'Confirmar bloqueio' : 'Bloquear controles' : 'Habilitar JOG →';
  $('arm').disabled = arming || modeChanging || localBusy || holdStopBusy > 0 || (!mine && (!online || !state.ready || state.state !== 'Idle' || state.busy || !$('confirm').checked || (state.armed && !mine)));
  $('arm-title').textContent = mine ? safetyBlocked ? 'Bloqueados localmente · confirme a parada' : 'Controles habilitados nesta aba' : state.armed ? 'Outra aba está no controle' : 'Preparar os controles';
  document.querySelectorAll('.jog').forEach(button => {
    // Do not disable a held element: pointerup/cancel must keep reaching its handlers.
    button.disabled = activePress ? activePress.source !== button : !controlsReady();
  });
  $('mode-step').disabled = $('mode-hold').disabled = modeChanging;
  showHold();
  const message = actionError || state.message;
  $('notice').textContent = message;
  $('notice').classList.toggle('error', !!actionError);
  $('move-status').textContent = !online ? '○ Aguardando conexão' : state.busy || state.state === 'Jog' ? '● Movimento em curso' : mine && !safetyBlocked ? '● Pronto para JOG' : '○ Controles bloqueados';
  $('live-feed').textContent = online ? Math.round(state.feed).toString() : '—';
  $('live-spindle').textContent = !online || state.rpm === null ? '—' : state.rpm === 0 ? '0 rpm · parado' : `${state.rpm} rpm`;
  $('live-pins').textContent = online ? state.pins || 'Nenhuma' : '—';
  const soft = state.settings['20'], hard = state.settings['21'];
  $('live-limits').textContent = !online ? 'Aguardando leitura' : soft === '0' && hard === '0' ? 'Desativados' : `Software: ${soft === '1' ? 'sim' : 'não'} · físicos: ${Number(hard) & 1 ? 'sim' : 'não'}`;
  const logs = $('logs');
  let appended = false;
  for (const log of state.logs) {
    if (log.id <= lastLog) continue;
    lastLog = log.id;
    const row = document.createElement('div');
    row.className = 'log-line ' + log.direction;
    const time = document.createElement('time'); time.textContent = log.time;
    const dir = document.createElement('span'); dir.className = 'direction'; dir.textContent = log.direction === 'tx' ? '→' : log.direction === 'rx' ? '←' : '·';
    const text = document.createElement('span'); text.textContent = log.text;
    row.append(time, dir, text); logs.append(row); appended = true;
  }
  while (logs.children.length > 160) logs.firstChild.remove();
  if (appended) logs.scrollTop = logs.scrollHeight;
}
async function refreshPorts() {
  const choices = await api('ports');
  const old = $('port').value;
  $('port').replaceChildren(...choices.map(p => {
    const option = document.createElement('option');
    option.value = p.device;
    option.textContent = p.device === 'SIMULADOR' ? 'Demonstração · sem CNC' : `${p.device} · SKR / NXP`;
    return option;
  }));
  if (choices.some(p => p.device === old)) $('port').value = old;
  lastPorts = Date.now();
}
async function poll() {
  if (pollBusy || document.hidden) return;
  pollBusy = true;
  const requestStarted = performance.now();
  try {
    state = await api('state', undefined, 1400);
    lastStateRequest = requestStarted;
    reconcileHold(requestStarted);
    render();
    if (!state.connected && !connecting && Date.now() - lastPorts > 5000) await refreshPorts();
  } catch (error) {
    safetyBlocked = true; safetyGeneration++;
    if (state) {state.fresh = false; state.armed = false;}
    $('confirm').checked = false;
    latchPress('Servidor sem resposta. Solte e confira a parada física.');
    actionError = 'Servidor local sem resposta. Os controles foram bloqueados.';
    render();
  } finally {pollBusy = false;}
}
async function cancel() {
  safetyBlocked = true; safetyGeneration++;
  localBusy = true;
  endPress();
  if (state) state.armed = false;
  $('confirm').checked = false;
  render();
  let confirmed = false;
  try {
    await api('cancel', {});
    confirmed = true;
    actionError = '';
    notify('Cancelamento de JOG enviado. Controles bloqueados.');
  } catch (error) {
    notify('Não foi possível confirmar o cancelamento por USB. Use o desligamento físico se necessário.', true);
  } finally {localBusy = false; await poll();}
  return confirmed;
}
$('cancel').addEventListener('click', cancel);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.repeat) {event.preventDefault(); cancel();}
});
$('connect').addEventListener('click', async () => {
  if (connecting) return;
  endPress();
  connecting = true; $('confirm').checked = false; actionError = ''; render();
  try {
    if (state?.connected) await api('disconnect', {}, 6000);
    else await api('connect', {device: $('port').value}, 15000);
  } catch (error) {notify(error.message, true);}
  finally {connecting = false; await poll(); render();}
});
$('refresh').addEventListener('click', () => refreshPorts().catch(error => notify(error.message, true)));
$('confirm').addEventListener('change', render);
$('arm').addEventListener('click', async () => {
  if (arming) return;
  if (state?.armed && state.owner === client) return cancel();
  const generation = safetyGeneration;
  safetyBlocked = true; arming = true; render();
  try {
    actionError = '';
    await api('arm', {});
    // A blur/cancel while the request was in flight cannot re-enable controls.
    if (generation !== safetyGeneration || document.hidden || !$('confirm').checked) {await cancel(); return;}
    safetyBlocked = false;
    await poll();
    if (!safetyBlocked && state?.armed && state.owner === client) {
      clearTimeout(toastTimer); $('toast').hidden = true;
      $('command-preview').textContent = movementMode === 'hold' ? 'Segure uma direção ou arraste a alavanca. Solte para parar.' : 'Clique em uma direção para enviar a distância escolhida.';
    }
  } catch (error) {notify(error.message, true);}
  finally {arming = false; render();}
});
async function changeMode(next) {
  if (modeChanging || movementMode === next) return;
  modeChanging = true;
  endPress();
  $('confirm').checked = false;
  render();
  try {
    if (state?.connected && !(await cancel())) {
      notify('Modo mantido: confirme o bloqueio antes de trocar os controles.', true);
      return;
    }
    movementMode = next;
    document.body.classList.toggle('hold-mode', next === 'hold');
    $('mode-step').classList.toggle('selected', next === 'step');
    $('mode-hold').classList.toggle('selected', next === 'hold');
    $('mode-step').setAttribute('aria-pressed', String(next === 'step'));
    $('mode-hold').setAttribute('aria-pressed', String(next === 'hold'));
    $('hold-guidance').hidden = $('mouse-control').hidden = next !== 'hold';
    $('mode-description').textContent = next === 'hold' ? 'Mova enquanto segura. Soltar solicita a parada.' : 'Um clique envia apenas a distância escolhida.';
    document.querySelector('.pad-hint').textContent = next === 'hold' ? 'Segure para mover. Solte para parar. Mouse, toque, Espaço ou Enter.' : 'Um clique. Um deslocamento. Não é necessário segurar.';
    document.querySelector('.pill').textContent = next === 'hold' ? 'Enquanto segura' : 'Incremental';
    document.querySelector('.setting-note').textContent = next === 'hold' ? 'Confira primeiro o sentido físico em “Por clique”, com 0,1 mm.' : '↗ Comece com 0,1 mm para conferir o sentido físico dos eixos.';
    $('command-preview').textContent = 'Modo alterado. Confira a área livre e habilite os controles novamente.';
  } finally {modeChanging = false; render();}
}
$('mode-step').addEventListener('click', () => changeMode('step'));
$('mode-hold').addEventListener('click', () => changeMode('hold'));
for (const name of ['xy', 'z']) {
  const feed = $(name + '-feed'), slider = $(name + '-slider');
  const speed = () => $(name + '-speed').textContent = feed.validity.valid && feed.value ? `${(Number(feed.value) / 60).toLocaleString('pt-BR', {maximumFractionDigits:1})} mm/s` : 'Revise o valor';
  feed.addEventListener('input', () => {if (feed.validity.valid) slider.value = feed.value; speed();});
  slider.addEventListener('input', () => {feed.value = slider.value; speed();});
  $(name + '-steps').querySelectorAll('button').forEach(button => {
    button.setAttribute('aria-pressed', button.classList.contains('selected') ? 'true' : 'false');
    button.addEventListener('click', () => {
      const value = Number(button.dataset.value);
      if (name === 'xy') xyStep = value; else zStep = value;
      $(name + '-steps').querySelectorAll('button').forEach(other => {
        other.classList.toggle('selected', other === button);
        other.setAttribute('aria-pressed', other === button ? 'true' : 'false');
      });
    });
  });
}
document.querySelectorAll('.jog').forEach(button => {
  const parameters = () => ({axis: button.dataset.axis,
    distance: (button.dataset.axis === 'Z' ? zStep : xyStep) * Number(button.dataset.dir),
    feed: Number($(button.dataset.axis === 'Z' ? 'z-feed' : 'xy-feed').value)});
  const preview = () => {
    const p = parameters();
    if (activePress) return;
    $('command-preview').textContent = movementMode === 'hold' ? `Segure: ${p.axis}${p.distance > 0 ? '+' : '−'} · ${p.feed} mm/min · solte para parar` : `Próximo clique: ${p.axis}${p.distance > 0 ? '+' : '−'} · ${Math.abs(p.distance).toLocaleString('pt-BR')} mm · ${p.feed} mm/min`;
  };
  button.addEventListener('pointerenter', preview); button.addEventListener('focus', preview);
  button.addEventListener('pointerdown', event => {
    if (movementMode !== 'hold' || event.button !== 0 || !event.isPrimary) return;
    event.preventDefault();
    const field = $(button.dataset.axis === 'Z' ? 'z-feed' : 'xy-feed');
    if (!field.value || !field.reportValidity()) return;
    beginPress(button, {pointerId: event.pointerId}, {axis: button.dataset.axis, direction: Number(button.dataset.dir)});
  });
  button.addEventListener('keydown', event => {
    if (movementMode !== 'hold' || ![' ', 'Enter'].includes(event.key)) return;
    event.preventDefault();
    if (event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    beginPress(button, {key: event.key}, {axis: button.dataset.axis, direction: Number(button.dataset.dir)});
  });
  button.addEventListener('blur', () => {if (activePress?.source === button && activePress.key) endPress();});
  button.addEventListener('lostpointercapture', event => {if (activePress?.source === button && activePress.pointerId === event.pointerId) endPress();});
  button.addEventListener('contextmenu', event => {if (movementMode === 'hold') event.preventDefault();});
  button.addEventListener('click', async () => {
    if (movementMode !== 'step' || !controlsReady()) return;
    const field = $(button.dataset.axis === 'Z' ? 'z-feed' : 'xy-feed');
    if (!field.value || !field.reportValidity()) return;
    localBusy = true; actionError = ''; render();
    try {
      const result = await api('jog', {...parameters(), epoch: state.control_epoch});
      $('command-preview').textContent = 'Enviado: ' + result.command;
    } catch (error) {notify(error.message, true);}
    finally {localBusy = false; await poll(); render();}
  });
});
const joystick = $('joystick');
joystick.addEventListener('pointerdown', event => {
  if (event.button !== 0 || !event.isPrimary) return;
  event.preventDefault();
  beginPress(joystick, {pointerId: event.pointerId});
});
joystick.addEventListener('pointermove', event => {
  const press = activePress;
  if (!press || press.source !== joystick || press.pointerId !== event.pointerId || press.latched || press.released) return;
  if (!(event.buttons & 1)) {endPress(); return;}
  const rect = joystick.getBoundingClientRect();
  const dx = event.clientX - rect.left - rect.width / 2;
  const dy = event.clientY - rect.top - rect.height / 2;
  const radius = rect.width / 2 - 32;
  const distance = Math.hypot(dx, dy), deadzone = radius * 0.27;
  const factor = distance > radius ? radius / distance : 1;
  $('joystick-knob').style.transform = `translate(${dx * factor}px, ${dy * factor}px)`;
  if (distance <= deadzone) {
    if (press.startSent) latchPress('Centro: parada solicitada. Solte antes de recomeçar.');
    else $('joystick-readout').textContent = 'Centro · sem movimento';
    return;
  }
  // Nearest cardinal direction only: never issue diagonal or simultaneous axes.
  const axis = Math.abs(dx) >= Math.abs(dy) ? 'X' : 'Y';
  press.intent = {axis, direction: axis === 'X' ? (dx >= 0 ? 1 : -1) : (dy <= 0 ? 1 : -1),
    ratio: Math.min(1, (distance - deadzone) / (radius - deadzone))};
  const feed = Math.max(60, Math.round(Number($('xy-feed').value) * press.intent.ratio));
  $('joystick-readout').textContent = `${axis}${press.intent.direction > 0 ? '+' : '−'} · até ${feed} mm/min`;
  if (!press.startSent) sendHold(press);
  showHold();
});
joystick.addEventListener('lostpointercapture', event => {if (activePress?.source === joystick && activePress.pointerId === event.pointerId) endPress();});
joystick.addEventListener('contextmenu', event => event.preventDefault());
document.addEventListener('pointerup', event => {if (activePress?.pointerId === event.pointerId) endPress();}, true);
document.addEventListener('pointercancel', event => {if (activePress?.pointerId === event.pointerId) endPress();}, true);
document.addEventListener('pointermove', event => {if (activePress?.pointerId === event.pointerId && !(event.buttons & 1)) endPress();}, true);
document.addEventListener('keyup', event => {
  if (activePress?.key === event.key) {event.preventDefault(); endPress();}
}, true);
setInterval(() => {
  const press = activePress;
  if (press?.startSent && press.startAcknowledgedAt && !press.requestBusy && !press.latched && !press.released) sendHold(press);
}, 150);
$('export').addEventListener('click', () => {
  const copy = {...state, captured_at: new Date().toISOString(), note: 'Posição calculada; movimento físico e calibração não verificados.'};
  delete copy.owner;
  delete copy.control_epoch;
  const url = URL.createObjectURL(new Blob([JSON.stringify(copy, null, 2)], {type:'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `cnc-diagnostico-${new Date().toISOString().slice(0,10)}${state?.simulation ? '-SIMULADO' : ''}.json`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
function leave() {
  safetyBlocked = true; safetyGeneration++;
  $('confirm').checked = false;
  const press = activePress;
  if (press) {
    press.released = true;
    clearTimeout(press.deadline);
    activePress = null;
    if (press.startSent && !press.stopSent) {
      press.stopSent = true;
      navigator.sendBeacon('/api/hold/stop', new Blob([JSON.stringify({...holdBody(press, false), token, client})], {type:'application/json'}));
    }
  }
  if (state?.armed && state.owner === client) {
    navigator.sendBeacon('/api/cancel', new Blob([JSON.stringify({token, client})], {type:'application/json'}));
    state.armed = false; $('confirm').checked = false; render();
  }
  render();
}
document.addEventListener('visibilitychange', () => {if (document.hidden) leave(); else poll();});
window.addEventListener('pagehide', leave);
window.addEventListener('blur', leave);
setInterval(async () => {
  if (heartbeatBusy || safetyBlocked || document.hidden || !state?.armed || state.owner !== client) return;
  heartbeatBusy = true;
  try {await api('heartbeat', {epoch: state.control_epoch}, 1000);} catch (_) {leave();}
  finally {heartbeatBusy = false;}
}, 450);
setInterval(poll, 350);
refreshPorts().then(poll).catch(error => {notify(error.message, true); $('notice').textContent = error.message;});
