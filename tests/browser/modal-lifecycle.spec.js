import { expect, test } from '@playwright/test';


async function openHarness(page, viewport = { width: 1200, height: 760 }) {
  await page.setViewportSize(viewport);
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/sessions' || path === '/api/models' || path === '/api/model-endpoints') {
      return route.fulfill({ json: [] });
    }
    if (path === '/api/notes') return route.fulfill({ json: { notes: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => {
    window.__modalHarness = await import('/static/js/modalManager.js');
  });
}


test('the declarative default-dock table covers every MAD-850 tool', async ({ page }) => {
  await openHarness(page);
  const docks = await page.evaluate(() => window.__modalHarness.DECLARED_DEFAULT_DOCKS);
  expect(docks).toEqual({
    'mad-mcp-modal': 'right',
    'memory-modal': 'right',
    'calendar-modal': 'right',
    'compare-model-overlay': 'right',
    'cookbook-modal': 'right',
    'research-overlay': 'right',
    'gallery-modal': 'right',
    'doclib-modal': 'right',
    'notes-panel': 'right',
    'tasks-modal': 'right',
    'marketplace-modal': 'right',
  });
});


test('actual Plugins and Compare openers register their production modal IDs', async ({ page }) => {
  await openHarness(page);

  await page.locator('#add-plugins-btn').click();
  const plugins = page.locator('#marketplace-modal');
  await expect(plugins).toBeVisible();
  await expect(plugins).toHaveClass(/modal-right-docked/);
  await expect.poll(() => page.evaluate(() => window.__modalHarness.isRegistered('marketplace-modal'))).toBe(true);

  await page.evaluate(async () => {
    const compare = await import('/static/js/compare/selector.js');
    window.__compareSelectorPromise = compare.showModelSelector();
  });
  const compare = page.locator('#compare-model-overlay');
  await expect(compare).toBeVisible();
  await expect(compare).toHaveClass(/modal-right-docked/);
  await expect.poll(() => page.evaluate(() => window.__modalHarness.isRegistered('compare-model-overlay'))).toBe(true);
  await expect(plugins).toHaveClass(/modal-minimized/);
  await expect(page.locator('.modal-right-docked:not(.hidden):not(.modal-minimized)')).toHaveCount(1);
  await compare.locator('.close-btn').click();
});


test('declared tools default right and same-side switching minimizes without teardown', async ({ page }) => {
  await openHarness(page);

  await page.evaluate(() => {
    const manager = window.__modalHarness;
    window.__dockCloseCount = 0;
    const portal = document.getElementById('mad-mcp-modal');
    portal.classList.remove('hidden');
    portal.style.display = '';
    manager.register('mad-mcp-modal', {
      closeFn: () => { window.__dockCloseCount += 1; },
      restoreFn: () => {},
    });
  });

  const portal = page.locator('#mad-mcp-modal');
  await expect(portal).toHaveClass(/modal-right-docked/);
  await expect(page.locator('body')).toHaveClass(/right-dock-active/);

  await page.evaluate(() => {
    const manager = window.__modalHarness;
    const brain = document.getElementById('memory-modal');
    brain.classList.remove('hidden');
    brain.style.display = '';
    manager.register('memory-modal', { closeFn: () => {}, restoreFn: () => {} });
  });

  const brain = page.locator('#memory-modal');
  await expect(brain).toHaveClass(/modal-right-docked/);
  await expect(portal).toHaveClass(/modal-minimized/);
  await expect(portal).toHaveClass(/hidden/);
  await expect(page.locator('.modal-right-docked:not(.hidden):not(.modal-minimized)')).toHaveCount(1);
  await expect(page.locator('.minimized-dock-chip[data-modal-id="mad-mcp-modal"]')).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => window.__dockCloseCount)).toBe(0);

  await page.evaluate(() => window.__modalHarness.restore('mad-mcp-modal'));
  await expect(portal).toBeVisible();
  await expect(portal).toHaveClass(/modal-right-docked/);
  await expect(brain).toHaveClass(/modal-minimized/);

  const idsBeforeClose = await page.evaluate(() => ({
    portal: document.getElementById('mad-mcp-modal'),
    brain: document.getElementById('memory-modal'),
  }));
  expect(idsBeforeClose.portal).toBeTruthy();
  expect(idsBeforeClose.brain).toBeTruthy();

  await page.evaluate(() => window.__modalHarness.close('mad-mcp-modal'));
  await expect.poll(() => page.evaluate(() => window.__dockCloseCount)).toBe(1);
  await expect(portal).toHaveClass(/hidden/);
  await expect.poll(() => page.evaluate(() => window.__modalHarness.isRegistered('mad-mcp-modal'))).toBe(false);
});


