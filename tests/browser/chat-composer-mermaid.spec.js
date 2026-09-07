import { expect, test } from '@playwright/test';

const model = {
  endpoint_id: 'endpoint-one',
  endpoint_url: 'http://model.test/v1/chat/completions',
  model: 'test/model',
};

async function mockApp(page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: true,
          privileges: {},
          agent_identity: { source: 'configured', display_name: 'Jarvis' },
        },
      });
    }
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: model });
    if (url.pathname === '/api/models') {
      return route.fulfill({
        json: {
          items: [{
            ...model,
            id: model.endpoint_id,
            url: model.endpoint_url,
            models: [model.model],
            models_display: ['Test Model'],
            offline: false,
          }],
        },
      });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/sessions') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

async function composerMetrics(page) {
  return page.locator('.chat-input-top:visible').evaluate(inputTop => {
    const textarea = inputTop.querySelector('textarea#message');
    const ghost = inputTop.querySelector('#message-ghost');
    const picker = inputTop.querySelector('#model-picker-wrap');
    const textareaRect = textarea.getBoundingClientRect();
    const pickerRect = picker.getBoundingClientRect();
    const paddingRight = parseFloat(getComputedStyle(textarea).paddingRight) || 0;
    const ghostPaddingRight = parseFloat(getComputedStyle(ghost).paddingRight) || 0;
    return {
      inputWidth: inputTop.clientWidth,
      pickerDisplay: getComputedStyle(picker).display,
      pickerLeft: pickerRect.left,
      paddingRight,
      ghostPaddingRight,
      textRight: textareaRect.right - paddingRight,
      textareaWidth: textareaRect.width,
      ghostWidth: ghost.getBoundingClientRect().width,
      textareaHeight: textareaRect.height,
    };
  });
}

async function assertComposerClearsPicker(page) {
  await expect.poll(async () => (await composerMetrics(page)).paddingRight).toBeGreaterThan(20);
  const metrics = await composerMetrics(page);
  expect(metrics.pickerDisplay).not.toBe('none');
  expect(metrics.textRight).toBeLessThanOrEqual(metrics.pickerLeft - 7);
  expect(metrics.ghostPaddingRight).toBe(metrics.paddingRight);
  return metrics;
}

test('composer reserves the live picker width across desktop, split, and narrow layouts', async ({ page }) => {
  await mockApp(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');

  const textarea = page.locator('#message:visible');
  await page.locator('#model-picker-label:visible').evaluate(label => {
    label.textContent = 'Jarvis Network Inspector';
  });
  await textarea.fill('A long composer draft must wrap before the selector instead of disappearing beneath it.');

  const desktop = await assertComposerClearsPicker(page);
  expect(desktop.inputWidth).toBeGreaterThan(900);

  await page.locator('.chat-input-bar:visible').evaluate(bar => { bar.style.width = '420px'; });
  await expect.poll(async () => (await composerMetrics(page)).inputWidth).toBeLessThanOrEqual(420);
  await assertComposerClearsPicker(page);
  await expect(textarea).toHaveAttribute('placeholder', 'Message...');

  await textarea.fill('/new t');
  await expect(page.locator('#message-ghost:visible')).toBeVisible();
  const ghost = await assertComposerClearsPicker(page);
  expect(Math.abs(ghost.textareaWidth - ghost.ghostWidth)).toBeLessThan(1);
  await textarea.press('Tab');
  await expect(textarea).toHaveValue('/new test/model');

  await textarea.fill('one\ntwo\nthree\nfour\nfive\nsix');
  await expect.poll(async () => (await composerMetrics(page)).textareaHeight).toBeGreaterThan(24);
  const expanded = await composerMetrics(page);
  expect(expanded.textareaHeight).toBeGreaterThan(ghost.textareaHeight);
  expect(expanded.textareaHeight).toBeLessThanOrEqual(200);

  await page.locator('.chat-input-bar:visible').evaluate(bar => { bar.style.width = '250px'; });
  await expect(page.locator('#model-picker-wrap:visible')).toHaveCount(0);
  await expect.poll(async () => (await composerMetrics(page)).paddingRight).toBe(0);
  await expect(textarea).toHaveAttribute('placeholder', 'Message...');
});

test('touch layout keeps picker clearance without responsive keyboard flicker', async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
  });
  const page = await context.newPage();
  await mockApp(page);
  await page.goto('/static/index.html');

  await page.locator('#model-picker-label:visible').evaluate(label => {
    label.textContent = 'Jarvis Mobile';
  });
  await page.locator('#message:visible').fill('Mobile text must stay clear of the picker too.');
  await assertComposerClearsPicker(page);
  await expect(page.locator('#message:visible')).toHaveAttribute('placeholder', 'Message Pandamonium...');

  await page.locator('#model-picker-btn:visible').click();
  await expect(page.locator('#model-picker-menu:visible')).toBeVisible();
  await page.locator('#message:visible').press('Escape');
  await context.close();
});

