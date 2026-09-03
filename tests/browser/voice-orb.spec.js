import { expect, test } from '@playwright/test';

async function installVoiceRoutes(page) {
  const requests = { sessions: [], interrupts: 0, prewarms: 0 };
  await page.route('**/api/voice/oracle-config', route => route.fulfill({
    json: { oracle_protocol_url: '', extension_surfaces: [] },
  }));
  await page.route('**/api/agent-workers', route => route.fulfill({
    json: {
      workers: {
        jarvis: { enabled: true, machine: 'Self-hosted', connection: { state: 'connected' } },
      },
    },
  }));
  await page.route('**/api/voice/prewarm', route => {
    requests.prewarms += 1;
    return route.fulfill({ json: { ok: true, brain_state: 'selected-chat-session', tts_state: 'warmed' } });
  });
  await page.route(url => url.pathname === '/api/voice/sessions', async route => {
    requests.sessions.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        id: 'browser-test-session',
        chat_session_id: null,
        status: 'listening',
        target: 'jarvis',
        workspace: 'home-lab',
        extension_surfaces: [],
      },
    });
  });
  await page.route('**/api/voice/sessions/*/interrupt', route => {
    requests.interrupts += 1;
    return route.fulfill({ json: { ok: true, status: 'interrupted' } });
  });
  return requests;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__capturedMedia = { constraints: [], streams: [] };
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      const stream = await nativeGetUserMedia(constraints);
      window.__capturedMedia.constraints.push(constraints);
      window.__capturedMedia.streams.push(stream);
      return stream;
    };
  });
});

test('Pandamonium call panel opens with a bounded microphone session and closes cleanly', async ({ page }) => {
  const requests = await installVoiceRoutes(page);
  await page.goto('/static/index.html');

  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.jarvisVoiceBound || '')).toBe('1');
  await page.evaluate(() => window.jarvisVoice.startCall());
  const panel = page.locator('#jarvis-call-panel');
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute('data-state', 'listening');
  await expect.poll(() => requests.sessions.length).toBe(1);
  expect(requests.sessions[0].mode).toBe('jarvis_call');
  await expect.poll(() => requests.prewarms).toBe(1);

  const constraints = await page.evaluate(() => window.__capturedMedia.constraints[0]);
  expect(constraints.audio.echoCancellation).toBe(true);
  expect(constraints.audio.noiseSuppression).toBe(true);
  expect(constraints.video).toBeUndefined();

  await page.evaluate(() => window.jarvisVoice.endCall());
  await expect(panel).toBeHidden();
  await expect.poll(() => page.evaluate(() => (
    window.__capturedMedia.streams.flatMap(stream => stream.getTracks())
      .every(track => track.readyState === 'ended')
  ))).toBe(true);
  await expect.poll(() => requests.interrupts).toBe(1);
});

test('camera media stays browser-owned and releases every track', async ({ page }) => {
  await installVoiceRoutes(page);
  await page.goto('/static/index.html');

  const state = await page.evaluate(async () => {
    const media = await import('/static/js/voiceOrbMedia.js');
    const opened = await media.openCamera();
    const element = document.getElementById('odysseus-voice-orb-media');
    const attached = Boolean(element && element.parentElement?.id === 'jarvis-call-orb');
    media.closeCamera();
    return { opened, attached, closed: media.getState() };
  });

  expect(state.opened.mode).toBe('camera');
  expect(state.attached).toBe(true);
  expect(state.closed.mode).toBe('idle');
  const cameraConstraints = await page.evaluate(() => (
    window.__capturedMedia.constraints.find(item => Boolean(item.video))
  ));
  expect(cameraConstraints.audio).toBe(false);
  expect(cameraConstraints.video.width.ideal).toBe(1024);
  await expect.poll(() => page.evaluate(() => (
    window.__capturedMedia.streams.flatMap(stream => stream.getTracks())
      .every(track => track.readyState === 'ended')
  ))).toBe(true);
});
