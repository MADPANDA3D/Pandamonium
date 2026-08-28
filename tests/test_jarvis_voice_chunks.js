const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/jarvisVoice.js'), 'utf8');
const appSource = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const documentSource = fs.readFileSync(path.join(__dirname, '../static/js/document.js'), 'utf8');
const rendererSource = fs.readFileSync(path.join(__dirname, '../static/js/chatRenderer.js'), 'utf8');
const sessionsSource = fs.readFileSync(path.join(__dirname, '../static/js/sessions.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
const style = fs.readFileSync(path.join(__dirname, '../static/style.css'), 'utf8');
const serviceWorker = fs.readFileSync(path.join(__dirname, '../static/sw.js'), 'utf8');
const workerAdaptersSource = fs.readFileSync(path.join(__dirname, '../src/agent_worker_adapters.py'), 'utf8');

assert.match(index, /id="oracle-protocol-panel"[\s\S]*?id="oracle-protocol-frame"/);
assert.match(style, /\.oracle-protocol-panel[\s\S]*?inset: 0;[\s\S]*?z-index: 10001;[\s\S]*?transition: opacity 200ms ease, transform 200ms ease/);
assert.match(source, /VOICE_PROTOCOL_CONTROL_ALLOWLIST = new Set\(\[[\s\S]*?'oracle_protocol_engage'[\s\S]*?'oracle_protocol_shutdown'[\s\S]*?'oracle_protocol_command'/);
assert.match(source, /function applyOracleProtocolControl\(event\)[\s\S]*?sendOracleProtocolCommand\(String\(event\.tool/);
assert.match(source, /function handleOracleProtocolMessage\(event\)[\s\S]*?event\.origin !== oracleProtocolOrigin\(\)/);
assert.match(source, /function voiceRequestPayload\(text\)[\s\S]*?clientState\.oracle = oracle/);
assert.match(source, /fetchJson\('\/api\/voice\/oracle-config'\)/);
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
assert.match(source, /is requesting approval\. Please take a look\./);
assert.match(source, /has a question\. Please take a look\./);
assert.match(source, /hit a problem\. Please take a look\./);
assert.match(source, /WORKER_SPEECH_MAX_CHARS = 700/);
assert.match(source, /VOICE_RMS_THRESHOLD = 0\.018/);
assert.match(source, /VOICE_SAMPLE_INTERVAL_MS = 140/);
assert.match(source, /MIN_VOICED_MS = 280/);
assert.doesNotMatch(source, /cueAudioContext|unlockVoiceCueAudio|closeVoiceCueAudio/);
assert.match(source, /let playbackAudioContext = null/);
assert.match(source, /const VOICE_CUE_GAIN = 0\.12/);
assert.match(source, /source\.connect\(playbackAnalyser\)/);
assert.match(source, /playVoiceCue\('call'\)/);
assert.match(source, /playVoiceCue\('heard'\)/);
assert.match(source, /await playVoiceCue\('thinking'\)/);
assert.match(source, /gain\.connect\(playbackAnalyser\)/);
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
const stopPlaybackBody = source.match(/function stopPlaybackAudio\(\) \{([\s\S]*?)\n\}/)?.[1] || '';
const stopTracksBody = source.match(/function stopTracks\(\) \{([\s\S]*?)\n\}/)?.[1] || '';
const requestMicrophoneBody = source.match(/async function requestMicrophone\([^)]*\) \{([\s\S]*?)\n\}/)?.[1] || '';
const recorderStopBody = source.match(/mediaRecorder\.onstop = async \(\) => \{([\s\S]*?)\n  \};/)?.[1] || '';
const setStatusBody = source.match(/function setStatus\([^)]*\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.doesNotMatch(endCallBody, /workerStreams\.forEach|workerStreams\.clear|handledWorkerEventIds = new Set/);
assert.doesNotMatch(endCallBody, /unmountOrganicSphere/);
assert.doesNotMatch(setStatusBody, /unmountOrganicSphere/);
assert.match(endCallBody, /deferCallPanelClose\(panel, closingGeneration\)/);
assert.match(endCallBody, /closePlaybackAudio\(\)/);
assert.match(endCallBody, /setAudioSessionType\('auto'\)/);
assert.match(stopTracksBody, /setAudioSessionType\(isActive \? 'playback' : 'auto'\)/);
assert.match(requestMicrophoneBody, /setAudioSessionType\('play-and-record'\)/);
assert.doesNotMatch(stopPlaybackBody, /closePlaybackAudio|playbackAudioContext\.close/);
assert.ok(
  recorderStopBody.indexOf("await playVoiceCue('heard')") >= 0
    && recorderStopBody.indexOf("await playVoiceCue('heard')") < recorderStopBody.indexOf('await transcribe(blob)')
    && recorderStopBody.indexOf('await transcribe(blob)') < recorderStopBody.indexOf("await playVoiceCue('thinking')"),
  'capture and thinking cues must announce their actual turn boundaries',
);
assert.match(source, /isActive = false;\s*restoreActivityGroupsToChat\(\)/);
assert.match(source, /if \(!continuedTasks\) chatSessionId = null/);
assert.match(source, /loadDocument\(documentId, \{ side: 'left' \}\)/);
assert.match(documentSource, /export async function loadDocument\(docId, options = \{\}\)/);
assert.match(documentSource, /pane\.classList\.contains\('doc-left'\) === \(side === 'left'\)\) return/);
assert.match(documentSource, /if \(divider\.dataset\.dragBound === '1'\) return/);
assert.match(style, /body\.jarvis-voice-active \.chat-container \{\s*margin-right: 34vw/);
assert.match(style, /body\.jarvis-voice-active \.msg table \{\s*max-width: none/);
assert.match(style, /\.jarvis-call-panel \{[\s\S]*?right: 0;[\s\S]*?width: 34vw;[\s\S]*?background: transparent/);
assert.match(style, /\.jarvis-call-panel \{[\s\S]*?transform: translateX\(100%\);[\s\S]*?transition: transform 280ms/);
assert.match(style, /\.jarvis-call-panel\.is-open \{\s*transform: translateX\(0\)/);
const mobileStyle = style.match(/@media \(max-width: 720px\) \{([\s\S]*?)\n\}\n@media \(prefers-reduced-motion: reduce\)/)?.[1] || '';
assert.match(mobileStyle, /\.jarvis-agent-workspace,\s*\.jarvis-activity-rail \{\s*display: none/);
assert.match(mobileStyle, /width: min\(64vw, 42dvh, 280px\)/);
assert.match(mobileStyle, /\.jarvis-organic-frame \{[\s\S]*?mask-image: radial-gradient/);
assert.match(mobileStyle, /\.jarvis-call-talk,\s*\.jarvis-call-close \{[\s\S]*?min-width: 44px/);
assert.match(mobileStyle, /\.jarvis-call-panel\.is-minimized \{\s*transform: translateX\(100%\)/);
assert.match(mobileStyle, /\.jarvis-call-view-chat \{[\s\S]*?display: inline-flex/);
assert.match(style, /padding: calc\(env\(safe-area-inset-top, 0px\) \+ 44px\)/);
assert.match(style, /#pinned-tools-bar:empty \{ display: none; \}/);
assert.match(style, /\.chat-input-bar \{\s*padding: 8px 10px;\s*gap: 4px;/);
assert.match(style, /\.jarvis-input-sphere \{\s*width: 44px;\s*height: 44px;\s*flex-basis: 44px;/);
assert.match(style, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.jarvis-call-panel \{\s*transition: none;/);
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
assert.match(index, /id="jarvis-call-view-chat"[^>]*>View chat<\/button>/);
assert.match(index, /<button[^>]*data-worker="hermes"[^>]*>\s*<span>Gordon<\/span><small>Hermes laptop · gated<\/small>\s*<\/button>/);
assert.match(index, /<button[^>]*data-worker="pc-codex"[^>]*>\s*<span>Friday<\/span><small>Local workstation · checking<\/small>\s*<\/button>/);
assert.match(index, /style\.css\?v=20260722T162202Z/);
assert.match(index, /sessions\.js\?v=20260719T024058Z/);
assert.match(index, /jarvisVoice\.js\?v=20260828T020000Z/);
assert.match(index, /app\.js\?v=20260719T024058Z/);
assert.match(appSource, /sessions\.js\?v=20260719T024058Z/);
assert.match(serviceWorker, /CACHE_NAME = 'odysseus-v366'/);
assert.match(index, /id="hamburger-btn"[^>]*aria-label="Toggle sidebar"[^>]*aria-controls="sidebar"/);
assert.match(serviceWorker, /\/static\/js\/voiceOrbMedia\.js/);
assert.match(serviceWorker, /\/static\/voice-orb-media\.json/);
assert.doesNotMatch(serviceWorker, /motivational-abstract\.webm/);
assert.doesNotMatch(documentSource, /_ensureAgentMode/);
assert.match(source, /VOICE_PREWARM_TIMEOUT_MS = 2500/);
assert.match(source, /return Promise\.allSettled\(jobs\)\.then/);
assert.doesNotMatch(source, /client_tts_probe|manager\.play\(text\)|speechSynthesis\.speak/);
const queuedSpeechBody = source.match(/async function speak\([^)]*\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.match(queuedSpeechBody, /playBufferedAudio\('\/api\/tts\/synthesize'/);
assert.doesNotMatch(queuedSpeechBody, /aiTTSManager|useBrowserTTS|\.play\(/);
const startCallSource = source.match(/async function startCall\(\) \{([\s\S]*?)\n\}/)?.[1] || '';
const streamTurnSource = source.match(/async function streamTurn\([^)]*\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.match(source, /const VOICE_TARGET_LABELS = \{ \.\.\.WORKER_LABELS, hermes: 'Gordon', friday: 'Friday' \}/);
assert.match(source, /'pc-codex': 'Friday'/);
assert.match(source, /jarvis: \{ enabled: true, machine: 'Self-hosted'/);
assert.match(source, /event\.type === 'assistant_handoff'/);
assert.match(source, /const previousAudio = turnAudioPromise \|\| Promise\.resolve\(\)/);
assert.match(source, /includes\('chatgpt\.com\/backend-api\/codex'\)/);
assert.match(sessionsSource, /if \(_pendingChat && _pendingChat\.modelId\) return _pendingChat\.modelId/);
assert.match(source, /document\.querySelectorAll\('\.jarvis-call-name'\)/);
assert.match(source, /label\.textContent = VOICE_TARGET_LABELS\[worker\] \|\| worker/);
assert.match(source, /const queuedUpdate = targetUpdatePromise\s*\.catch\(\(\) => \{\}\)/);
assert.match(source, /persistVoiceTarget\(payload, voiceSessionId\)/);
assert.match(source, /if \(!voiceSessionId \|\| !voiceSessionReady\) return targetUpdatePromise/);
assert.match(workerAdaptersSource, /r"<think\(\?:ing\)\?>\[\\s\\S\]\*\?<\/think\(\?:ing\)\?>"/);
assert.ok(
  streamTurnSource.indexOf('await awaitVoiceTargetReady()') >= 0
    && streamTurnSource.indexOf('await awaitVoiceTargetReady()') < streamTurnSource.indexOf('/respond/stream'),
  'the selected voice target must persist before the response request starts',
);
const microphoneStart = startCallSource.indexOf('const microphoneReady = requestMicrophone(callGeneration)');
const playbackUnlock = startCallSource.indexOf('unlockPlaybackAudio()');
const sessionStart = startCallSource.indexOf('const sessionReady = createSession(callGeneration)');
const selectedModelTarget = startCallSource.indexOf('const selectedTarget = voiceTargetForModel(');
const readinessWait = startCallSource.indexOf('await Promise.all([microphoneReady, sessionReady])');
const readinessCue = startCallSource.indexOf("await playVoiceCue('call')");
const recorderStart = startCallSource.indexOf('await startListening(requestedStream, callGeneration)');
assert.ok(microphoneStart >= 0 && microphoneStart < readinessWait);
assert.ok(playbackUnlock >= 0 && playbackUnlock < microphoneStart, 'playback must unlock during the initiating tap');
assert.ok(sessionStart >= 0 && sessionStart < readinessWait);
assert.ok(selectedModelTarget >= 0 && selectedModelTarget < sessionStart);
assert.ok(startCallSource.indexOf('prewarmVoiceStack().catch') < readinessWait);
assert.ok(readinessWait < readinessCue && readinessCue < recorderStart);
assert.doesNotMatch(startCallSource, /await prewarmVoiceStack/);
assert.match(startCallSource, /catch \(error\) \{\s*if \(!isCurrentVoiceCall\(callGeneration\)\) return;\s*const failedSessionId = sessionId;\s*const invalidatedGeneration = \+\+voiceCallGeneration;[\s\S]*?await interruptVoiceSession\(failedSessionId\);\s*if \(sessionId === failedSessionId\) sessionId = null;[\s\S]*?if \(voiceCallGeneration !== invalidatedGeneration\) return;[\s\S]*?handleError\(error\)/);
const createSessionSource = source.match(/async function createSession\([^)]*\) \{([\s\S]*?)\n\}/)?.[1] || '';
assert.match(createSessionSource, /if \(!isCurrentVoiceCall\(callGeneration\)\) \{\s*await interruptVoiceSession\(session\.id\);\s*return null;\s*\}\s*sessionId = session\.id/);
assert.match(createSessionSource, /const selectionRevisionAtStart = targetSelectionRevision/);
assert.match(createSessionSource, /if \(pendingVoiceTargetState \|\| targetSelectionRevision !== selectionRevisionAtStart\)[\s\S]*?queueVoiceTargetUpdate[\s\S]*?await awaitVoiceTargetReady\(\)/);
assert.match(source, /pendingVoiceTargetState = \{\s*target: voiceTarget,\s*workspace: activeWorkspace/);
assert.match(source, /Could not confirm \$\{label\} as the voice target\. Your message was not sent\./);

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
const makeClassList = () => {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
    toggle(value, force) {
      const next = force === undefined ? !values.has(value) : Boolean(force);
      if (next) values.add(value);
      else values.delete(value);
      return next;
    },
  };
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
let orbUnmounts = 0;
let exposeFakeOrb = false;
const fakeOrb = { classList: { remove(value) { if (value === 'has-frame') orbUnmounts += 1; } } };
let exposeVoiceIdentity = false;
let exposeCallPanel = false;
const callPanel = {
  classList: makeClassList(),
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
  removeAttribute(name) { delete this.attributes[name]; },
};
const callName = { textContent: '' };
const callDetail = { textContent: '' };
const callTalk = {
  title: '',
  attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
};
const inputSphere = {
  title: '',
  attributes: {},
  focusOptions: null,
  focus(options) { this.focusOptions = options; },
  setAttribute(name, value) { this.attributes[name] = value; },
};
const fakeDocument = {
  readyState: 'loading',
  documentElement: { dataset: {}, classList: makeClassList() },
  body: { classList: makeClassList() },
  addEventListener() {},
  getElementById(id) {
    if (id === 'chat-history') return chat;
    if (id === 'jarvis-activity-rail') return rail;
    if (id === 'jarvis-call-orb' && exposeFakeOrb) return fakeOrb;
    if (id === 'jarvis-call-panel' && exposeCallPanel) return callPanel;
    if (exposeVoiceIdentity && id === 'jarvis-call-detail') return callDetail;
    if (exposeVoiceIdentity && id === 'jarvis-call-talk') return callTalk;
    if (exposeVoiceIdentity && id === 'jarvis-input-sphere') return inputSphere;
    return null;
  },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector === '.jarvis-task-activity[data-task-id]') return [group];
    if (selector === '#chat-history .msg[data-task-id]') return [acknowledgement, summary, result];
    if (selector === '.jarvis-call-name' && exposeVoiceIdentity) return [callName];
    return [];
  },
};
const sandboxConsole = {
  info() {},
  warn: (...args) => {
    if (args[0] === 'Could not save voice target:' && args[1]?.message?.includes('Your message was not sent.')) return;
    console.warn(...args);
  },
  error: (...args) => {
    if (args[0] === 'Jarvis voice error:' && args[1]?.message === 'microphone rejected') return;
    console.error(...args);
  },
};
let selectedModel = null;
const sandbox = {
  console: sandboxConsole,
  document: fakeDocument,
  navigator: {},
  performance: { now: () => 0 },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  fetch: () => Promise.resolve({ ok: true }),
  sessionModule: { getCurrentModel: () => selectedModel },
  aiTTSManager: { checkAvailability() { throw new Error('availability probe failed'); }, stop() {} },
};
sandbox.window = sandbox;
const executableSource = source
  .replace(
    "import markdownModule from './markdown.js';",
    'const markdownModule = { renderMarkdown: value => value };',
  )
  .replace(
    "import { collectClientState, handleUIControl } from './chatStream.js';",
    "const collectClientState = () => ({ active_view: 'chat' }); const handleUIControl = () => {};",
  )
  .replace(
    "import voiceOrbMedia from './voiceOrbMedia.js';",
    "let testCameraOpen = false; const voiceOrbMedia = { getState: () => ({ cameraOpen: testCameraOpen }), captureFrame: () => ({ captured: true }), openCamera: async () => ({}), closeCamera: () => ({}), playClip: async () => ({}), stopMedia: () => ({}) };",
  ) + '\n;globalThis.__activityPlacement = { positionActivityGroup, positionWorkerResult, positionWorkerSummary, restoreActivityGroupsToChat, findWorkerSummary, prewarmVoiceStack, mediaVoiceCommand, voiceRequestPayload, workerSpeech, workerApprovalAllowsOnce, rememberTask, requestMicrophone, deferCallPanelClose, startCall, endCall, sendTurn, streamTurn, awaitVoiceTargetReady, setVoiceTarget, setAudioSessionType, voiceTargetForModel, getVoiceTarget: () => voiceTarget, waitForTargetUpdate: () => targetUpdatePromise, getTargetSyncState: () => ({ voiceSessionReady, targetSelectionRevision, confirmedVoiceTargetState, pendingVoiceTargetState, targetUpdateFailure: targetUpdateFailure?.message || null }), enableWorker: worker => { workerCatalog[worker] = { ...(workerCatalog[worker] || {}), enabled: true, connection: { state: "connected" } }; }, getVoiceState: () => ({ sessionId, voiceCallGeneration, isActive, status }), setCameraOpen: value => { testCameraOpen = value; }, setActive: value => { isActive = value; }, setCallPanelMinimized, isCallPanelMinimized, unlockPlaybackAudio, stopPlaybackAudio, closePlaybackAudio, getPlaybackContext: () => playbackAudioContext };';
vm.runInNewContext(executableSource, sandbox);
const placement = sandbox.__activityPlacement;
assert.equal(placement.setAudioSessionType('playback'), false);
sandbox.navigator.audioSession = { type: 'auto' };
assert.equal(placement.setAudioSessionType('play-and-record'), true);
assert.equal(sandbox.navigator.audioSession.type, 'play-and-record');
assert.equal(placement.setAudioSessionType('playback'), true);
assert.equal(sandbox.navigator.audioSession.type, 'playback');
assert.equal(placement.setAudioSessionType('auto'), true);
assert.equal(sandbox.navigator.audioSession.type, 'auto');
delete sandbox.navigator.audioSession;
placement.enableWorker('hermes');
exposeCallPanel = true;
exposeVoiceIdentity = true;
placement.setActive(true);
placement.setCallPanelMinimized(true);
assert.equal(placement.isCallPanelMinimized(), true);
assert.equal(placement.getVoiceState().isActive, true, 'viewing chat must preserve the voice session');
assert.equal(fakeDocument.body.classList.contains('jarvis-voice-minimized'), true);
assert.equal(callPanel.inert, true);
assert.equal(callPanel.attributes['aria-hidden'], 'true');
assert.equal(inputSphere.focusOptions?.preventScroll, true, 'View chat moves focus outside the inert panel');
placement.setCallPanelMinimized(false);
assert.equal(placement.isCallPanelMinimized(), false);
assert.equal(callPanel.inert, false);
placement.setActive(false);
exposeCallPanel = false;
exposeVoiceIdentity = false;

let playbackCloses = 0;
sandbox.AudioContext = class FakePlaybackAudioContext {
  constructor() {
    this.state = 'suspended';
    this.sampleRate = 48000;
    this.destination = {};
  }
  createAnalyser() {
    return { fftSize: 0, frequencyBinCount: 8, connect() {}, disconnect() {}, getByteFrequencyData() {} };
  }
  createBuffer() { return {}; }
  createBufferSource() {
    return { connect() {}, disconnect() {}, start() { this.onended?.(); } };
  }
  resume() {
    this.state = 'running';
    return Promise.resolve();
  }
  close() {
    this.state = 'closed';
    playbackCloses += 1;
    return Promise.resolve();
  }
};
placement.unlockPlaybackAudio();
const unlockedPlaybackContext = placement.getPlaybackContext();
assert.equal(unlockedPlaybackContext.state, 'running');
placement.stopPlaybackAudio();
assert.equal(playbackCloses, 0, 'turn and orb cleanup must keep mobile playback unlocked');
placement.closePlaybackAudio();
assert.equal(playbackCloses, 1, 'ending voice closes the dedicated playback context');
delete sandbox.AudioContext;
assert.equal(placement.voiceTargetForModel('hermes-agent'), 'hermes');
assert.equal(placement.voiceTargetForModel('provider/hermes-agent'), 'hermes');
assert.equal(placement.voiceTargetForModel('qwen3.5-jarvis-v5:latest'), 'jarvis');
assert.equal(placement.voiceTargetForModel('qwen3.5:9b'), 'jarvis');
assert.equal(placement.voiceTargetForModel('gpt-5-codex', 'https://chatgpt.com/backend-api/codex'), 'friday');
assert.equal(placement.voiceTargetForModel('unknown-model'), 'jarvis');
exposeVoiceIdentity = true;
assert.equal(placement.setVoiceTarget('hermes', false), true);
assert.equal(callName.textContent, 'Gordon');
assert.equal(callDetail.textContent, 'Gordon is standing by.');
assert.equal(callTalk.title, 'Speak to Gordon');
assert.equal(callTalk.attributes['aria-label'], 'Speak to Gordon');
assert.equal(inputSphere.title, 'Gordon live call');
assert.equal(inputSphere.attributes['aria-label'], 'Gordon live call');
assert.equal(placement.setVoiceTarget('jarvis', false), true);
exposeVoiceIdentity = false;
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
[
  ['open your eyes', 'camera_open'],
  ['open eyes', 'camera_open'],
  ['open the camera', 'camera_open'],
  ['what do you see', 'camera_describe'],
  ['describe what you see', 'camera_describe'],
  ['describe the camera', 'camera_describe'],
  ['close your eyes', 'camera_close'],
  ['close eyes', 'camera_close'],
  ['close the camera', 'camera_close'],
  ['i need something motivational', 'media_motivation'],
  ['need something motivational', 'media_motivation'],
  ['i want something motivational', 'media_motivation'],
  ['want something motivational', 'media_motivation'],
  ['show me something motivational', 'media_motivation'],
  ['play something motivational', 'media_motivation'],
].forEach(([phrase, intent]) => assert.equal(placement.mediaVoiceCommand(phrase), intent, phrase));
[
  ['Hey Jarvis, open your eyes.', 'camera_open'],
  ['okay please Jarvis open eyes', 'camera_open'],
  ['can you please open the camera', 'camera_open'],
  ['could you describe the camera', 'camera_describe'],
  ['would you close eyes', 'camera_close'],
  ['will you play something motivational', 'media_motivation'],
  ['I want you to open your eyes', 'camera_open'],
  ['I need to close the camera', 'camera_close'],
  ['I would like you to describe what you see', 'camera_describe'],
  ['actually do me a favor and please open your eyes', 'camera_open'],
  ['do me favor show me something motivational', 'media_motivation'],
  ['actually close your eyes', 'camera_close'],
  ['go ahead and show me something motivational', 'media_motivation'],
  ['please play something motivational please', 'media_motivation'],
].forEach(([phrase, intent]) => assert.equal(placement.mediaVoiceCommand(phrase), intent, phrase));
[
  "don't open your eyes",
  'do not open your eyes',
  'never open your eyes',
  'not open your eyes',
  'open your eyes and describe what you see',
  'open your eyes then close your eyes',
  'open your eyes also close your eyes',
  'tell me what you see',
  'what can you see',
  'open the camera or close the camera',
].forEach(phrase => assert.equal(placement.mediaVoiceCommand(phrase), null, phrase));
placement.setCameraOpen(true);
assert.equal(placement.voiceRequestPayload('describe the camera').frame.captured, true);
assert.equal(placement.voiceRequestPayload('tell me what you see').frame, undefined);
placement.setCameraOpen(false);
assert.equal(placement.workerSpeech({ type: 'approval_required', worker: 'hermes', text: 'Restart the service with these long arguments.' }), 'Gordon is requesting approval. Please take a look.');
assert.equal(placement.workerSpeech({ type: 'question', worker: 'pc-codex', text: 'Which branch and why?' }), 'Friday has a question. Please take a look.');
assert.equal(placement.workerSpeech({ type: 'error', worker: 'vps-codex', text: 'Long stack trace.' }), 'VPS Codex hit a problem. Please take a look.');
placement.rememberTask({ task_id: 'read-only', permission_mode: 'read_only', approved: false });
placement.rememberTask({ task_id: 'private-write', permission_mode: 'workspace_write', approved: true });
assert.equal(placement.workerApprovalAllowsOnce({ task_id: 'read-only' }), false);
assert.equal(placement.workerApprovalAllowsOnce({ task_id: 'private-write' }), true);
assert.equal(placement.workerApprovalAllowsOnce({ task_id: 'unknown' }), false);
assert.equal(placement.workerApprovalAllowsOnce({ task_id: 'read-only', metadata: { permission_mode: 'workspace_write', approved: true } }), false);

let transitionListener = null;
const transitionPanel = {
  hidden: false,
  addEventListener(type, listener) { if (type === 'transitionend') transitionListener = listener; },
  removeEventListener(type, listener) {
    if (type === 'transitionend' && transitionListener === listener) transitionListener = null;
  },
};
sandbox.matchMedia = () => ({ matches: false });
exposeFakeOrb = true;
const unmountsBeforeTransition = orbUnmounts;
placement.deferCallPanelClose(transitionPanel, placement.getVoiceState().voiceCallGeneration);
assert.equal(transitionPanel.hidden, false, 'the panel and orb must remain mounted while the close transition runs');
assert.equal(orbUnmounts, unmountsBeforeTransition);
transitionListener({ target: transitionPanel, propertyName: 'opacity' });
assert.equal(transitionPanel.hidden, false);
assert.equal(orbUnmounts, unmountsBeforeTransition);
transitionListener({ target: transitionPanel, propertyName: 'transform' });
assert.equal(transitionPanel.hidden, true, 'transform transition completion hides the panel');
assert.equal(orbUnmounts, unmountsBeforeTransition + 1, 'sphere teardown waits for the slide transition');
const reducedMotionPanel = { hidden: false, addEventListener() {}, removeEventListener() {} };
sandbox.matchMedia = () => ({ matches: true });
placement.deferCallPanelClose(reducedMotionPanel, placement.getVoiceState().voiceCallGeneration);
assert.equal(reducedMotionPanel.hidden, true, 'reduced motion closes immediately');
exposeFakeOrb = false;
delete sandbox.matchMedia;

(async () => {
  const results = await placement.prewarmVoiceStack();
  assert.equal(results.length, 1);
  assert.equal(results[0].status, 'fulfilled');

  let resolveMicrophone;
  let stopped = false;
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => new Promise(resolve => { resolveMicrophone = resolve; }),
  };
  placement.setActive(true);
  const staleRequest = placement.requestMicrophone(0);
  placement.setActive(false);
  resolveMicrophone({ getTracks: () => [{ stop: () => { stopped = true; } }] });
  assert.equal(await staleRequest, null);
  assert.equal(stopped, true, 'a microphone stream resolving after call close must be stopped');

  const jsonResponse = body => ({ ok: true, status: 200, json: async () => body });
  let resolveLateSession;
  let resolveSessionFirstInterrupt;
  let sessionRequests = 0;
  const interruptRequests = [];
  sandbox.isSecureContext = true;
  sandbox.fetch = (url, options = {}) => {
    if (url === '/api/voice/sessions') {
      sessionRequests += 1;
      if (sessionRequests === 1) {
        return new Promise(resolve => { resolveLateSession = () => resolve(jsonResponse({ id: 'late-session' })); });
      }
      if (sessionRequests === 2) {
        return Promise.resolve(jsonResponse({
          id: 'session-first',
          chat_session_id: null,
          target: 'jarvis',
          workspace: 'home-lab',
        }));
      }
      return Promise.resolve(jsonResponse({
        id: 'retry-session',
        chat_session_id: null,
        target: 'jarvis',
        workspace: 'home-lab',
      }));
    }
    if (String(url).endsWith('/interrupt')) {
      interruptRequests.push({
        url,
        method: options.method,
        body: options.body,
        credentials: options.credentials,
        sessionIdAtRequest: placement.getVoiceState().sessionId,
      });
      if (String(url).includes('/session-first/')) {
        return new Promise(resolve => { resolveSessionFirstInterrupt = () => resolve(jsonResponse({})); });
      }
    }
    return Promise.resolve(jsonResponse({}));
  };
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => Promise.reject(new Error('microphone rejected')),
  };
  const generationBeforeFailure = placement.getVoiceState().voiceCallGeneration;
  await placement.startCall();
  const failedState = placement.getVoiceState();
  assert.equal(failedState.status, 'failed');
  assert.equal(failedState.sessionId, null);
  assert.ok(failedState.voiceCallGeneration >= generationBeforeFailure + 2, 'failed initialization invalidates its generation');
  resolveLateSession();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(placement.getVoiceState().sessionId, null, 'a late session must not mutate failed initialization state');
  assert.deepEqual(interruptRequests[0], {
    url: '/api/voice/sessions/late-session/interrupt',
    method: 'POST',
    body: '{}',
    credentials: 'same-origin',
    sessionIdAtRequest: null,
  });

  let rejectSessionFirstMicrophone;
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => new Promise((_, reject) => { rejectSessionFirstMicrophone = reject; }),
  };
  const sessionFirstCall = placement.startCall();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(placement.getVoiceState().sessionId, 'session-first');
  rejectSessionFirstMicrophone(new Error('microphone rejected after session'));
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(interruptRequests[1], {
    url: '/api/voice/sessions/session-first/interrupt',
    method: 'POST',
    body: '{}',
    credentials: 'same-origin',
    sessionIdAtRequest: 'session-first',
  });

  let retryStreamStopped = false;
  const retryStream = {
    getTracks: () => [{ stop: () => { retryStreamStopped = true; } }],
  };
  sandbox.navigator.mediaDevices = { getUserMedia: () => Promise.resolve(retryStream) };
  sandbox.MediaRecorder = class FakeMediaRecorder {
    constructor() { this.state = 'inactive'; }
    start() { this.state = 'recording'; }
    stop() {
      this.state = 'inactive';
      Promise.resolve().then(() => this.onstop?.());
    }
  };
  await placement.startCall();
  const retryState = placement.getVoiceState();
  assert.equal(retryState.sessionId, 'retry-session');
  assert.equal(retryState.status, 'listening');
  assert.equal(retryState.isActive, true);
  assert.ok(retryState.voiceCallGeneration > failedState.voiceCallGeneration);
  assert.equal(interruptRequests.length, 2, 'the old failure must never interrupt the retry session');
  resolveSessionFirstInterrupt();
  await sessionFirstCall;
  assert.equal(placement.getVoiceState().sessionId, 'retry-session');
  assert.equal(placement.getVoiceState().status, 'listening');

  const targetRequests = [];
  const resolveTargetRequest = {};
  let persistedTarget = 'jarvis';
  sandbox.fetch = (url, options = {}) => {
    if (String(url).endsWith('/target')) {
      const payload = JSON.parse(options.body);
      targetRequests.push(payload.target);
      return new Promise(resolve => {
        resolveTargetRequest[payload.target] = () => {
          persistedTarget = payload.target;
          resolve(jsonResponse({ target: payload.target }));
        };
      });
    }
    return Promise.resolve(jsonResponse({}));
  };
  assert.equal(placement.setVoiceTarget('hermes'), true);
  assert.equal(placement.setVoiceTarget('jarvis'), true);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(targetRequests, ['hermes'], 'Jarvis persistence must wait for the deferred Gordon update');
  resolveTargetRequest.hermes();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(targetRequests, ['hermes', 'jarvis'], 'rapid target changes must persist in selection order');
  resolveTargetRequest.jarvis();
  await placement.waitForTargetUpdate();
  assert.equal(persistedTarget, 'jarvis', 'the final persisted target must match the final client selection');
  assert.equal(placement.getVoiceTarget(), 'jarvis');

  placement.endCall();
  await Promise.resolve();
  assert.equal(retryStreamStopped, true);
  assert.equal(placement.getVoiceState().sessionId, null);

  let resolveSelectedSession;
  let selectionSessionRequests = 0;
  let selectionPersistedTarget = 'jarvis';
  let selectionStreamStopped = false;
  const selectionTargetRequests = [];
  const selectionRespondTargets = [];
  sandbox.fetch = (url, options = {}) => {
    if (url === '/api/voice/sessions') {
      selectionSessionRequests += 1;
      selectionPersistedTarget = 'jarvis';
      return new Promise(resolve => {
        resolveSelectedSession = () => resolve(jsonResponse({
          id: [
            'selection-from-model',
            'selection-explicit-override',
            'selection-during-create',
          ][selectionSessionRequests - 1] || `selection-${selectionSessionRequests}`,
          chat_session_id: null,
          target: 'jarvis',
          workspace: 'home-lab',
        }));
      });
    }
    if (String(url).endsWith('/target')) {
      const payload = JSON.parse(options.body);
      selectionTargetRequests.push(payload);
      selectionPersistedTarget = payload.target;
      return Promise.resolve(jsonResponse(payload));
    }
    if (String(url).endsWith('/respond')) {
      selectionRespondTargets.push(selectionPersistedTarget);
      return Promise.resolve(jsonResponse({ assistant_text: 'Gordon received the turn.' }));
    }
    return Promise.resolve(jsonResponse({}));
  };
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => Promise.resolve({
      getTracks: () => [{ stop: () => { selectionStreamStopped = true; } }],
    }),
  };

  selectedModel = 'hermes-agent';
  const selectedBeforeCall = placement.startCall();
  assert.equal(typeof resolveSelectedSession, 'function');
  resolveSelectedSession();
  await selectedBeforeCall;
  assert.equal(placement.getVoiceTarget(), 'hermes', 'the selected Hermes model must make Gordon the live voice target');
  await placement.sendTurn('Is this Gordon?');
  assert.deepEqual(selectionRespondTargets, ['hermes'], 'the first response request must use the model-derived Gordon target');
  assert.deepEqual(selectionTargetRequests.map(payload => payload.target), ['hermes']);
  assert.equal(selectionTargetRequests[0].workspace, 'home-lab', 'the pre-call workspace must persist with its target');
  placement.endCall();
  await Promise.resolve();
  assert.equal(selectionStreamStopped, true);
  assert.equal(placement.getVoiceTarget(), 'jarvis');
  assert.equal(placement.getTargetSyncState().pendingVoiceTargetState, null);

  selectionStreamStopped = false;
  resolveSelectedSession = null;
  assert.equal(placement.setVoiceTarget('jarvis'), true);
  const explicitOverrideCall = placement.startCall();
  assert.equal(typeof resolveSelectedSession, 'function');
  resolveSelectedSession();
  await explicitOverrideCall;
  assert.equal(placement.getVoiceTarget(), 'jarvis', 'an explicit pre-call Jarvis choice must override the Hermes model');
  await placement.sendTurn('Stay with Jarvis.');
  assert.deepEqual(selectionRespondTargets, ['hermes', 'jarvis']);
  assert.deepEqual(selectionTargetRequests.map(payload => payload.target), ['hermes', 'jarvis']);
  placement.endCall();
  await Promise.resolve();
  assert.equal(selectionStreamStopped, true);

  selectionStreamStopped = false;
  resolveSelectedSession = null;
  selectedModel = 'qwen3.5-jarvis-v5:latest';
  const selectedDuringCreateCall = placement.startCall();
  assert.equal(typeof resolveSelectedSession, 'function');
  assert.equal(placement.setVoiceTarget('hermes'), true);
  resolveSelectedSession();
  await selectedDuringCreateCall;
  assert.equal(placement.getVoiceTarget(), 'hermes', 'session creation must not reset a user selection');
  assert.deepEqual(
    selectionTargetRequests.map(payload => payload.target),
    ['hermes', 'jarvis', 'hermes'],
    'the selection made during session creation must persist once the session is ready',
  );
  assert.equal(placement.getTargetSyncState().confirmedVoiceTargetState.target, 'hermes');
  placement.endCall();
  await Promise.resolve();
  assert.equal(selectionStreamStopped, true);

  let targetFailureAttempt = 0;
  let targetFailureSessionRequests = 0;
  let failedRouteRequests = 0;
  let failureStreamStopped = false;
  const errorResponse = (message, status = 503) => ({
    ok: false,
    status,
    statusText: message,
    json: async () => ({ message }),
  });
  sandbox.fetch = (url, options = {}) => {
    if (url === '/api/voice/sessions') {
      targetFailureSessionRequests += 1;
      return Promise.resolve(jsonResponse({
        id: targetFailureSessionRequests === 1 ? 'target-failure-session' : 'target-recovery-session',
        chat_session_id: null,
        target: 'jarvis',
        workspace: 'home-lab',
      }));
    }
    if (String(url).endsWith('/target')) {
      targetFailureAttempt += 1;
      if (targetFailureAttempt === 1) return Promise.resolve(errorResponse('target save failed'));
      return Promise.resolve(jsonResponse(JSON.parse(options.body)));
    }
    if (String(url).endsWith('/respond/stream')) {
      failedRouteRequests += 1;
      return Promise.resolve(errorResponse('the fail-safe should stop before routing'));
    }
    return Promise.resolve(jsonResponse({}));
  };
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => Promise.resolve({
      getTracks: () => [{ stop: () => { failureStreamStopped = true; } }],
    }),
  };
  await placement.startCall();
  assert.equal(placement.getVoiceTarget(), 'jarvis');
  assert.equal(placement.setVoiceTarget('hermes'), true);
  const failedTargetUpdate = placement.waitForTargetUpdate();
  await assert.rejects(failedTargetUpdate, /Could not switch to Gordon\. Your message was not sent\./);
  assert.equal(placement.getVoiceTarget(), 'jarvis', 'a failed selection must visibly return to the confirmed target');
  await assert.rejects(
    placement.streamTurn('This must not reach the wrong agent.', {}, 0, placement.getVoiceState().voiceCallGeneration),
    /Your message was not sent\./,
  );
  assert.equal(failedRouteRequests, 0, 'a failed target update must block the response request');
  assert.equal((await placement.awaitVoiceTargetReady()).target, 'jarvis', 'the rejected update must not poison later confirmed turns');
  assert.equal(placement.setVoiceTarget('hermes'), true);
  await placement.waitForTargetUpdate();
  assert.equal((await placement.awaitVoiceTargetReady()).target, 'hermes', 'a later successful selection must recover normally');
  placement.endCall();
  await Promise.resolve();
  assert.equal(failureStreamStopped, true);

  let recoveryStreamStopped = false;
  sandbox.navigator.mediaDevices = {
    getUserMedia: () => Promise.resolve({
      getTracks: () => [{ stop: () => { recoveryStreamStopped = true; } }],
    }),
  };
  await placement.startCall();
  assert.equal(placement.getVoiceState().sessionId, 'target-recovery-session');
  assert.equal((await placement.awaitVoiceTargetReady()).target, 'jarvis', 'a new call must start with a clean target-sync state');
  placement.endCall();
  await Promise.resolve();
  assert.equal(recoveryStreamStopped, true);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
