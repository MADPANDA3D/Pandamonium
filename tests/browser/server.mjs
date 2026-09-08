import { createServer } from 'node:http';
import { readFile, realpath } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';

const HOST = '127.0.0.1';
const PORT = 4173;
const STATIC_ROOT = await realpath(resolve('static'));
const FUTURE_WORKER_COOKIE = 'pandamonium-test-sw=future';
const CONTENT_TYPES = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webm': 'video/webm',
  '.woff2': 'font/woff2',
};

createServer(async (request, response) => {
  try {
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405).end();
      return;
    }
    const pathname = decodeURIComponent(new URL(request.url, `http://${HOST}:${PORT}`).pathname);
    if (pathname !== '/' && !pathname.startsWith('/static/')) {
      response.writeHead(404).end();
      return;
    }
    const requestedAsset = resolve(
      STATIC_ROOT,
      pathname === '/' ? 'index.html' : pathname.slice('/static/'.length),
    );
    const asset = await realpath(requestedAsset);
    if (!asset.startsWith(`${STATIC_ROOT}${sep}`)) {
      response.writeHead(403).end();
      return;
    }
    let body = await readFile(asset);
    const cookies = request.headers.cookie?.split(';').map(value => value.trim()) || [];
    if (pathname === '/static/sw.js' && cookies.includes(FUTURE_WORKER_COOKIE)) {
      body = Buffer.from(body.toString('utf8').replace('pandamonium-v392', 'pandamonium-v393'));
    }
    response.writeHead(200, {
      'Content-Type': CONTENT_TYPES[extname(asset)] || 'application/octet-stream',
      'Cache-Control': pathname === '/static/sw.js' ? 'no-cache' : 'no-store',
      'X-Content-Type-Options': 'nosniff',
    });
    response.end(request.method === 'HEAD' ? undefined : body);
  } catch {
    response.writeHead(404).end();
  }
}).listen(PORT, HOST);
