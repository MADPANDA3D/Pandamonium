import { expect, test } from '@playwright/test';

const harnesses = new WeakMap();
const SETUP_TEXT = 'Core voice setup is ready. 2 of 3 optional fixed read-only workers are ready.';

function setupSnapshot(text = SETUP_TEXT, workers = [
  { id: 'pc-codex', configured: true, ready: true, status: 'ready', capabilities: ['read_only'] },
  { id: 'hermes', configured: true, ready: true, status: 'ready', capabilities: ['read_only', 'questions'] },
  { id: 'vps-codex', configured: false, ready: false, status: 'not_configured', capabilities: [] },
]) {
  return {
    version: 1,
    core_ready: true,
    text,
    model: { configured: true, selection: 'default' },
    speech_to_text: { available: true, provider: 'endpoint' },
    text_to_speech: { available: true, provider: 'browser' },
    workers: { optional: true, ready_count: 2, total: 3, items: workers },
  };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function sse(events) {
  return events.map(event => `data: ${JSON.stringify(event)}\n\n`).join('');
}

async function installVoiceRoutes(page) {
  const state = {
    transcripts: [],
    responseEvents: [],
    requests: [],
    manifestRequests: 0,
    workerEventRequested: false,
    workerGate: null,
    workerListRequests: 0,
    tailnetListRequests: 0,
    tailnetProbeRequests: [],
  };
  harnesses.set(page, state);

  await page.route('**/api/voice/status', route => route.fulfill({
    json: {
      assistant: 'Odysseus',
      stt: { available: true, provider: 'endpoint:test' },
      tts: { available: false, provider: 'disabled', voice: '', speed: 1 },
      setup: setupSnapshot(),
    },
  }));
  await page.route('**/api/voice/sessions', route => route.fulfill({
    json: {
      id: 'browser-test-session',
      chat_session_id: 'browser-test-chat',
      assistant: 'Odysseus',
      model: 'test-model',
      status: 'ready',
    },
  }));
  await page.route('**/api/voice/sessions/*/interrupt', route => route.fulfill({
    json: { ok: true, status: 'interrupted' },
  }));
  await page.route('**/api/stt/transcribe', route => route.fulfill({
    json: { text: state.transcripts.shift() || '' },
  }));
  await page.route('**/api/voice/sessions/*/respond', async route => {
    state.requests.push(route.request().postDataJSON());
    const events = state.responseEvents.shift() || [{ type: 'final', text: 'Done.' }];
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: sse(events),
    });
  });
  await page.route(url => url.pathname === '/api/agent-tasks' && url.searchParams.has('session_id'), route => {
    state.workerListRequests += 1;
    return route.fulfill({ json: { tasks: [] } });
  });
  await page.route('**/api/agent-tasks/*/events', async route => {
    state.workerEventRequested = true;
    if (state.workerGate) await state.workerGate.promise;
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
      body: sse([{
        event_id: 'task-cont:2',
        task_id: 'task-cont',
        seq: 2,
        type: 'result',
        text: 'Worker completed after voice ended.',
      }]),
    });
  });
  await page.route(url => url.pathname === '/api/discover', route => {
    const mode = route.request().url().includes('mode=tailnet_probe') ? 'tailnet_probe' : 'tailnet_peers';
    if (mode === 'tailnet_peers') {
      state.tailnetListRequests += 1;
      return route.fulfill({
        json: {
          mode,
          peers: [{ id: 'a'.repeat(32), os: 'linux', status: 'online', address: '192.0.2.10' }],
          raw_url: 'http://192.0.2.10',
        },
      });
    }
    const url = new URL(route.request().url());
    state.tailnetProbeRequests.push(url.searchParams.getAll('peer_id'));
    return route.fulfill({
      json: {
        mode,
        candidates: [{
          peer_id: 'a'.repeat(32),
          provider: 'ollama',
          models: ['safe-model:latest'],
          capabilities: ['model-list'],
          url: 'http://192.0.2.10:11434',
        }],
      },
    });
  });
  await page.route('**/static/voice-orb-media.json', route => {
    state.manifestRequests += 1;
    return route.continue();
  });
}

async function startVoice(page) {
  await page.getByRole('button', { name: 'Open Odysseus Voice Orb' }).first().click();
  await expect(page.locator('#voice-orb-panel')).toBeVisible();
  await expect(page.locator('#voice-orb-panel')).toHaveAttribute('data-state', 'listening');
}

async function runTurn(page, text, events = []) {
  const state = harnesses.get(page);
  const before = state.requests.length;
  const panel = page.locator('#voice-orb-panel');
  if (await panel.getAttribute('data-state') !== 'listening') {
    await page.locator('#voice-orb-talk').click();
    await expect(panel).toHaveAttribute('data-state', 'listening');
  }
  state.transcripts.push(text);
  state.responseEvents.push(events.some(event => event.type === 'final')
    ? events
    : [...events, { type: 'final', text: 'Done.' }]);
  await page.locator('#voice-orb-talk').click();
  await expect.poll(() => state.requests.length).toBe(before + 1);
  await expect(panel).toHaveAttribute('data-state', 'idle');
  return state.requests.at(-1);
}

