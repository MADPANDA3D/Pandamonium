import { expect, test } from '@playwright/test';

async function installVoiceRoutes(page) {
  await page.route('**/api/voice/status', route => route.fulfill({
    json: {
      assistant: 'Odysseus',
      stt: { available: true, provider: 'endpoint:test' },
      tts: { available: false, provider: 'disabled', voice: '', speed: 1 },
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
}

test.beforeEach(async ({ page }) => {
  await installVoiceRoutes(page);
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
  await page.goto('/static/index.html');
});

test('voice orb uses the fake microphone and stops it on close', async ({ page }) => {
  await page.getByRole('button', { name: 'Open Odysseus Voice Orb' }).first().click();
  await expect(page.locator('#voice-orb-panel')).toBeVisible();
  await expect(page.locator('#voice-orb-panel')).toHaveAttribute('data-state', 'listening');

  const requested = await page.evaluate(() => window.__capturedMedia.constraints[0]);
  expect(requested.audio).toBeTruthy();
  expect(requested.video).toBeUndefined();

  await page.getByRole('button', { name: 'End voice' }).click();
  await expect(page.locator('#voice-orb-panel')).toBeHidden();
  await expect.poll(() => page.evaluate(() => (
    window.__capturedMedia.streams.flatMap(stream => stream.getTracks()).every(track => track.readyState === 'ended')
  ))).toBe(true);
});

test('fake camera is available but v0.1 does not attach a camera surface', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const tracks = stream.getVideoTracks();
    const labels = tracks.map(track => track.kind);
    tracks.forEach(track => track.stop());
    return labels;
  });

  expect(result).toEqual(['video']);
  await expect(page.locator('#voice-orb-panel video')).toHaveCount(0);
});
