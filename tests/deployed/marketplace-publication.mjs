// Real GitHub publication plus a fresh authenticated app, all owner state disposable.
// PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE is required on the publisher host only.
// MAD-965 owns the release bump; this source smoke uses its package floor 1.0.68.
import { chromium, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:net';

const output = process.env.PLUGIN_UI_PROOF_DIR ? resolve(process.env.PLUGIN_UI_PROOF_DIR) : await mkdtemp(join(tmpdir(), 'mad962-proof-'));
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const children = [];
const directories = [];
async function instance(publisher) {
  const data = await mkdtemp(join(tmpdir(), 'mad962-app-'));
  directories.push(data);
  const reservation = createServer();
  await new Promise(resolve => reservation.listen(0, '127.0.0.1', resolve));
  const port = reservation.address().port;
  await new Promise(resolve => reservation.close(resolve));
  const env = { ...process.env, AUTH_ENABLED: 'true', SECURE_COOKIES: 'false', ODYSSEUS_DATA_DIR: data, ODYSSEUS_EXTENSIONS_DIR: join(data, 'extensions'), DATABASE_URL: `sqlite:///${data}/app.db`, ODYSSEUS_ORACLE_URL: '', PYTHON_DOTENV_DISABLED: '1' };
  if (!publisher) delete env.PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE;
  const launch = 'import routes.extension_routes; routes.extension_routes.APP_VERSION="1.0.68"; import uvicorn; uvicorn.run("app:app", host="127.0.0.1", port=' + port + ', log_level="warning")';
  const child = spawn(resolve('.venv/bin/python'), ['-c', launch], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  children.push(child);
  let log = '';
  child.stdout.on('data', b => { log = (log + b).slice(-20000); });
  child.stderr.on('data', b => { log = (log + b).slice(-20000); });
  const base = `http://127.0.0.1:${port}`;
  await expect.poll(async () => {
    if (child.exitCode !== null) { await writeFile(join(output, 'app-failure.log'), log); throw new Error('Disposable app failed'); }
    try { return (await fetch(base + '/api/auth/status')).status; } catch { return 0; }
  }, { timeout: 60000 }).toBe(200);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const credentials = { username: 'marketplace-proof', password: randomUUID() + randomUUID() };
  expect((await context.request.post(base + '/api/auth/setup', { data: credentials })).ok()).toBeTruthy();
  expect((await context.request.post(base + '/api/auth/login', { data: credentials })).ok()).toBeTruthy();
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: "Don't show this at startup" }).click({ timeout: 60000 });
  await page.getByRole('button', { name: 'Browse plugins' }).click();
  return { page, context, base, child };
}
try {
  if (process.argv.includes('--inventory')) {
    const inventory = JSON.parse(await readFile('docs/mad-963-inventory.json', 'utf8'));
    const consumer = await instance(false);
    await consumer.page.getByRole('tab', { name: 'Marketplace' }).click();
    await consumer.page.getByRole('button', { name: 'Refresh catalog', exact: true }).click();
    await expect(consumer.page.getByRole('button', { name: 'Refresh catalog', exact: true })).toBeEnabled({ timeout: 30000 });
    const catalog = await (await consumer.context.request.get(consumer.base + '/api/extensions/marketplace')).json();
    await writeFile(join(output, 'mad963-discovery.json'), JSON.stringify(catalog, null, 2));
    const packages = inventory.entries.flatMap(e => e.packages).filter(p => p.publication.state === 'published');
    for (const item of packages) {
      expect(catalog.plugins.some(p => p.id === item.extension_id)).toBeTruthy();
    }
    await consumer.page.screenshot({ animations: 'disabled', path: join(output, 'mad963-discovery.png') });
    const installs = [];
    for (const id of ['superpowers', 'step-parts']) {
      await consumer.page.getByRole('tab', { name: 'Marketplace' }).click();
      await consumer.page.locator(`[data-plugin-id="${id}"]`).click();
      const detail = consumer.page.locator('#marketplace-detail');
      await expect(detail).toContainText('Catalog + artifact verified');
      await detail.getByRole('button', { name: 'Install', exact: true }).click();
      await detail.getByRole('button', { name: 'Approve once', exact: true }).click();
      await expect(consumer.page.locator('#marketplace-summary')).toContainText('Install completed', { timeout: 120000 });
      const registry = await (await consumer.context.request.get(consumer.base + '/api/extensions')).json();
      const digest = packages.find(p => p.extension_id === id).publication.digest;
      expect(registry.extensions[id].active_revision).toBe(digest);
      let skillCount = 0;
      if (id === 'superpowers') {
        skillCount = (await (await consumer.context.request.get(consumer.base + '/api/skills')).json()).count;
        expect(skillCount).toBe(14);
      }
      installs.push({ extension_id: id, installed_digest: digest, native_skills: skillCount });
      await consumer.page.getByRole('tab', { name: 'Installed plugins' }).click();
      await consumer.page.locator(`[data-installed-id="${id}"]`).click();
      await expect(consumer.page.getByRole('button', { name: 'Remove', exact: true })).toBeVisible();
      await consumer.page.screenshot({ animations: 'disabled', path: join(output, `mad963-${id}-installed.png`) });
      await consumer.page.getByRole('button', { name: 'Remove', exact: true }).click();
      await consumer.page.getByRole('button', { name: 'Approve remove once' }).click();
      await expect(consumer.page.locator('#marketplace-installed-list')).toContainText('No plugins installed');
    }
    await writeFile(join(output, 'mad963-consumer.json'), JSON.stringify({ discovered: packages.map(p => p.extension_id), installs, remaining_plugins: 0, compatibility_test_version: '1.0.68', publisher_key_available: false }, null, 2) + '\n');
    console.log('Inventory discovery and fresh skill/API install passed: ' + output);
  } else {
  const producer = await instance(true);
  await producer.page.getByRole('tab', { name: 'Add a new plugin' }).click();
  await producer.page.locator('#marketplace-source-url').fill('https://github.com/dietrichgebert/ponytail');
  await producer.page.getByRole('button', { name: 'Scan repository' }).click();
  await expect(producer.page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'complete', { timeout: 120000 });
  const scanId = await producer.page.evaluate(() => sessionStorage.getItem('pandamonium-intake-scan'));
  const scan = await (await producer.context.request.get(producer.base + '/api/extensions/scans/' + scanId)).json();
  const artifact = scan.artifact;
  const id = artifact.draft_manifest.extension_id;
  const responsePromise = producer.page.waitForResponse(r => r.url().endsWith(`/scans/${scanId}/publish`));
  await producer.page.getByRole('button', { name: 'Add to marketplace', exact: true }).click();
  const job = await (await responsePromise).json();
  let receipt;
  await expect.poll(async () => {
    receipt = await (await producer.context.request.get(producer.base + '/api/extensions/publications/' + job.id)).json();
    if (receipt.state === 'failed') throw new Error(receipt.message);
    return receipt.state;
  }, { timeout: 180000 }).toBe('published');
  await expect(producer.page.locator('#marketplace-scan-results')).toContainText('Published — the tested package', { timeout: 10000 });
  expect((await (await producer.context.request.get(producer.base + '/api/extensions/installed')).json()).plugins).toEqual([]);
  await producer.page.screenshot({ animations: 'disabled', path: join(output, 'mad962-published.png') });
  producer.child.kill('SIGTERM');
  await new Promise(resolve => producer.child.once('exit', resolve));
  await producer.context.close();

  const consumer = await instance(false);
  await consumer.page.getByRole('tab', { name: 'Marketplace' }).click();
  await consumer.page.getByRole('button', { name: 'Refresh catalog', exact: true }).click();
  await expect(consumer.page.getByRole('button', { name: 'Refresh catalog', exact: true })).toBeEnabled({ timeout: 30000 });
  const discovered = await (await consumer.context.request.get(consumer.base + '/api/extensions/marketplace')).json();
  await writeFile(join(output, 'discovery.json'), JSON.stringify(discovered, null, 2));
  await consumer.page.screenshot({ animations: 'disabled', path: join(output, 'discovery.png') });
  await consumer.page.locator(`[data-plugin-id="${id}"]`).click({ timeout: 30000 });
  const detail = consumer.page.locator('#marketplace-detail');
  await expect(detail).toContainText('Catalog + artifact verified');
  await detail.getByRole('button', { name: 'Install', exact: true }).click();
  await detail.getByRole('button', { name: 'Approve once', exact: true }).click();
  await expect(consumer.page.locator('#marketplace-summary')).toContainText('Install completed', { timeout: 60000 });
  await consumer.page.getByRole('tab', { name: 'Installed plugins' }).click();
  await consumer.page.locator(`[data-installed-id="${id}"]`).click();
  const installed = await (await consumer.context.request.get(consumer.base + '/api/extensions')).json();
  const record = installed.extensions[id];
  expect(record.active_revision).toBe(receipt.digest);
  const skills = await (await consumer.context.request.get(consumer.base + '/api/skills')).json();
  expect(skills.count).toBeGreaterThan(0);
  await consumer.page.screenshot({ animations: 'disabled', path: join(output, 'mad962-fresh-install.png') });
  await consumer.page.setViewportSize({ width: 390, height: 844 });
  await consumer.page.screenshot({ animations: 'disabled', path: join(output, 'mad962-mobile.png') });
  await consumer.page.getByRole('button', { name: 'Remove', exact: true }).click();
  await consumer.page.getByRole('button', { name: 'Approve remove once' }).click();
  await expect(consumer.page.locator('#marketplace-installed-list')).toContainText('No plugins installed');
  await writeFile(join(output, 'mad962-publication.json'), JSON.stringify({ receipt, source: artifact.source_url, revision: artifact.source_revision, prepared_digest: artifact.package.sha256, installed_digest: record.active_revision, native_skills: skills.count, publisher_account_installs: 0, remaining_plugins: 0, compatibility_test_version: '1.0.68', checks: ['real authenticated developer action', 'real GitHub release and signed catalog CAS', 'fresh app with bundled public keys and no publisher key', 'exact tested archive installation', 'native Skills admission', 'desktop/mobile', 'removal and disposable cleanup'] }, null, 2) + '\n');
  console.log('Publication and fresh-install proof passed: ' + output);
  }
} finally {
  await browser.close();
  for (const child of children) {
    if (child.exitCode !== null) continue;
    child.kill('SIGTERM');
    await new Promise(resolve => { child.once('exit', resolve); setTimeout(() => { child.kill('SIGKILL'); resolve(); }, 5000).unref(); });
  }
  for (const data of directories) await rm(data, { recursive: true, force: true });
}
