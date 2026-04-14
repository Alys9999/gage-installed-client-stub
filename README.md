# GAGE Local Codex Proxy Service

This repository provides a minimal HTTP service for GAGE installed-client workflows.
It proxies `POST /run` requests to a local `codex` executable and returns the result
through the installed-client contract.

## Features

- `GET /healthz` readiness endpoint
- `POST /run` installed-client endpoint
- Optional bearer-token auth
- Local Codex CLI proxy mode

## Run

```bash
cd /home/amyjx/work/gage-installed-client-stub
./run_local_codex_proxy.sh
```

Equivalent explicit command:

```bash
cd /home/amyjx/work/gage-installed-client-stub
python -m stub_installed_client_service.server --host 127.0.0.1 --port 8787
```

Use a custom executable path:

```bash
CODEX_EXECUTABLE=/path/to/codex ./run_local_codex_proxy.sh
```

Optional auth:

```bash
export STUB_CLIENT_TOKEN=secret-token
python -m stub_installed_client_service.server --host 127.0.0.1 --port 8787
```

## Connect GAGE

Point GAGE at the service:

```bash
export GAGE_CODEX_CLIENT_URL=http://127.0.0.1:8787
```

If auth is enabled:

```bash
export GAGE_CODEX_CLIENT_TOKEN=secret-token
```

Then run your installed-client workflow or the 8-flow script.

## Contract

The stub follows the installed-client request/response contract currently documented in:

- `/home/amyjx/work/GAGE/docs/installed_client_service_contract.md`

## Notes

- This service requires a working local `codex` executable.
- It does not require GAGE benchmark images to have `codex` installed.
- GAGE only needs the service URL; the proxy owns the local Codex invocation.
