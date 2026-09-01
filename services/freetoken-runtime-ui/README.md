# FreeToken Runtime UI

Read-only dashboard for a headless FreeToken engine. It exposes health,
model, throughput, cache occupancy, latency, and recent request telemetry. It does not
proxy generation, cache-resize, stop, or other mutating endpoints.

The service template binds to `http://127.0.0.1:1921/`. Set an explicit private
interface address in deployment configuration only when remote access is intended.
