const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/jarvisVoice.js'), 'utf8');
const documentSource = fs.readFileSync(path.join(__dirname, '../static/js/document.js'), 'utf8');
const rendererSource = fs.readFileSync(path.join(__dirname, '../static/js/chatRenderer.js'), 'utf8');
const sessionsSource = fs.readFileSync(path.join(__dirname, '../static/js/sessions.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const style = fs.readFileSync(path.join(__dirname, '../static/style.css'), 'utf8');
const serviceWorker = fs.readFileSync(path.join(__dirname, '../static/sw.js'), 'utf8');

assert.match(source, /turns\/\$\{encodeURIComponent\(turnId\)\}\/audio/);
assert.match(source, /playPcmAudioStream/);
assert.match(source, /response\.body\.getReader\(\)/);
assert.match(source, /pcm16FromBase64/);
assert.match(source, /context\.createBuffer\(1, samples\.length, sampleRate\)/);
assert.match(source, /source\.start\(beginsAt\)/);
assert.match(source, /playbackScheduledUntil = beginsAt \+ audioBuffer\.duration/);
assert.match(source, /let lastSourceEnded = Promise\.resolve\(\)/);
assert.match(source, /source\.onended = finish/);
assert.match(source, /await lastSourceEnded/);
assert.match(source, /playBufferedAudio/);
assert.match(source, /response\.arrayBuffer\(\)/);
assert.match(source, /context\.decodeAudioData/);
assert.match(source, /timings\.tts_chunks \+= 1/);
assert.match(source, /timings\.tts_blocks = Math\.max/);
assert.match(source, /timings\.scheduler_underruns = 0/);
assert.match(source, /playBufferedAudio\('\/api\/tts\/synthesize'/);
assert.doesNotMatch(source, /STREAM_EDGE_CROSSFADE_SECONDS|STREAM_INITIAL_BUFFER_SECONDS|\/api\/tts\/stream/);
const pcmStreamBody = source.match(/async function playPcmAudioStream\([\s\S]*?\n\}/)?.[0] || '';
assert.doesNotMatch(pcmStreamBody, /createGain|linearRampToValueAtTime|setTimeout/);
assert.match(source, /SPOKEN_WORKER_EVENTS = new Set\(\['progress', 'question', 'approval_required', 'result', 'error'\]\)/);
assert.match(source, /DURABLE_SPEECH_TYPES = new Set\(\['question', 'approval_required', 'error'\]\)/);
assert.match(source, /event\.spoken_text \|\| `\$\{label\} finished\. The full result is in chat\.`/);
assert.doesNotMatch(source, /enqueueSpeech\(event\.text/);
assert.match(source, /WORKER_SPEECH_MAX_CHARS = 700/);
assert.match(source, /VOICE_RMS_THRESHOLD = 0\.018/);
assert.match(source, /VOICE_SAMPLE_INTERVAL_MS = 140/);
assert.match(source, /MIN_VOICED_MS = 280/);
assert.match(source, /let cueAudioContext = null/);
assert.match(source, /playVoiceCue\('call'\)/);
assert.match(source, /playVoiceCue\('heard'\)/);
assert.match(source, /playVoiceCue\('thinking', 0\.1\)/);
assert.match(source, /closeVoiceCueAudio\(\)/);
assert.match(source, /captureVoicedMs \+= VOICE_SAMPLE_INTERVAL_MS/);
assert.match(source, /captureVoicedMs < MIN_VOICED_MS/);
assert.match(source, /echoCancellation: true/);
assert.match(source, /noiseSuppression: true/);
assert.match(source, /autoGainControl: true/);
assert.match(source, /channelCount: 1/);
assert.doesNotMatch(source, /startDirectWorkerTask|pendingWorkerText|requestsJarvisTarget/);
assert.match(source, /event\.type !== 'progress' \|\| Boolean\(event\.spoken_text\)/);
assert.match(source, /let voiceCallGeneration = 0/);
assert.match(source, /return isActive && callGeneration === voiceCallGeneration/);
assert.match(source, /const callGeneration = \+\+voiceCallGeneration/);
assert.match(source, /voiceCallGeneration \+= 1;\s*isActive = false/);
assert.match(source, /const endingSessionId = sessionId/);
assert.match(source, /sessions\/\$\{encodeURIComponent\(endingSessionId\)\}\/interrupt/);
assert.match(source, /if \(!isCurrentVoiceCall\(callGeneration\)\) \{\s*turnAudioPromise = Promise\.resolve\(\)/);
assert.match(source, /if \(!turnAudioPromise && !isCurrentVoiceCall\(callGeneration\)\) turnAudioPromise = Promise\.resolve\(\)/);
assert.match(source, /const text = await transcribe\(blob\);[\s\S]*?if \(!isCurrentVoiceCall\(callGeneration\)\) return;/);
assert.match(source, /requestedStream\.getTracks\(\)\.forEach\(track => track\.stop\(\)\)/);
assert.doesNotMatch(source, /let audioChunks = \[\]/);
assert.match(source, /const recordingChunks = \[\]/);
assert.match(source, /recordingChunks\.push\(event\.data\)/);
assert.match(source, /new Blob\(recordingChunks/);
assert.match(source, /streamTurn\(text, timings, turnStarted, callGeneration\)/);
assert.match(source, /playVoiceTurnAudio\(event\.turn_id, timings, turnSessionId\)/);
assert.match(source, /followWorkerTask\(event\.task_id, currentCall\)/);
assert.match(source, /taskId === activeWorkerTaskId[\s\S]*?task\?\.session_id === chatSessionId/);
assert.match(source, /event\.metadata\?\.codex_thread_id && eventBelongsToActiveVoiceTask/);
assert.match(source, /'X-Tz-Offset': String\(-new Date\(\)\.getTimezoneOffset\(\)\)/);
assert.match(source, /'X-Tz-Name': name/);
assert.equal((source.match(/browserTimezoneHeaders\(\)/g) || []).length, 4);
assert.match(source, /postPlaybackState\(turnId, 'failed', timings, voiceSessionId\)/);
assert.match(style, /\.jarvis-call-panel\[data-state="failed"\] \.jarvis-call-copy/);
assert.match(source, /jarvis-agent-chip/);
assert.match(source, /openWorkerArtifact/);
assert.match(source, /codex:\/\/threads\//);
assert.match(source, /approval_required/);
assert.match(source, /if \(chatSessionId\) await openLinkedChatSession\(chatSessionId, callGeneration\)/);
assert.match(source, /document\.createElement\('details'\)/);
assert.match(source, /group\.className = 'jarvis-task-activity'/);
assert.match(source, /document\.querySelectorAll\('\.jarvis-task-activity\[data-task-id\]'\)/);
assert.match(source, /if \(group\.parentElement !== rail\) rail\.appendChild\(group\)/);
assert.match(source, /if \(TERMINAL_TASK_STATES\.has\(task\.status\)\) positionActivityGroup\(group\)/);
assert.match(source, /return `Working for \$\{elapsed\}`/);
assert.match(source, /return `Worked for \$\{elapsed\}`/);
assert.match(source, /group\.open = false/);
assert.match(source, /group\.open = true/);
assert.match(source, /last\?\.dataset\.eventType === 'tool_activity'/);
assert.match(source, /row\._labels\.map/);
assert.match(source, /handledWorkerEventIds\.has\(eventId\)/);
assert.match(source, /handledWorkerEventIds\.add\(String\(event\.event_id\)\)/);
assert.match(source, /new EventSource\(`\/api\/agent-tasks\/\$\{encodeURIComponent\(taskId\)\}\/events`\)/);
assert.match(source, /stream\.onerror = refreshAgentControl/);
assert.match(source, /let workerEventChains = new Map\(\)/);
assert.match(source, /\.then\(\(\) => handleWorkerEvent\(event\)\)/);
assert.match(source, /queueWorkerEvent\(event\)/);
assert.doesNotMatch(source, /events\?after=|Could not refresh worker result in chat/);
assert.match(source, /fetchJson\(`\/api\/agent-tasks\/\$\{encodeURIComponent\(taskId\)\}`\)/);
assert.match(source, /snapshot\.session_id !== sessionIdToRestore/);
assert.match(source, /TERMINAL_TASK_STATES\.has\(task\.status \|\| ''\)/);
assert.match(source, /events\.forEach\(event => \{\s*renderActivityEvent\(event\);\s*renderWorkerSummary\(event, task\);\s*if \(event\.event_id\) handledWorkerEventIds\.add/);
assert.match(source, /taskMessageElements\(taskId\)\.find\(item => item\.dataset\.source === 'agent_worker'\)/);
assert.match(source, /item\.dataset\.source === 'jarvis_worker_summary'/);
assert.match(source, /const isResultSummary = event\.type === 'result'/);
assert.match(source, /metadata\.progress_summary === true \|\| metadata\.milestone === true/);
assert.match(source, /source: 'jarvis_worker_summary'/);
assert.match(source, /character_name: 'Jarvis'/);
assert.match(source, /summary\.dataset\.workerEventId = eventId/);
assert.match(source, /if \(eventId\) return item\.dataset\.workerEventId === eventId/);
assert.match(source, /if \(afterResult\)/);
assert.match(source, /result\.after\(summary\)/);
assert.match(source, /result\.before\(summary\)/);
assert.match(source, /if \(activeWorkerTaskId === taskId\)/);
assert.match(source, /querySelectorAll\('\.jarvis-task-approval-actions button'\)/);
assert.doesNotMatch(source, /history\.setAttribute\('role', 'log'\)/);
assert.doesNotMatch(source, /history\.setAttribute\('aria-live', 'polite'\)/);
assert.match(source, /window\.chatModule\?\.addMessage\?\.\('assistant', event\.text, '', \{/);
assert.match(source, /character_name: WORKER_LABELS\[event\.worker\] \|\| event\.worker \|\| 'Worker'/);
assert.match(source, /full result is in chat/i);
assert.match(source, /END_VOICE_LABEL = 'End voice — task continues'/);
assert.match(source, /window\.confirm\('Cancel the active task\?'\)/);
assert.match(source, /agent-tasks\/\$\{encodeURIComponent\(taskId\)\}\/cancel/);
assert.match(source, /Voice ended\./);
const endCallBody = source.match(/function endCall\(\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.doesNotMatch(endCallBody, /workerStreams\.forEach|workerStreams\.clear|handledWorkerEventIds = new Set/);
assert.match(source, /isActive = false;\s*restoreActivityGroupsToChat\(\)/);
assert.match(source, /if \(!continuedTasks\) chatSessionId = null/);
assert.match(source, /loadDocument\(documentId, \{ side: 'left' \}\)/);
assert.match(documentSource, /export async function loadDocument\(docId, options = \{\}\)/);
assert.match(documentSource, /pane\.classList\.contains\('doc-left'\) === \(side === 'left'\)\) return/);
assert.match(documentSource, /if \(divider\.dataset\.dragBound === '1'\) return/);
assert.match(style, /body\.jarvis-voice-active \.chat-container \{\s*margin-right: 34vw/);
assert.match(style, /body\.jarvis-voice-active \.msg table \{\s*max-width: none/);
assert.match(style, /\.jarvis-call-panel \{[\s\S]*?right: 0;[\s\S]*?width: 34vw;[\s\S]*?background: transparent/);
assert.match(style, /\.jarvis-activity-rail \{[\s\S]*?top: min\(34vw, 52dvh\)/);
assert.match(style, /\.jarvis-task-activity > summary/);
assert.match(style, /\.jarvis-task-activity-history/);
assert.match(style, /\.jarvis-task-tool-row/);
assert.doesNotMatch(style, /\.jarvis-call-panel\.has-agent-task \.jarvis-task-timeline/);
const activityRailZ = Number(style.match(/\.jarvis-activity-rail\s*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
const callActionsZ = Number(style.match(/\.jarvis-call-actions\s*\{[^}]*z-index:\s*(\d+)/s)?.[1]);
assert.ok(callActionsZ > activityRailZ, 'voice controls must remain above the active-task rail hitbox');
assert.match(rendererSource, /wrap\.dataset\.source = String\(metadata\.source\)/);
assert.match(rendererSource, /wrap\.dataset\.taskId = String\(metadata\.task_id\)/);
assert.match(rendererSource, /wrap\.dataset\.worker = String\(metadata\.worker\)/);
assert.match(rendererSource, /wrap\.dataset\.workerEventId = String\(metadata\.worker_event_id\)/);
assert.match(sessionsSource, /new CustomEvent\('odysseus:session-rendered'/);
assert.ok((sessionsSource.match(/_notifySessionRendered\(/g) || []).length >= 4);
assert.doesNotMatch(index, /jarvis-task-timeline/);
assert.match(index, /id="jarvis-activity-rail"[^>]*role="region"[^>]*aria-label="Live worker activity"/);
assert.match(index, /id="jarvis-agent-cancel"[^>]*hidden disabled/);
assert.match(index, /title="End voice — task continues" aria-label="End voice — task continues"/);
assert.match(index, /jarvisVoice\.js\?v=20260714T084500Z/);
assert.match(serviceWorker, /CACHE_NAME = 'odysseus-v356'/);
assert.doesNotMatch(documentSource, /_ensureAgentMode/);
assert.match(source, /return Promise\.allSettled\(jobs\)/);
assert.match(source, /Promise\.resolve\(\)\.then\(\(\) => window\.aiTTSManager\.checkAvailability\(\)\)/);
const startCallSource = source.match(/async function startCall\(\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.ok(startCallSource.indexOf('const voiceWarmup = prewarmVoiceStack()') < startCallSource.indexOf('await createSession(callGeneration)'));
assert.ok(startCallSource.indexOf('await voiceWarmup') < startCallSource.indexOf('await startListening()'));

// Exercise the production placement functions with a tiny dependency-free DOM.
// The same details node must keep its rail order, then return to chat before its final result.
const chatOrder = [];
const railOrder = [];
const moveNode = (node, parent, order) => {
  const priorOrder = node.parentElement === rail ? railOrder : chatOrder;
  const priorIndex = priorOrder.indexOf(node);
  if (priorIndex >= 0) priorOrder.splice(priorIndex, 1);
  node.parentElement = parent;
  order.push(node);
};
const chat = {
  appendChild(node) { moveNode(node, chat, chatOrder); },
  get children() { return chatOrder; },
};
const rail = {
  appendChild(node) { moveNode(node, rail, railOrder); },
  querySelectorAll() { return [...railOrder]; },
};
const acknowledgement = {
  classList: { contains: value => value === 'msg-ai' },
  dataset: { source: 'jarvis_voice', taskId: 'task-rail' },
  after(node) {
    const priorOrder = node.parentElement === rail ? railOrder : chatOrder;
    const priorIndex = priorOrder.indexOf(node);
    if (priorIndex >= 0) priorOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(chatOrder.indexOf(acknowledgement) + 1, 0, node);
  },
};
const result = {
  dataset: { source: 'agent_worker', taskId: 'task-rail' },
  after(node) {
    const priorIndex = chatOrder.indexOf(node);
    if (priorIndex >= 0) chatOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(chatOrder.indexOf(result) + 1, 0, node);
  },
  before(node) {
    const priorIndex = chatOrder.indexOf(node);
    if (priorIndex >= 0) chatOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(chatOrder.indexOf(result), 0, node);
  },
};
const summary = {
  dataset: { source: 'jarvis_worker_summary', taskId: 'task-rail', workerEventId: 'summary-1' },
  querySelector(selector) {
    return selector === '.body' ? { textContent: 'PC Codex verified the same result.' } : null;
  },
  after(node) {
    const index = chatOrder.indexOf(summary);
    const priorIndex = chatOrder.indexOf(node);
    if (priorIndex >= 0) chatOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(index + 1, 0, node);
  },
  before(node) {
    const index = chatOrder.indexOf(summary);
    const priorIndex = chatOrder.indexOf(node);
    if (priorIndex >= 0) chatOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(index, 0, node);
  },
};
const unrelated = { dataset: { source: 'jarvis_voice', taskId: 'other-task' } };
const group = {
  dataset: { taskId: 'task-rail', status: 'running' },
  parentElement: null,
  after(node) {
    const index = chatOrder.indexOf(group);
    const priorIndex = chatOrder.indexOf(node);
    if (priorIndex >= 0) chatOrder.splice(priorIndex, 1);
    node.parentElement = chat;
    chatOrder.splice(index + 1, 0, node);
  },
};
chatOrder.push(acknowledgement, summary, unrelated, result);
acknowledgement.parentElement = chat;
summary.parentElement = chat;
unrelated.parentElement = chat;
result.parentElement = chat;
const fakeDocument = {
  readyState: 'loading',
  documentElement: { dataset: {} },
  body: { classList: { toggle() {} } },
  addEventListener() {},
  getElementById(id) { return id === 'chat-history' ? chat : (id === 'jarvis-activity-rail' ? rail : null); },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector === '.jarvis-task-activity[data-task-id]') return [group];
    if (selector === '#chat-history .msg[data-task-id]') return [acknowledgement, summary, result];
    return [];
  },
};
const sandbox = {
  console,
  document: fakeDocument,
  navigator: {},
  performance: { now: () => 0 },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  fetch: () => Promise.resolve({ ok: true }),
  aiTTSManager: { checkAvailability() { throw new Error('availability probe failed'); } },
};
sandbox.window = sandbox;
const executableSource = source.replace(
  "import markdownModule from './markdown.js';",
  'const markdownModule = { renderMarkdown: value => value };',
) + '\n;globalThis.__activityPlacement = { positionActivityGroup, positionWorkerResult, positionWorkerSummary, restoreActivityGroupsToChat, findWorkerSummary, prewarmVoiceStack, setActive: value => { isActive = value; } };';
vm.runInNewContext(executableSource, sandbox);
const placement = sandbox.__activityPlacement;
assert.equal(placement.findWorkerSummary('task-rail', 'summary-1', 'PC Codex verified the same result.'), summary);
assert.equal(placement.findWorkerSummary('task-rail', 'summary-2', 'PC Codex verified the same result.'), undefined);
assert.equal(placement.findWorkerSummary('task-rail', '', 'PC Codex verified the same result.'), summary);
chatOrder.splice(0, chatOrder.length, acknowledgement, unrelated, result, summary);
placement.positionWorkerSummary(summary, 'task-rail');
assert.deepEqual(chatOrder, [acknowledgement, unrelated, summary, result], 'a restored summary must precede the final worker result');
chatOrder.splice(0, chatOrder.length, acknowledgement, summary, unrelated, result);
placement.positionWorkerSummary(summary, 'task-rail');
assert.deepEqual(chatOrder, [acknowledgement, summary, unrelated, result], 'an already ordered summary must keep transcript chronology');
placement.positionWorkerSummary(summary, 'task-rail', true);
assert.deepEqual(chatOrder, [acknowledgement, unrelated, result, summary], 'a terminal Jarvis brief must follow the full worker result');
chatOrder.splice(0, chatOrder.length, acknowledgement, summary, unrelated, result);
placement.setActive(true);
placement.positionActivityGroup(group);
assert.equal(group.parentElement, rail);
assert.deepEqual(railOrder, [group]);
placement.positionActivityGroup(group);
assert.deepEqual(railOrder, [group], 'new activity must not reorder the same rail node');
group.dataset.status = 'completed';
placement.positionActivityGroup(group);
placement.positionWorkerResult(result, group.dataset.taskId);
assert.deepEqual(chatOrder, [acknowledgement, group, summary, unrelated, result]);
group.dataset.status = 'running';
placement.positionActivityGroup(group);
placement.setActive(false);
placement.restoreActivityGroupsToChat();
assert.deepEqual(chatOrder, [acknowledgement, group, summary, unrelated, result]);
placement.prewarmVoiceStack().then(results => {
  assert.equal(results.length, 2);
  assert.equal(results[0].status, 'fulfilled');
  assert.equal(results[1].status, 'rejected');
}).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
