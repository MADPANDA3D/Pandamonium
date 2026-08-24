#!/usr/bin/env python3
"""Small read-only LAN dashboard for the local FreeToken runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOST = os.getenv("FREETOKEN_UI_HOST", "127.0.0.1")
PORT = int(os.getenv("FREETOKEN_UI_PORT", "1921"))
UPSTREAM = os.getenv("FREETOKEN_API_URL", "http://127.0.0.1:1919").rstrip("/")
READ_ENDPOINTS = {
    "health": "/health",
    "models": "/v1/models",
    "stats": "/v1/stats",
    "cache": "/v1/cache/status",
    "requests": "/v1/requests?limit=20",
}


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>FreeToken Runtime</title>
  <style>
    :root{color-scheme:dark;--bg:#05070c;--panel:#0b111b;--line:#18344a;--cyan:#28e8ff;--muted:#7793a5;--ok:#5dffb1;--warn:#ffbe55}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#10253a 0,transparent 30%),var(--bg);color:#d9f8ff;font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}
    main{max-width:1440px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;gap:16px;align-items:end;margin-bottom:24px}h1{margin:0;color:var(--cyan);font-size:25px;letter-spacing:.08em}.sub,.muted{color:var(--muted)}#status{padding:7px 12px;border:1px solid var(--line);border-radius:999px}.ok{color:var(--ok)}.bad{color:#ff6978}
    .grid{display:grid;grid-template-columns:repeat(4,minmax(190px,1fr));gap:14px}.card{background:linear-gradient(145deg,#0d1722dd,#080d15ee);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:0 12px 35px #0007}.wide{grid-column:span 2}.full{grid-column:1/-1}h2{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:0 0 13px}.value{font-size:23px;color:var(--cyan)}.kv{display:grid;grid-template-columns:1fr auto;gap:7px}.bar{height:7px;background:#101c28;border-radius:9px;overflow:hidden;margin-top:10px}.bar i{display:block;height:100%;background:linear-gradient(90deg,#277bff,var(--cyan));width:0}table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:8px;border-bottom:1px solid #142535}th{color:var(--muted)}code{color:#9ff6ff;word-break:break-all}@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.wide{grid-column:span 2}}@media(max-width:560px){main{padding:16px}.grid{grid-template-columns:1fr}.wide,.full{grid-column:span 1}.top{align-items:start;flex-direction:column}}
  </style>
</head>
<body><main>
  <div class="top"><div><h1>FREETOKEN RUNTIME</h1><div class="sub">Jarvis · local inference telemetry · read-only</div></div><div id="status">Connecting…</div></div>
  <section class="grid">
    <article class="card"><h2>Model</h2><div class="value" id="model">—</div><div class="muted" id="version">—</div></article>
    <article class="card"><h2>Runtime</h2><div class="value" id="uptime">—</div><div class="muted" id="maintenance">—</div></article>
    <article class="card"><h2>Decode</h2><div class="value"><span id="decode">—</span> tok/s</div><div class="muted">Prefill <span id="prefill">—</span> tok/s</div></article>
    <article class="card"><h2>Requests</h2><div class="value"><span id="active">—</span> active</div><div class="muted"><span id="completed">—</span> completed</div></article>
    <article class="card wide"><h2>VRAM</h2><div class="value" id="vram">—</div><div class="muted">FreeToken engine allocation</div></article>
    <article class="card wide"><h2>Context caches</h2><div class="kv"><span>KV</span><span id="kv">—</span><span>SWA</span><span id="swa">—</span><span>MoE expert slots</span><span id="moe">—</span></div><div class="bar"><i id="kvbar"></i></div></article>
    <article class="card wide"><h2>Latency</h2><div class="kv"><span>Mean time to first token</span><span id="ttft">—</span><span>P95 request</span><span id="p95">—</span></div></article>
    <article class="card wide"><h2>Checkpoint</h2><code id="checkpoint">—</code><div class="muted" id="context">—</div></article>
    <article class="card full"><h2>Recent requests</h2><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Route</th><th>Status</th><th>Duration</th><th>TTFT</th><th>Tokens</th></tr></thead><tbody id="requestRows"></tbody></table></div></article>
  </section>
</main><script>
const $=id=>document.getElementById(id), fmtMs=v=>v==null?'—':`${(v/1000).toFixed(2)} s`, ratio=(u,t)=>t?Math.min(100,u/t*100):0;
function uptime(s){s=Number(s||0);const d=Math.floor(s/86400),h=Math.floor(s%86400/3600),m=Math.floor(s%3600/60);return `${d}d ${h}h ${m}m`}
function setText(id,v){$(id).textContent=v??'—'}
async function refresh(){try{const r=await fetch('/api/runtime',{cache:'no-store'}),d=await r.json();if(!r.ok)throw new Error(d.error||r.statusText);const h=d.health||{},s=d.stats||{},c=d.cache||{},models=d.models?.data||[],m=models[0]||{},q=s.requests||{},kv=s.kv||{},swa=s.swa||{},g=c.geometry||{};
  $('status').className='ok';setText('status',`${h.status||'ok'} · refreshed ${new Date().toLocaleTimeString()}`);setText('model',h.model||s.model?.id||m.id);setText('version',`FreeToken ${h.version||'—'}`);setText('uptime',uptime(h.uptime_s));setText('maintenance',h.maintenance||c.state);setText('decode',Number(s.throughput?.decode_tps||0).toFixed(1));setText('prefill',Number(s.throughput?.prefill_tps||0).toFixed(1));setText('active',q.active);setText('completed',q.completed);setText('vram',`${(Number(s.vram_bytes||0)/1073741824).toFixed(2)} GiB`);setText('kv',`${kv.used_pages||0} / ${kv.total_pages||0} tokens`);setText('swa',`${swa.used_pages||0} / ${swa.total_pages||0} tokens`);setText('moe',`${g.moe_cache_size||0} / ${g.num_experts*g.num_moe_layers||0}`);$('kvbar').style.width=`${ratio(kv.used_pages,kv.total_pages)}%`;setText('ttft',fmtMs(q.ttft_mean_ms));setText('p95',fmtMs(q.p95_ms));setText('checkpoint',m.root);setText('context',`${m.context_length||s.model?.ctx||'—'} token context · ${s.model?.attn||'—'} attention`);
  const rows=(d.requests?.entries||[]).slice().reverse();$('requestRows').replaceChildren(...rows.map(x=>{const tr=document.createElement('tr');[new Date(x.ts).toLocaleString(),x.path,x.status,fmtMs(x.duration_ms),fmtMs(x.ttft_ms),`${x.prompt_tokens||0} → ${x.completion_tokens||0}`].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td)});return tr}));
 }catch(e){$('status').className='bad';setText('status',`Unavailable · ${e.message}`)}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def fetch_json(path: str) -> object:
    with urllib.request.urlopen(f"{UPSTREAM}{path}", timeout=5) as response:
        return json.load(response)


class Handler(BaseHTTPRequestHandler):
    server_version = "FreeTokenRuntimeUI/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def send_body(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            self.send_body(200, "text/html; charset=utf-8", HTML.encode())
            return
        if self.path == "/healthz":
            self.send_body(200, "application/json", b'{"ok":true}')
            return
        if self.path != "/api/runtime":
            self.send_body(404, "application/json", b'{"error":"not_found"}')
            return
        try:
            payload = {name: fetch_json(path) for name, path in READ_ENDPOINTS.items()}
            self.send_body(200, "application/json", json.dumps(payload).encode())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            body = json.dumps({"error": f"FreeToken API unavailable: {type(exc).__name__}"}).encode()
            self.send_body(502, "application/json", body)

    def do_POST(self) -> None:
        self.send_body(405, "application/json", b'{"error":"read_only"}')


def self_check() -> None:
    assert set(READ_ENDPOINTS.values()) == {
        "/health", "/v1/models", "/v1/stats", "/v1/cache/status", "/v1/requests?limit=20",
    }
    assert "/v1/admin" not in HTML
    assert "FREETOKEN RUNTIME" in HTML


if __name__ == "__main__":
    self_check()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
