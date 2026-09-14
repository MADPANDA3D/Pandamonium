const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'static/js/portalConnect.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

function fakeResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

async function main() {
  const mod = await loadModule();

  // ── the same guards the MAD MCP window already enforced ──────────────
  assert.match(mod.portalUrlIssue(''), /Portal MCP URL/);
  assert.match(mod.portalUrlIssue('portal.example.com'), /Portal MCP URL/);
  assert.equal(mod.portalUrlIssue('https://portal.example.com/api/mcp'), '');
  assert.match(mod.portalMasterKeyIssue('too-short'), /master key/i);
  assert.equal(mod.portalMasterKeyIssue('m'.repeat(20)), '');

  // ── invalid input never touches the network ──────────────────────────
  let fetches = 0;
  global.fetch = async () => { fetches += 1; return fakeResponse({}); };
  const badUrl = await mod.connectPortal({ portalUrl: 'nope', masterKey: 'm'.repeat(40) });
  assert.equal(badUrl.ok, false);
  assert.equal(badUrl.failure, 'portal_url');
  const badKey = await mod.connectPortal({ portalUrl: 'https://portal.example.com/api/mcp', masterKey: 'short' });
  assert.equal(badKey.ok, false);
  assert.equal(badKey.failure, 'master_key');
  assert.equal(fetches, 0);

  // ── success posts the proven master key + URL body ────────────────────
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return fakeResponse({ configured: true, status: 'connected', tool_count: 3 });
  };
  const connected = await mod.connectPortal({
    portalUrl: 'https://portal.example.com/api/mcp',
    masterKey: 'm'.repeat(40),
  });
  assert.equal(connected.ok, true);
  assert.equal(connected.payload.tool_count, 3);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/mcp/portal/connect');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.credentials, 'same-origin');
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.portal_url, 'https://portal.example.com/api/mcp');
  assert.equal(body.master_key, 'm'.repeat(40));

  // ── http failure copy is human, with next steps, no raw code ─────────
  global.fetch = async () => fakeResponse({ detail: 'MAD MCP rejected the key' }, false, 502);
  const failed = await mod.connectPortal({
    portalUrl: 'https://portal.example.com/api/mcp',
    masterKey: 'm'.repeat(40),
  });
  assert.equal(failed.ok, false);
  assert.equal(failed.failure, 'http_error');
  const failureCopy = mod.portalConnectFailureMessage(failed);
  assert.match(failureCopy, /couldn't connect to your Portal/i);
  assert.match(failureCopy, /try again/i);
  assert.doesNotMatch(failureCopy, /502|detail|rejected the key/);

  // ── unreachable copy stays actionable ─────────────────────────────────
  global.fetch = async () => { throw new Error('socket hang up'); };
  const unreachable = await mod.connectPortal({
    portalUrl: 'https://portal.example.com/api/mcp',
    masterKey: 'm'.repeat(40),
  });
  assert.equal(unreachable.failure, 'unreachable');
  assert.match(mod.portalConnectFailureMessage(unreachable), /couldn't reach your Portal/i);
  assert.match(mod.portalConnectFailureMessage(unreachable), /try again/i);

  // ── success copy reports the live tool count ──────────────────────────
  assert.match(mod.portalConnectSuccessMessage({ tool_count: 3 }), /3 tools/);
  assert.match(mod.portalConnectSuccessMessage({ tool_count: 1 }), /1 tool\b/);
  assert.equal(mod.portalConnectSuccessMessage({}), 'MAD MCP Portal is connected.');
}

main()
  .then(() => {
    console.log('portal connect helper: ok');
  })
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