test.beforeEach(async ({ page }) => {
  await installVoiceRoutes(page);
  await page.addInitScript(() => {
    window.__capturedMedia = { constraints: [], streams: [] };
    window.__deferNextVideo = false;
    window.__resolvePendingVideo = null;
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      const stream = await nativeGetUserMedia(constraints);
      window.__capturedMedia.constraints.push(constraints);
      window.__capturedMedia.streams.push(stream);
      if (constraints?.video && window.__deferNextVideo) {
        window.__deferNextVideo = false;
        return new Promise(resolve => {
          window.__resolvePendingVideo = () => resolve(stream);
        });
      }
      return stream;
    };
  });
  await page.goto('/static/index.html');
});

test('setup status is mirrored and Tailnet inspection stays explicit', async ({ page }) => {
  const state = harnesses.get(page);
  await startVoice(page);

  await expect(page.locator('#voice-orb-setup-text')).toHaveText(SETUP_TEXT);
  await expect(page.locator('#voice-orb-setup-workers-summary')).toContainText('Fixed worker cluster');
  await expect(page.locator('#voice-orb-setup-workers')).not.toContainText('discovered');
  expect(state.tailnetListRequests).toBe(0);
  expect(state.tailnetProbeRequests).toEqual([]);

  const spokenText = 'Voice setup needs attention. Optional fixed read-only workers are not ready.';
  await runTurn(page, 'Check voice setup.', [{
    type: 'final',
    text: spokenText,
    setup: setupSnapshot(spokenText, [
      { id: 'pc-codex', configured: true, ready: true, status: 'ready', capabilities: ['read_only'] },
      { id: 'discovered-agent', configured: true, ready: true, status: 'ready', capabilities: ['write'] },
    ]),
  }]);
  await expect(page.locator('#voice-orb-setup')).toHaveAttribute('open', '');
  await expect(page.locator('#voice-orb-setup-text')).toHaveText(spokenText);
  await expect(page.locator('#voice-orb-setup-workers-summary')).not.toContainText('cluster');
  expect(state.tailnetListRequests).toBe(0);

  await page.getByRole('button', { name: 'Inspect Tailnet peers' }).click();
  await expect.poll(() => state.tailnetListRequests).toBe(1);
  await expect(page.locator('#voice-orb-tailnet-peers')).toContainText('linux · online');
  await expect(page.locator('#voice-orb-tailnet-peers')).not.toContainText('192.0.2');
  await page.locator('#voice-orb-tailnet-peers input[type="checkbox"]').check();
  await page.getByRole('button', { name: 'Probe selected peers' }).click();
  await expect.poll(() => state.tailnetProbeRequests).toEqual([['a'.repeat(32)]]);
  await expect(page.locator('#voice-orb-tailnet-results')).toContainText('safe-model:latest');
  await expect(page.locator('#voice-orb-setup')).not.toContainText('192.0.2.10');
});

test('voice orb uses the fake microphone and stops it on close', async ({ page }) => {
  await startVoice(page);

  const requested = await page.evaluate(() => window.__capturedMedia.constraints[0]);
  expect(requested.audio).toBeTruthy();
  expect(requested.video).toBeUndefined();

  await page.getByRole('button', { name: 'End voice' }).click();
  await expect(page.locator('#voice-orb-panel')).toBeHidden();
  await expect.poll(() => page.evaluate(() => (
    window.__capturedMedia.streams.flatMap(stream => stream.getTracks()).every(track => track.readyState === 'ended')
  ))).toBe(true);
});

test('End Voice invalidates a pending camera permission result', async ({ page }) => {
  await startVoice(page);
  await page.evaluate(() => { window.__deferNextVideo = true; });
  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);

  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'camera-pending');
  await expect(page.locator('#voice-orb-media-indicator')).toHaveText('Camera permission requested');
  await expect.poll(() => page.evaluate(() => typeof window.__resolvePendingVideo)).toBe('function');

  await page.getByRole('button', { name: 'End voice' }).click();
  await expect(page.locator('#voice-orb-panel')).toBeHidden();
  await page.evaluate(() => window.__resolvePendingVideo());
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect(page.locator('#voice-orb-media-indicator')).toBeHidden();
  await expect.poll(() => page.evaluate(() => {
    const index = window.__capturedMedia.constraints.findLastIndex(item => Boolean(item?.video));
    return index >= 0 && window.__capturedMedia.streams[index].getTracks().every(track => track.readyState === 'ended');
  })).toBe(true);
});

test('camera permission denial fails closed', async ({ page }) => {
  await startVoice(page);
  await page.evaluate(() => {
    const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = constraints => {
      if (constraints?.video) throw new DOMException('Permission denied', 'NotAllowedError');
      return getUserMedia(constraints);
    };
  });

  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);

  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect(page.locator('#voice-orb-media-indicator')).toBeHidden();
  await expect(page.locator('#voice-orb-detail')).toHaveText('Camera access is unavailable.');
  await expect(page.locator('#toast')).toContainText('Camera permission was denied.');
});

