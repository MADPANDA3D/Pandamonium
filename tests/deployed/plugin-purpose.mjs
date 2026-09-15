// Real authenticated app, native Git intake and Skills admission; disposable owner/data.
// Run from the repository: node tests/deployed/plugin-purpose.mjs
import { chromium, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';

const data = await mkdtemp(join(tmpdir(), 'mad961-plugin-ui-'));
const output = process.env.PLUGIN_UI_PROOF_DIR ? resolve(process.env.PLUGIN_UI_PROOF_DIR) : await mkdtemp(join(tmpdir(), 'pandamonium-plugin-proof-'));
await mkdir(output, { recursive: true });
const reservation = createServer();
await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
const port = reservation.address().port;
await new Promise(resolve => reservation.close(resolve));
const base = `http://127.0.0.1:${port}`;
const child = spawn(resolve('.venv/bin/python'), ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'], {
  env: { ...process.env, AUTH_ENABLED: 'true', SECURE_COOKIES: 'false', ODYSSEUS_DATA_DIR: data, ODYSSEUS_EXTENSIONS_DIR: join(data, 'runtime-extensions'), DATABASE_URL: `sqlite:///${data}/app.db`, ODYSSEUS_ORACLE_URL: '', PYTHON_DOTENV_DISABLED: '1' },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let log = '';
child.stdout.on('data', chunk => { log = (log + chunk).slice(-20000); });
child.stderr.on('data', chunk => { log = (log + chunk).slice(-20000); });
let browser;
try {
  await expect.poll(async () => {
    if (child.exitCode !== null) throw new Error('Disposable app exited before startup.');
    try { return (await fetch(`${base}/api/auth/status`)).status; } catch { return 0; }
  }, { timeout: 60000 }).toBe(200);
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const credentials = { username: 'plugin-ui-proof', password: randomUUID() + randomUUID() };
  expect((await context.request.post(`${base}/api/auth/setup`, { data: credentials })).ok()).toBeTruthy();
  expect((await context.request.post(`${base}/api/auth/login`, { data: credentials })).ok()).toBeTruthy();
  const page = await context.newPage();
  page.on('pageerror', error => console.error('Browser error: ' + error.message));
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: "Don't show this at startup" }).click({ timeout: 60000 });
  await page.getByRole('button', { name: 'Browse plugins' }).click();
  await page.getByRole('tab', { name: 'Add a new plugin' }).click();
  await page.locator('#marketplace-source-url').fill('https://github.com/dietrichgebert/ponytail');
  await page.getByRole('button', { name: 'Scan repository' }).click();
  await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'complete', { timeout: 120000 });
  const scanId = await page.evaluate(() => sessionStorage.getItem('pandamonium-intake-scan'));
  const scan = await (await context.request.get(`${base}/api/extensions/scans/${scanId}`)).json();
  const artifact = scan.artifact;
  const id = artifact.draft_manifest.extension_id;
  const summary = artifact.plugin.summary;
  expect(summary.length).toBeGreaterThan(30);
  expect(summary).not.toMatch(/^[>|]/);
  await expect(page.locator('#marketplace-scan-results .marketplace-product-heading')).toContainText(summary);
  await expect(page.locator('#marketplace-scan-results [data-readiness="needs_setup"]')).toBeVisible();
  await page.getByRole('button', { name: 'Install plugin…' }).click();
  await page.getByRole('button', { name: 'Approve once', exact: true }).click();
  await expect(page.locator('#marketplace-summary')).toContainText('Install completed.', { timeout: 60000 });
  await page.getByRole('tab', { name: 'Installed plugins' }).click();
  const card = page.locator(`[data-installed-id="${id}"]`);
  await expect(card).toContainText(summary);
  await expect(card.locator('[data-readiness="ready"]')).toBeVisible();
  expect(await card.evaluate(node => node.scrollHeight <= node.clientHeight + 1)).toBeTruthy();
  await page.screenshot({ animations: 'disabled', path: join(output, 'mad961-cards-desktop.png') });
  await card.press('Enter');
  const detail = page.locator('#marketplace-installed-detail');
  await expect(detail).toBeFocused();
  await expect(detail).toContainText(summary);
  await expect(detail).toContainText('Try asking');
  await expect(detail).toContainText('Memory → Skills');
  await expect(detail.locator('.marketplace-technical')).not.toHaveAttribute('open');
  const installed = await (await context.request.get(`${base}/api/extensions/installed/${id}`)).json();
  const skills = await (await context.request.get(`${base}/api/skills`)).json();
  for (const item of installed.capability_summaries) expect(skills.skills.some(skill => skill.name === item.name)).toBeTruthy();
  await page.screenshot({ animations: 'disabled', path: join(output, 'mad961-installed-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await detail.evaluate(node => node.scrollWidth > node.clientWidth + 1);
  expect(overflow).toBeFalsy();
  await detail.locator('[data-readiness="ready"]').scrollIntoViewIfNeeded();
  await page.screenshot({ animations: 'disabled', path: join(output, 'mad961-installed-mobile.png') });
  await page.getByRole('button', { name: '← Back to installed' }).click();
  await expect(card).toBeFocused();
  await page.getByRole('tab', { name: 'Marketplace' }).click();
  await page.locator(`[data-plugin-id="${id}"]`).click();
  await expect(page.locator('#marketplace-detail')).toContainText(summary);
  await expect(page.locator('#marketplace-detail [data-readiness="ready"]')).toBeVisible();
  await page.getByRole('tab', { name: 'Installed plugins' }).click();
  await card.click();
  await page.getByRole('button', { name: 'Disable', exact: true }).click();
  await page.getByRole('button', { name: 'Approve disable once' }).click();
  await expect(detail.locator('[data-readiness="disabled"]')).toBeVisible();
  await page.getByRole('button', { name: 'Enable', exact: true }).click();
  await page.getByRole('button', { name: 'Approve enable once' }).click();
  await expect(detail.locator('[data-readiness="ready"]')).toBeVisible();
  await page.getByRole('button', { name: 'Remove', exact: true }).click();
  await page.getByRole('button', { name: 'Approve remove once' }).click();
  await expect(page.locator('#marketplace-installed-list')).toContainText('No plugins installed');
  const remaining = await (await context.request.get(`${base}/api/extensions/installed`)).json();
  expect(remaining.plugins).toEqual([]);
  await writeFile(join(output, 'mad961-plugin-ui.json'), JSON.stringify({ source: artifact.source_url, revision: artifact.source_revision, package: artifact.package, summary, capabilities: installed.capability_summaries, skill_count: skills.count, checks: ['authenticated native URL scan', 'package metadata parity', 'native install and Skills admission', 'keyboard detail and focus return', 'desktop/mobile no overflow', 'marketplace parity', 'disable/enable/remove'], remaining_plugins: 0 }, null, 2) + '\n');
  console.log('Disposable browser proof passed; source revision ' + artifact.source_revision + '; evidence ' + output);
} catch (error) {
  // Local-only diagnostics; never commit account/session logs.
  await writeFile(join(output, 'failure.log'), log);
  console.error('Disposable app failure log: ' + join(output, 'failure.log'));
  throw error;
} finally {
  await browser?.close();
  child.kill('SIGTERM');
  await new Promise(resolve => { if (child.exitCode !== null) return resolve(); child.once('exit', resolve); setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000).unref(); });
  await rm(data, { recursive: true, force: true });
}
