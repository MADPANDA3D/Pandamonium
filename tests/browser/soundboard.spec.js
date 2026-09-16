import { expect, test } from '@playwright/test';

const sound = { id: 'vine-boom-123', title: 'Vine boom', url: 'https://www.myinstants.com/en/instant/vine-boom-123/', mp3: 'https://www.myinstants.com/media/sounds/vine-boom.mp3', cue: '[[sound:vine-boom-123]]' };

async function app(page) {
  const state = { installed: true, enabled: true, favorites: [], volume: 0.6, muted: false };
  await page.addInitScript(() => {
    window.soundPlays = 0;
    HTMLMediaElement.prototype.play = async function () { window.soundPlays += 1; };
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/soundboard') return route.fulfill({ json: state });
    if (path.startsWith('/api/soundboard/favorites/')) {
      state.favorites = route.request().postDataJSON().enabled ? [sound] : [];
      return route.fulfill({ json: { saved: true } });
    }
    if (path === '/api/soundboard/preferences') {
      Object.assign(state, route.request().postDataJSON());
      return route.fulfill({ json: { saved: true } });
    }
    if (path.startsWith('/api/soundboard/sounds')) return route.fulfill({ json: { sounds: [sound] } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  return state;
}

test('installed Settings supports manual preview, saved favorites, mute and uninstall visibility', async ({ page }) => {
  const state = await app(page);
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('soundboard'));
  await expect(page.locator('#soundboard-results')).toContainText('Vine boom');
  expect(await page.evaluate(() => window.soundPlays)).toBe(0);
  await page.locator('#soundboard-results .sound-cue').click();
  await expect.poll(() => page.evaluate(() => window.soundPlays)).toBe(1);
  await page.locator('#soundboard-results [data-sound-favorite]').click();
  await expect(page.locator('#soundboard-favorites')).toContainText('Vine boom');
  await page.locator('#soundboard-muted').check();
  await expect.poll(() => state.muted).toBe(true);
  await page.locator('#soundboard-favorites .sound-cue').click();
  expect(await page.evaluate(() => window.soundPlays)).toBe(1);
  await page.screenshot({ path: '/tmp/mad968-settings-fixture.png', fullPage: true });
  state.enabled = false;
  await page.evaluate(() => window.dispatchEvent(new Event('pandamonium:extensions-changed')));
  await expect(page.locator('#soundboard-status')).toContainText('disabled');
  await expect(page.locator('#soundboard-favorites .sound-cue')).toBeDisabled();
  state.installed = false;
  await page.evaluate(() => window.dispatchEvent(new Event('pandamonium:extensions-changed')));
  await expect(page.locator('[data-settings-tab="soundboard"]')).toBeHidden();
});

test('cue positions survive final rendering and history; partial markers do not leak or autoplay', async ({ page }) => {
  await app(page);
  const text = `Bad news ${sound.cue} and more news ${sound.cue} but we can fix it.`;
  await page.evaluate(async value => {
    const { addMessage } = await import('/static/js/chatRenderer.js');
    addMessage('assistant', value, 'Jarvis', { timestamp: Date.now(), _db_id: 'soundboard-test' });
  }, text);
  const body = page.locator('.msg-ai .body').last();
  await expect(body.locator('.sound-cue')).toHaveCount(2);
  await expect(body).not.toContainText('[[sound:');
  expect(await page.evaluate(() => window.soundPlays)).toBe(0);
  const offsets = await body.locator('.sound-cue').evaluateAll(nodes => nodes.map(node => node.dataset.soundCue));
  expect(new Set(offsets).size).toBe(2);
  await body.locator('.sound-cue').first().click();
  await expect.poll(() => page.evaluate(() => window.soundPlays)).toBe(1);
  await page.screenshot({ path: '/tmp/mad968-text-fixture.png', fullPage: true });
  const html = await page.evaluate(async () => {
    const { mdToHtml } = await import('/static/js/markdown.js');
    return { partial: mdToHtml('Bad news [[sound:vine-bo'), code: mdToHtml('`[[sound:vine-boom-123]]`') };
  });
  expect(html.partial).not.toContain('[[sound:');
  expect(html.code).toContain('<code>[[sound:vine-boom-123]]</code>');
  await page.reload();
  await page.evaluate(async value => (await import('/static/js/chatRenderer.js')).addMessage('assistant', value, 'Jarvis', { _db_id: 'soundboard-test' }), text);
  await expect(page.locator('.msg-ai .body').last().locator('.sound-cue')).toHaveCount(2);
  expect(await page.evaluate(() => window.soundPlays)).toBe(0);
});

test('voice effects use the speech clock, deduplicate, skip late media and cancel independently', async ({ page }) => {
  const state = await app(page);
  await page.route('**/api/soundboard/sounds/*/audio', route => route.fulfill({ contentType: 'audio/wav', body: Buffer.from([0]) }));
  const result = await page.evaluate(async () => {
    const sounds = await import('/static/js/soundboard.js');
    const starts = [], stopped = [];
    const context = {
      currentTime: 1, state: 'running', destination: {},
      decodeAudioData: async () => ({ duration: 1 }),
      createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }),
      createBufferSource: () => {
        const source = { connect(gain) { return gain; }, disconnect() {},
          start(time) { starts.push(time); }, stop() { stopped.push(1); source.onended?.(); } };
        return source;
      },
    };
    const cue = { cue_id: 'turn:second-boom', sound_id: 'vine-boom-123', end_sample: 48000, title: 'Boom' };
    const prepared = sounds.prepareVoiceCues(context, [cue, cue]);
    await Promise.all(prepared.map(item => item.ready));
    sounds.scheduleVoiceCue(context, prepared[0], 3);
    await Promise.resolve();
    sounds.stopVoiceSounds();
    const delayed = sounds.prepareVoiceCues(context, [{ ...cue, cue_id: 'late' }]);
    await delayed[0].ready;
    context.currentTime = 8;
    sounds.scheduleVoiceCue(context, delayed[0], 3);
    await Promise.resolve();
    sounds.stopVoiceSounds();
    const cancelled = sounds.prepareVoiceCues(context, [{ ...cue, cue_id: 'cancelled' }]);
    sounds.stopVoiceSounds();
    sounds.scheduleVoiceCue(context, cancelled[0], 9);
    await cancelled[0].ready;
    await Promise.resolve();
    return { starts, stopped, deduplicated: prepared.length };
  });
  expect(result).toEqual({ starts: [3], stopped: [1], deduplicated: 1 });
  state.muted = true;
  const muted = await page.evaluate(async () => {
    const sounds = await import('/static/js/soundboard.js');
    const cues = sounds.prepareVoiceCues({}, [{ cue_id: 'muted', sound_id: 'vine-boom-123', end_sample: 1 }]);
    return await cues[0].ready;
  });
  expect(muted).toBeNull();
});