test('missing browser camera API fails closed', async ({ page }) => {
  await startVoice(page);
  await page.evaluate(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true,
      value: undefined,
    });
  });

  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);

  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect(page.locator('#voice-orb-media-indicator')).toBeHidden();
  await expect(page.locator('#odysseus-voice-orb-media')).toHaveCount(0);
  await expect(page.locator('#toast')).toContainText('Camera is unavailable.');
});

test('camera frames and built-in media stay bounded to exact voice controls', async ({ page }) => {
  const state = harnesses.get(page);
  await startVoice(page);
  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);

  const video = page.locator('#odysseus-voice-orb-media');
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'camera');
  await expect(page.locator('#voice-orb-media-indicator')).toHaveText('Camera on');
  await expect(video).toHaveCount(1);
  await expect.poll(() => video.evaluate(node => node.videoWidth)).toBeGreaterThan(0);

  const exact = await runTurn(page, 'What do you see?');
  expect(exact.frame?.mime).toBe('image/jpeg');
  expect(exact.frame?.data_base64).toBeTruthy();
  expect(exact.frame?.width).toBeLessThanOrEqual(1024);
  expect(exact.frame?.height).toBeLessThanOrEqual(576);

  const exactDescribe = await runTurn(page, 'Describe what you see.');
  expect(exactDescribe.frame?.mime).toBe('image/jpeg');

  const compound = await runTurn(page, 'Open your eyes and describe what you see.');
  expect(compound.frame).toBeUndefined();

  const cameraIndex = await page.evaluate(() => (
    window.__capturedMedia.constraints.findLastIndex(item => Boolean(item?.video))
  ));
  await runTurn(page, 'Close your eyes.', [{ type: 'ui_control', ui_event: 'camera_close' }]);
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect.poll(() => page.evaluate(index => (
    window.__capturedMedia.streams[index].getTracks().every(track => track.readyState === 'ended')
  ), cameraIndex)).toBe(true);

  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'camera');
  await page.evaluate(() => {
    const index = window.__capturedMedia.constraints.findLastIndex(item => Boolean(item?.video));
    window.__capturedMedia.streams[index].getVideoTracks()[0].dispatchEvent(new Event('ended'));
  });
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect(page.locator('#voice-orb-media-indicator')).toBeHidden();

  await runTurn(page, 'I need something motivational.', [{
    type: 'ui_control', ui_event: 'media_play', media_id: 'motivational-abstract',
  }]);
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'clip');
  await expect(video).toHaveAttribute('src', /\/static\/media\/voice-orb\/motivational-abstract\.webm$/);
  await expect(video).toHaveCount(1);
  const safeManifestRequests = state.manifestRequests;

  await runTurn(page, 'Play an unsafe clip.', [
    { type: 'ui_control', ui_event: 'media_play', media_id: 'not-listed' },
    {
      type: 'ui_control',
      ui_event: 'media_play',
      media_id: 'motivational-abstract',
      url: 'https://evil.invalid/clip.webm',
    },
    { type: 'ui_control', ui_event: 'camera_open', selector: '#chat-input' },
  ]);
  expect(state.manifestRequests).toBe(safeManifestRequests);
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'clip');
  await expect(video).toHaveCount(1);
  await page.getByRole('button', { name: 'End voice' }).click();
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
});

test('media stops while a worker continues after End Voice', async ({ page }) => {
  const state = harnesses.get(page);
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('browser-test-chat');
  });
  await expect.poll(() => state.workerListRequests).toBeGreaterThan(0);

  await startVoice(page);
  await runTurn(page, 'Open your eyes.', [{ type: 'ui_control', ui_event: 'camera_open' }]);
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'camera');

  state.workerGate = deferred();
  await runTurn(page, 'Ask PC Codex to inspect the repository.', [{
    type: 'worker_task',
    task: {
      task_id: 'task-cont',
      session_id: 'browser-test-chat',
      worker: 'pc-codex',
      workspace: 'default',
      status: 'running',
      events: [],
    },
  }]);
  await expect(page.locator('.voice-worker-task[data-task-id="task-cont"] .voice-worker-state')).toHaveText('running');
  await expect.poll(() => state.workerEventRequested).toBe(true);

  await page.getByRole('button', { name: 'End voice' }).click();
  await expect(page.locator('#voice-orb-panel')).toBeHidden();
  await expect(page.locator('#voice-orb-talk')).toHaveAttribute('data-voice-media', 'idle');
  await expect(page.locator('.voice-worker-task[data-task-id="task-cont"] .voice-worker-state')).toHaveText('running');

  state.workerGate.resolve();
  await expect(page.locator('.voice-worker-task[data-task-id="task-cont"] .voice-worker-state')).toHaveText('completed');
  await expect(page.locator('.voice-worker-task[data-task-id="task-cont"] .voice-worker-events')).toContainText(
    'Worker completed after voice ended.',
  );
  await expect(page.locator('#voice-orb-panel')).toBeHidden();
});
