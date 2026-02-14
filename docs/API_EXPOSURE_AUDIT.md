# API Exposure Audit (2026-02-14)

## Scope

Low-risk security check for whether local API is unintentionally exposed to public networks.

## Findings

1. Runtime listener check (local machine):
   - `127.0.0.1:8000` is listening.
   - No `0.0.0.0:8000` listener detected.
2. Health check:
   - `GET http://127.0.0.1:8000/healthz` returned `{"status":"ok"}`.
3. Tunnel/process check:
   - No obvious tunnel process found (`ngrok`, `cloudflared`, `frpc`, `traefik`, etc.).
4. Script defaults:
   - `scripts/start_overnight_until_7.ps1` default `ApiBase` is `http://127.0.0.1:8000`.
   - `scripts/supervise_until.ps1` default `ApiBase` is `http://127.0.0.1:8000`.
   - CLI default `--api-base` is `http://127.0.0.1:8000`.

Conclusion:

- Current default posture is localhost-only and is **not publicly exposed by default**.
- Exposure risk appears only if operator explicitly binds to non-loopback host and/or adds a tunnel or reverse proxy.

## Risk Scenarios (When Exposure Can Happen)

1. Start Uvicorn with `--host 0.0.0.0` or external IP.
2. Pass non-localhost `-ApiBase` in launcher scripts.
3. Use tunneling services (ngrok/cloudflared) to forward local port.
4. Open firewall ingress on the listening port.

## Recommended Guardrails

1. Keep API bind target to `127.0.0.1` for local operation.
2. If remote access is required, place API behind an authenticated reverse proxy.
3. Add host allowlist or auth middleware before any public exposure.
4. Monitor listening sockets before unattended runs.

## Operator Quick Check Commands

```powershell
# 1) Listening addresses
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 8000,80,443 } |
  Select-Object LocalAddress,LocalPort,OwningProcess

# 2) Tunnel processes
Get-Process | Where-Object { $_.ProcessName -match 'ngrok|cloudflared|frpc|frps|traefik|caddy|tunnel' } |
  Select-Object ProcessName,Id,Path

# 3) Local API health
Invoke-RestMethod http://127.0.0.1:8000/healthz
```

## Low-Risk Rename Note

Current rename strategy in this repository:

1. Display brand can change (for example `AWE-AgentForge`).
2. Internal package/runtime IDs remain `awe-agentcheck` / `awe_agentcheck` to avoid breaking scripts and imports.