test('composer reserves the injected Compare eval picker width', async ({ page }) => {
  await mockApp(page);
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto('/static/index.html');

  await page.locator('.chat-input-bar:visible').evaluate(bar => { bar.style.width = '520px'; });
  const textarea = page.locator('#message:visible');
  await textarea.fill(
    'Compare prompts must stay clear of the replacement picker and reflow to the correct measured height when that control is wider.'
  );
  const initialHeight = await textarea.evaluate(ta => ta.getBoundingClientRect().height);
  await page.locator('.chat-input-top:visible').evaluate(inputTop => {
    inputTop.querySelector('#model-picker-wrap').style.display = 'none';
    const comparePicker = document.createElement('div');
    comparePicker.className = 'cmp-eval-wrap';
    comparePicker.style.width = '240px';
    comparePicker.style.height = '28px';
    inputTop.appendChild(comparePicker);
  });

  await expect.poll(async () => page.locator('.chat-input-top:visible').evaluate(inputTop => {
    const ta = inputTop.querySelector('#message');
    return parseFloat(getComputedStyle(ta).paddingRight) || 0;
  })).toBeGreaterThanOrEqual(248);
  await expect.poll(async () => textarea.evaluate(ta => ta.getBoundingClientRect().height))
    .toBeGreaterThan(initialHeight);
  const clearsCompare = await page.locator('.chat-input-top:visible').evaluate(inputTop => {
    const ta = inputTop.querySelector('#message');
    const picker = inputTop.querySelector('.cmp-eval-wrap');
    const paddingRight = parseFloat(getComputedStyle(ta).paddingRight) || 0;
    return ta.getBoundingClientRect().right - paddingRight <= picker.getBoundingClientRect().left - 7;
  });
  expect(clearsCompare).toBe(true);

  await page.locator('.chat-input-top:visible').evaluate(inputTop => {
    inputTop.querySelector('.cmp-eval-wrap').remove();
    inputTop.querySelector('#model-picker-wrap').style.display = '';
  });
  await assertComposerClearsPicker(page);
});

test('completed code fences preserve structured Qwen role markers', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');

  const result = await page.evaluate(async () => {
    const { stripToolBlocks } = await import('/static/js/chatRenderer.js');
    return stripToolBlocks([
      '<|assistant|> outside marker is stripped',
      '```text',
      '<|system|>',
      '<|user|>',
      '<|assistant|>',
      '<|end|>',
      '```',
      '<|end|> trailing marker is stripped',
    ].join('\n'));
  });

  expect(result).toContain('```text\n<|system|>\n<|user|>\n<|assistant|>\n<|end|>\n```');
  expect(result).not.toContain('<|assistant|> outside');
  expect(result).not.toContain('<|end|> trailing');
});

async function renderDiagram(page, source) {
  return page.evaluate(async definition => {
    const chatRenderer = await import('/static/js/chatRenderer.js');
    const chatHistory = document.querySelector('#chat-history');
    chatHistory.replaceChildren();
    const message = chatRenderer.addMessage(
      'assistant',
      `\`\`\`mermaid\n${definition}\n\`\`\``,
      'test/model',
      {},
    );
    const frame = message.querySelector('.mermaid-container');
    await new Promise((resolve, reject) => {
      const deadline = Date.now() + 5_000;
      const check = () => {
        if (frame.dataset.mermaidState === 'rendered' || frame.dataset.mermaidState === 'error') {
          resolve();
        } else if (Date.now() >= deadline) {
          reject(new Error(`Mermaid stayed ${frame.dataset.mermaidState}`));
        } else {
          setTimeout(check, 20);
        }
      };
      check();
    });
    return {
      state: frame.dataset.mermaidState,
      hasSvg: Boolean(frame.querySelector('svg')),
      sourceHidden: frame.querySelector('.mermaid-source').hidden,
      source: frame.querySelector('.mermaid-source').textContent,
      errorCount: frame.querySelectorAll('.mermaid-error').length,
      visualText: frame.querySelector('.mermaid-visual').textContent,
    };
  }, source);
}

test('Mermaid retries unsafe generated labels once and retains fallback source', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  await page.waitForFunction(() => Boolean(window.mermaid));

  const valid = 'flowchart LR\n  Start[Start] --> Done[Done]';
  const validResult = await renderDiagram(page, valid);
  expect(validResult).toMatchObject({ state: 'rendered', hasSvg: true, sourceHidden: true });

  await page.evaluate(() => {
    const originalRender = window.mermaid.render.bind(window.mermaid);
    window.__madMermaidRenderAttempts = [];
    window.mermaid.render = (...args) => {
      window.__madMermaidRenderAttempts.push(args[1]);
      return originalRender(...args);
    };
  });

  const generated = [
    'graph TD',
    '  Internet[Internet] -->|WAN| Router[Home Router<br/>(Wi‑Fi + Ethernet)]',
    '  Router --> WiFi[Wi‑Fi Devices — phones, tablets]',
    '  Router --> Wired[Ethernet devices (NAS/server)]',
    '  Router --> DB[(Inventory)]',
    '  Router --> Input[/Input queue/]',
    '  Router --> Output[\\Output queue\\]',
    '  Router --> Batch[/Batch window\\]',
    '  Router --> Archive[\\Archive window/]',
    '  subgraph Tailscale VPN',
    '    WiFi --> Tail[Tailscale nodes]',
    '  end',
  ].join('\n');
  const repairedResult = await renderDiagram(page, generated);
  expect(repairedResult).toMatchObject({ state: 'rendered', hasSvg: true, sourceHidden: true });
  expect(repairedResult.visualText).toContain('Home Router');
  expect(repairedResult.visualText).toContain('Wi‑Fi + Ethernet');
  const renderAttempts = await page.evaluate(() => window.__madMermaidRenderAttempts);
  expect(renderAttempts).toHaveLength(2);
  expect(renderAttempts[1]).toContain('DB[(Inventory)]');
  expect(renderAttempts[1]).toContain('Input[/Input queue/]');
  expect(renderAttempts[1]).toContain('Output[\\Output queue\\]');
  expect(renderAttempts[1]).toContain('Batch[/Batch window\\]');
  expect(renderAttempts[1]).toContain('Archive[\\Archive window/]');

  const invalid = 'flowchart LR\n  Start[Still broken -->';
  const invalidResult = await renderDiagram(page, invalid);
  expect(invalidResult).toMatchObject({
    state: 'error',
    hasSvg: false,
    sourceHidden: false,
    source: invalid,
    errorCount: 1,
  });
});
