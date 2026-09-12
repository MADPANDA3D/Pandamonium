// MAD-902: Pandamonium agent-workstation projects — list, hover new session,
// and the create/import add menu.
import { expect, test } from '@playwright/test';

test('Projects section lists workstation projects and starts a bound session', async ({ page }) => {
  let posted = null;
  const projects = [
    { id: 'p1', name: 'Rocket Lab', path: '/work/rocket', resolved_path: '/work/rocket', available: true, reason: '' },
    { id: 'p2', name: 'Ghost', path: '/work/gone', resolved_path: '/work/gone', available: false, reason: 'folder no longer exists' },
  ];
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/projects' && method === 'GET') {
      return route.fulfill({ json: { projects, root: '/data/projects' } });
    }
    if (url.pathname === '/api/projects' && method === 'POST') {
      posted = JSON.parse(route.request().postData() || '{}');
      const created = {
        id: 'p3', name: posted.name, path: `/data/projects/${posted.name}`,
        resolved_path: `/data/projects/${posted.name}`, available: true, reason: '',
      };
      return route.fulfill({ json: { project: created, projects: [...projects, created] } });
    }
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');

  await expect(page.locator('#projects-section')).toBeVisible();
  await expect(page.locator('.project-item')).toHaveCount(2);
  await expect(page.locator('.project-item').first()).toContainText('Rocket Lab');
  await expect(page.locator('.project-item').nth(1)).toHaveAttribute('aria-disabled', 'true');

  // The hover + binds the project workspace and starts a new session.
  const row = page.locator('.project-item').first();
  await row.hover();
  await row.locator('.project-session-btn').click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('odysseus-workspace'))).toBe('/work/rocket');

  // Unavailable projects never replace the bound workspace.
  const ghost = page.locator('.project-item').nth(1);
  await ghost.hover();
  await ghost.locator('.project-session-btn').click({ force: true });
  expect(await page.evaluate(() => localStorage.getItem('odysseus-workspace'))).toBe('/work/rocket');

  // The header + opens the add menu with both paths.
  await page.locator('#projects-add-btn').click();
  await expect(page.locator('#projects-add-menu')).toBeVisible();
  await expect(page.locator('#project-create-option')).toContainText('New project');
  await expect(page.locator('#project-import-option')).toContainText('Add existing folder');

  // New project posts the name and refreshes the list.
  await page.locator('#project-create-option').click();
  await page.locator('#styled-prompt-input').fill('Demo');
  await page.locator('#styled-prompt-ok').click();
  await expect.poll(() => posted && posted.name).toBe('Demo');
  await expect(page.locator('.project-item')).toHaveCount(3);
});