test('declared default dock does not override a user undock or side move', async ({ page }) => {
  await openHarness(page);
  await page.evaluate(() => {
    const modal = document.getElementById('mad-mcp-modal');
    modal.classList.remove('hidden');
    modal.style.display = '';
    window.__modalHarness.register('mad-mcp-modal', { closeFn: () => {}, restoreFn: () => {} });
  });

  const portal = page.locator('#mad-mcp-modal');
  await expect(portal).toHaveClass(/modal-right-docked/);
  await page.evaluate(async () => {
    const modal = document.getElementById('mad-mcp-modal');
    const snap = await import('/static/js/modalSnap.js');
    snap.clearRightDock(modal);
    document.body.appendChild(document.createElement('i')).remove();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await expect(portal).not.toHaveClass(/modal-right-docked|modal-left-docked/);

  await page.evaluate(async () => {
    const modal = document.getElementById('mad-mcp-modal');
    const snap = await import('/static/js/modalSnap.js');
    snap.applyEdgeDock(modal, 'left');
    document.body.appendChild(document.createElement('i')).remove();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
  await expect(portal).toHaveClass(/modal-left-docked/);
  await expect(portal).not.toHaveClass(/modal-right-docked/);
});


test('existing modal-minimize button is bound and restores through the shared lifecycle', async ({ page }) => {
  await openHarness(page);

  await page.evaluate(() => {
    const overlay = document.createElement('div');
    overlay.id = 'research-overlay';
    overlay.className = 'modal';
    overlay.innerHTML = `
      <div class="modal-content">
        <div class="modal-header">
          <h4>Deep Research</h4>
          <button class="modal-minimize-btn" type="button">_</button>
          <button class="close-btn" type="button">x</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    window.__modalHarness.register('research-overlay', { closeFn: () => {}, restoreFn: () => {} });
    window.__modalHarness.injectMinimizeButton(overlay, 'research-overlay');
  });

  const overlay = page.locator('#research-overlay');
  const minimize = overlay.locator('.modal-minimize-btn');
  await expect(minimize).toHaveAttribute('data-_modals-bound', '1');
  await minimize.click();
  await expect(overlay).toHaveClass(/modal-minimized/);
  await expect(overlay).toHaveClass(/hidden/);

  await page.evaluate(() => window.__modalHarness.restore('research-overlay'));
  await expect(overlay).toBeVisible();
  await expect(overlay).toHaveClass(/modal-right-docked/);
  await expect.poll(() => page.evaluate(() => window.__modalHarness.focus('research-overlay'))).toBe(true);
});


test('Notes minimize and immediate restore never duplicate its virtual pane', async ({ page }) => {
  await openHarness(page);
  await page.evaluate(async () => {
    window.__notesHarness = await import('/static/js/notes.js');
    window.__notesHarness.openPanel();
  });

  await expect(page.locator('#notes-pane')).toHaveCount(1);
  await expect(page.locator('#notes-pane')).toHaveClass(/modal-right-docked/);
  await page.evaluate(() => window.__modalHarness.minimize('notes-panel'));
  await expect(page.locator('#notes-pane')).toHaveCount(0);
  await expect(page.locator('.minimized-dock-chip[data-modal-id="notes-panel"]')).toHaveCount(1);

  await page.evaluate(() => window.__modalHarness.restore('notes-panel'));
  await expect(page.locator('#notes-pane')).toHaveCount(1);
  await expect(page.locator('#notes-pane')).toHaveClass(/modal-right-docked/);
  await page.waitForTimeout(260);
  await expect(page.locator('#notes-pane')).toHaveCount(1);
  await expect(page.locator('#notes-pane-backdrop')).toHaveCount(1);
});


test('right dock tracks a short dynamic viewport and resizes canonically', async ({ page }) => {
  await openHarness(page, { width: 1024, height: 420 });
  await page.evaluate(() => {
    const modal = document.getElementById('mad-mcp-modal');
    modal.classList.remove('hidden');
    modal.style.display = '';
    window.__modalHarness.register('mad-mcp-modal', { closeFn: () => {}, restoreFn: () => {} });
  });

  const content = page.locator('#mad-mcp-modal .modal-content');
  await expect.poll(() => content.evaluate(node => Math.round(node.getBoundingClientRect().height))).toBeLessThanOrEqual(420);
  await expect.poll(() => content.evaluate(node => node.style.height.endsWith('px'))).toBe(true);
  await expect.poll(() => content.evaluate(node => node.style.height.includes('vh'))).toBe(false);

  await page.setViewportSize({ width: 1024, height: 340 });
  await expect.poll(() => content.evaluate(node => Math.round(node.getBoundingClientRect().height))).toBeLessThanOrEqual(340);
  await expect.poll(() => content.evaluate(node => parseInt(node.style.height, 10))).toBeLessThanOrEqual(340);
});


test('Gallery dock and detail image stay contained in a short viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 420 });
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/fixture.png') {
      return route.fulfill({
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64'),
      });
    }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/gallery/library') {
      return route.fulfill({ json: {
        items: [{
          id: 'fixture-image', filename: 'fixture.png', url: '/fixture.png',
          prompt: 'Contained fixture', caption: '', model: 'imported',
          favorite: false, tags: '', ai_tags: '', width: 1600, height: 900,
          created_at: '2026-09-08T12:00:00Z',
        }],
        total: 1, total_tagged: 0, tags: [], models: ['imported'],
      } });
    }
    if (url.pathname === '/api/gallery/albums') return route.fulfill({ json: { albums: [] } });
    if (url.pathname === '/api/gallery/stats') return route.fulfill({ json: {} });
    if (url.pathname === '/api/sessions' || url.pathname === '/api/models' || url.pathname === '/api/model-endpoints') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/gallery.js')).openGallery());

  const gallery = page.locator('#gallery-modal');
  await expect(gallery).toBeVisible();
  await expect(gallery).toHaveClass(/modal-right-docked/);
  await expect(gallery.locator('.gallery-card[data-id="fixture-image"]')).toBeVisible();
  await gallery.locator('.gallery-card[data-id="fixture-image"]').click();
  const image = gallery.locator('#gallery-detail-img');
  await expect(image).toBeVisible();

  const geometry = await page.evaluate(() => {
    const content = document.querySelector('#gallery-modal .modal-content').getBoundingClientRect();
    const imageNode = document.getElementById('gallery-detail-img');
    const imageRect = imageNode.getBoundingClientRect();
    return {
      viewportBottom: (window.visualViewport?.offsetTop || 0) + (window.visualViewport?.height || window.innerHeight),
      contentBottom: content.bottom,
      imageBottom: imageRect.bottom,
      objectFit: getComputedStyle(imageNode).objectFit,
    };
  });
  expect(geometry.contentBottom).toBeLessThanOrEqual(geometry.viewportBottom + 1);
  expect(geometry.imageBottom).toBeLessThanOrEqual(geometry.contentBottom + 1);
  expect(geometry.objectFit).toBe('contain');
});


test('declared default docking is disabled on mobile', async ({ page }) => {
  await openHarness(page, { width: 390, height: 844 });
  const result = await page.evaluate(async () => {
    const modal = document.getElementById('mad-mcp-modal');
    modal.classList.remove('hidden');
    modal.style.display = '';
    window.__modalHarness.register('mad-mcp-modal', { closeFn: () => {}, restoreFn: () => {} });
    const snap = await import('/static/js/modalSnap.js');
    return {
      available: snap.edgeDockAvailable(),
      forcedWidth: snap.applyEdgeDock(modal, 'right'),
      docked: modal.classList.contains('modal-right-docked'),
      bodyDocked: document.body.classList.contains('right-dock-active'),
    };
  });

  expect(result).toEqual({ available: false, forcedWidth: 0, docked: false, bodyDocked: false });
});
