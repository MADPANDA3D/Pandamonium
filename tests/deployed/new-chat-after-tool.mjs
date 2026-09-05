import { chromium } from '@playwright/test';

const baseUrl = String(process.env.PANDAMONIUM_DEPLOYED_URL || '').replace(/\/$/, '');
const sessionToken = String(process.env.PANDAMONIUM_SESSION_TOKEN || '');
const timeoutMs = Number(process.env.PANDAMONIUM_E2E_TIMEOUT_MS || 300_000);

if (!baseUrl || !sessionToken) {
  throw new Error(
    'Set PANDAMONIUM_DEPLOYED_URL and PANDAMONIUM_SESSION_TOKEN to run the deployed New Chat check.',
  );
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function waitForValue(page, callback, predicate, description) {
  const deadline = Date.now() + timeoutMs;
  let value;
  while (Date.now() < deadline) {
    value = await page.evaluate(callback);
    if (predicate(value)) return value;
    await page.waitForTimeout(250);
  }
  throw new Error(`Timed out waiting for ${description}; last value: ${JSON.stringify(value)}`);
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const createdSessionIds = [];
const pageErrors = [];

page.on('pageerror', error => pageErrors.push(String(error?.stack || error)));

async function deleteCreatedSessions() {
  if (page.isClosed() || !createdSessionIds.length) return [];
  const ids = [...new Set(createdSessionIds)];
  const results = await page.evaluate(async sessionIds => Promise.all(sessionIds.map(async id => {
    const response = await fetch(`/api/session/${encodeURIComponent(id)}`, { method: 'DELETE' });
    return { id, status: response.status };
  })), ids);
  createdSessionIds.length = 0;
  return results;
}

try {
  await context.addCookies([{
    name: 'odysseus_session',
    value: sessionToken,
    url: baseUrl,
    httpOnly: true,
    sameSite: 'Lax',
  }]);

  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded', timeout: timeoutMs });
  await page.locator('#message').waitFor({ state: 'visible', timeout: timeoutMs });

  const auth = await page.evaluate(async () => {
    const response = await fetch('/api/auth/status');
    return { status: response.status, body: await response.json() };
  });
  assert(auth.status === 200 && auth.body?.username, 'Temporary browser session was not authenticated.');

  await page.locator('#sidebar-new-chat-btn').click();
  await page.locator('#current-meta').waitFor({ state: 'visible', timeout: timeoutMs });
  await waitForValue(
    page,
    () => document.getElementById('current-meta')?.textContent?.trim(),
    value => value === 'New Chat',
    'the initial blank New Chat state',
  );

  const originalTarget = await waitForValue(
    page,
    () => ({
      target: window.sessionModule?.getChatAgentTarget?.() || '',
      pending: window.sessionModule?.getPendingChat?.() || null,
    }),
    value => Boolean(value?.target || (value?.pending?.url && value?.pending?.modelId)),
    'a usable selected identity or model configuration',
  );

  await page.evaluate(() => {
    const checkbox = document.getElementById('bash-toggle');
    if (checkbox && !checkbox.checked) document.getElementById('bash-toggle-btn')?.click();
  });

  const firstPrompt = `MAD-802 deployed check ${Date.now()}: run the bash command whoami and tell me the result.`;
  await page.locator('#message').fill(firstPrompt);
  await page.locator('.send-btn:visible').click();

  const firstSessionId = await waitForValue(
    page,
    () => window.sessionModule?.getCurrentSessionId?.() || '',
    value => Boolean(value),
    'the tool-check chat to materialize',
  );
  createdSessionIds.push(firstSessionId);

  const completedTool = page.locator('.agent-thread-node').filter({ hasText: /bash.*done/i }).first();
  await completedTool.waitFor({ state: 'visible', timeout: timeoutMs });
  await waitForValue(
    page,
    () => ({
      streaming: document.querySelector('.send-btn')?.dataset?.mode === 'streaming',
      runningTools: document.querySelectorAll('.agent-thread-node.running').length,
    }),
    value => !value?.streaming && value?.runningTools === 0,
    'the real tool turn to finish',
  );

  await page.locator('#sidebar-new-chat-btn').click();
  await page.locator('#current-meta').filter({ hasText: /^New Chat$/ }).waitFor({
    state: 'visible',
    timeout: 1_500,
  });

  const resetState = await page.evaluate(() => ({
    sessionId: window.sessionModule?.getCurrentSessionId?.() || null,
    target: window.sessionModule?.getChatAgentTarget?.() || '',
    meta: document.getElementById('current-meta')?.textContent?.trim() || '',
    toolCards: document.querySelectorAll('#chat-history .agent-thread-node').length,
    messages: document.querySelectorAll('#chat-history .msg').length,
    inputDisabled: Boolean(document.getElementById('message')?.disabled),
  }));

  assert(resetState.sessionId === null, 'New Chat retained the completed tool session id.');
  assert(resetState.meta === 'New Chat', 'New Chat did not replace the old conversation header.');
  assert(resetState.toolCards === 0 && resetState.messages === 0, 'The completed tool conversation remained rendered.');
  assert(!resetState.inputDisabled, 'The New Chat composer is disabled.');
  if (originalTarget.target) {
    assert(resetState.target === originalTarget.target, 'New Chat changed the selected identity.');
  }

  const secondPrompt = `MAD-802 new-chat continuation ${Date.now()}: reply exactly MAD802_NEW_CHAT_READY without tools.`;
  await page.locator('#message').fill(secondPrompt);
  await page.locator('.send-btn:visible').click();
  const secondSessionId = await waitForValue(
    page,
    () => window.sessionModule?.getCurrentSessionId?.() || '',
    value => Boolean(value) && value !== firstSessionId,
    'a distinct post-reset chat to materialize',
  );
  createdSessionIds.push(secondSessionId);
  await waitForValue(
    page,
    () => ({
      streaming: document.querySelector('.send-btn')?.dataset?.mode === 'streaming',
      text: document.getElementById('chat-history')?.textContent || '',
    }),
    value => !value?.streaming && value?.text?.includes('MAD802_NEW_CHAT_READY'),
    'the post-reset task response',
  );

  assert(
    !pageErrors.some(error => error.includes('ReferenceError') || error.includes('emitVoiceLifecycle')),
    `Browser runtime error after New Chat: ${pageErrors.join('\n')}`,
  );

  const cleanup = await deleteCreatedSessions();
  assert(
    cleanup.length === 2 && cleanup.every(result => result.status === 200),
    `Temporary chat cleanup failed: ${JSON.stringify(cleanup)}`,
  );

  console.log(JSON.stringify({
    status: 'passed',
    user: auth.body.username,
    selectedIdentity: resetState.target || originalTarget.target || null,
    completedTool: 'bash whoami',
    immediateBlankState: true,
    oldConversationReturned: false,
    postResetTaskStarted: true,
    cleanedSessionCount: cleanup.length,
  }));
} finally {
  await deleteCreatedSessions().catch(() => {});
  if (!page.isClosed()) {
    await context.request.post(`${baseUrl}/api/auth/logout`).catch(() => {});
  }
  await context.close();
  await browser.close();
}
