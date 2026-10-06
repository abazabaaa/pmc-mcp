# Local HTTP plugins

The plugin uses Streamable HTTP at `http://127.0.0.1:47836/mcp`. It does not use an MCP stdio subprocess. A local HTTP service still needs to run; bundled setup and connection helpers manage that service separately from model conversations.

## Claude Code

From this checkout, with uv and Claude Code installed:

```sh
mkdir -p downloads
./scripts/install-claude.sh "$PWD/downloads"
```

This prepares the locked Python runtime outside the plugin directory, registers the local marketplace, and installs `pmc-mcp@pmc-local` at user scope with an existing absolute download folder. Start a fresh Claude session. The `/pmc-mcp:retrieve` skill explains search, explicit choices, and downloads. Native `/plugin configure pmc-mcp@pmc-local` can change the folder later.

Claude's `.claude-plugin/plugin.json` declares a required directory option. `.mcp.json` sends that human-owned value in `X-PMCMCP-Output-Root`; the helper supplies bearer authentication. The folder is never interpolated into a shell command. Download arguments must stay within the root carried by that request. Ordinary CLI `serve --allow-output-root` retains its separate, fixed launch grants and ignores the plugin folder header.

The connection helper is the readiness gate: it serializes startup, refuses foreign listeners, verifies authenticated installation/build identity, and reuses the existing service. A helper runs on connection/reconnection, with a ten-second host limit. Python dependencies are preinstalled by the setup script; network-dependent cold installation can exceed the helper limit, so use setup before the first session. No hook ordering is assumed and no hooks are bundled.

Validate both manifests:

```sh
claude plugin validate .claude-plugin/plugin.json --strict
claude plugin validate . --strict
```

## Codex

```sh
./scripts/install-codex.sh "$PWD/downloads"
```

This installs a public-file-only copy in local user storage, a stable `pmc-mcp-plugin-headers` helper on the local executable path, a private folder grant, and `pmc-mcp@pmc-local` through Codex's marketplace commands. Start a fresh Codex session; include the local executable directory on PATH when launching Codex from an app or a custom shell.

The package uses OpenAI's supported `.codex-plugin/plugin.json` compatibility format and a separate `codex.mcp.json`. Its native `http_headers_helper` supplies both credentials and the privately configured folder header. The compatibility format is deliberate: the current portable HTTP schema exposes literal headers and does not carry this helper setting. No credential is embedded in a manifest. This is a local supported-format package, not an OpenAI directory approval or endorsement. Public submission requires a separate review and public HTTPS infrastructure, outside this local-only project.

## Local state and lifecycle

Mutable keys, runtime, lock, folder settings, and service logs live in the private local state directory. Neither plugin caches nor source control hold credentials or machine paths. One daemon can serve both hosts. The request-scoped folder is not retained on the shared application; modern and legacy requests are tested for isolation.

An authenticated plugin client authorizes the root through its header. This is a different trust boundary from a server launched with immutable roots: a process possessing the bearer credential can provide another header. Protect the credential and human-owned host configuration. Tool arguments cannot alter that header. The service binds only loopback, rejects missing authentication and duplicate grants, and still enforces filesystem containment, symlink checks, and no-clobber publication.

A source/build mismatch fails with `service_version_conflict`. Stop the owned service before using an updated package, then reconnect. Sessions do not shut down the shared daemon. Plugin uninstall removes host registration; stop the daemon explicitly:

```sh
./scripts/bootstrap.sh stop
```

State keys are retained across stop/start. Losing the signing key invalidates earlier selections. Run one local deployment; multiple independent provider processes do not share rate limits.

## Distribution and verification

```sh
uv run python scripts/package_plugin.py
uv build
uv run python scripts/check_publication.py --archives
```

The ZIP includes an explicit list of public manifests, scripts, skills, source, and dependency files. Private research, host settings, credentials, downloaded papers, logs, and Git data are excluded. The Python wheel/sdist and plugin ZIP are all scanned.

Automated tests cover authenticated health, startup/reuse/restart/stop, strict endpoint parsing, private configuration tampering, duplicate/missing folder grants, rejection outside a grant, and overlapping modern/legacy requests with distinct folders.

Live installed-plugin acceptance completed on October 6, 2026:

| Host | Search | Fresh PDF | Retry | Outside configured folder |
| --- | --- | --- | --- | --- |
| Claude Code 2.1.292 | `ok`, actual plugin tools | `downloaded` | `already_present` | `destination_denied` |
| Codex CLI 0.160.1 | `ok`, actual plugin tools | `downloaded` | `already_present` | `destination_denied` |

Both clients retrieved explicit expression `PMC6404399.1`. Independent filesystem read-back matched each receipt: 2,920,820 bytes, SHA-256 `fc46e5cab70594aab3170dbf41e5399277b673d7a8c10c6632dd068d260ce954`. The denied destination remained empty. Raw transcripts, host settings, and machine paths are excluded from publication.

Codex's test used a temporary approval override for this plugin's download tool only; normal installation preserves the user's approval policy. These headless host tests verify the installed plugin connection and workflow, not interactive desktop UI or host-specific cancellation. Cancellation evidence remains the separate modern/legacy wire tests. The wider manually checked provider corpus remains pending.

## Current primary documentation

Reviewed October 6, 2026 by Luna xhigh mappers:

- [Claude plugin manifest and user configuration](https://code.claude.com/docs/en/plugins-reference)
- [Claude MCP and dynamic header helpers](https://code.claude.com/docs/en/mcp)
- [Claude plugin loading and dependency installation](https://code.claude.com/docs/en/plugins/loading)
- [Claude plugin CLI](https://code.claude.com/docs/en/plugins/cli-reference)
- [OpenAI plugin packaging and local marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Codex legacy MCP parser tests](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/plugin_config_tests.rs)

Host versions and mutable source references should be refreshed before claiming support for a newer release.
