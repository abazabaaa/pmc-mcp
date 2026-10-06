# Local scientific artifact retrieval

The model searches, receives explicit paper and artifact choices, and downloads selected artifacts to a user-granted directory on the same computer. Start with PMC main-article PDF and JATS. Other providers and supplements are separate future adapters.

## Boundaries

Pure domain functions normalize identifiers, validate selection claims, compare metadata fingerprints, and make rights and retry decisions. Application services coordinate provider and filesystem ports. Thin MCP tools map typed inputs and results. No candidate map or conversational state is held between calls.

A work, a deposited expression, an artifact, and a local filename are distinct identities. Selection tokens carry an exact PMC expression and artifact fingerprint across calls and restarts. Metadata changes require another choice. Retraction, manuscript status, and license evidence remain visible in choices and receipts.

## Delivery

The human configures allowed output roots locally. Tool arguments cannot expand those grants. Files are streamed into exclusive same-directory temporary files, checked for format and MD5, then published atomically without overwriting existing files. SHA-256 and provenance identify retrieved bytes. Receipts have explicit recovery semantics because a file and its sidecar are separate commits.

## Protocol

The implementation baseline is MCP Python SDK 2.3.0 and protocol revision 2026-07-28. Request-scoped streaming preserves the modern cancellation path; legacy sessionless calls may finish after the client stops waiting. Server deadlines and recoverable file publication apply to both modes. Actual host interoperability requires its own acceptance check.

## Milestones

1. Project tooling, privacy checks, and pure domain contracts with negative controls.
2. PMC metadata and content adapters, bounded network behavior, safe local publication, and application services.
3. Typed MCP tools and CLI, real loopback protocol tests, restart and failure recovery, user setup documentation.

Offline tests use synthetic content, injected clocks and transports, and temporary folders. Live provider smoke checks are opt-in. A checksum match proves agreement with source metadata, not scientific validity or permission for every downstream use.

## Primary references

- [MCP Python SDK 2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [SDK cancellation](https://py.sdk.modelcontextprotocol.io/handlers/cancellation/)
- [PMC Cloud Service](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)

## Concrete de-risking actions

| Risk | Concrete action and acceptance gate | Evidence or next step |
| --- | --- | --- |
| Documentation drift | Compare protocol and SDK release metadata with the local archive; refresh official transport/cancellation/provider pages using Firecrawl, retaining source URLs and hashes locally. Pin the implementation dependency lock. | Reviewed and refreshed October 6, 2026; SDK 2.3.0 and July protocol baseline. Refresh research is excluded from the public distribution. |
| Hidden conversational state | Keep all selection identity and metadata in signed, bounded tokens; search in process A, download in B, restart, and retry. | Automated independent HTTP-process test; no candidate map or external state store. Installation signing keys remain local configuration. |
| Thin tools hiding domain policy | Keep immutable values and identifier, selection, license, source-key, retry, and change checks in the domain; inject provider and destination ports into application services. | Domain negative controls and direct CLI use exercise the same functions as MCP. Provider relevance order is retained; no invented ranking confidence. |
| Wrong paper, expression, or role | Preserve an explicit suffix, expose available expressions and tri-state flags, reject mismatched IDs, supplemental keys, and changed digests/status. | Synthetic provider fixtures; live explicit-version PDF/JATS smoke. PDF identity relies on exact source metadata/path and checksum; parsing alone cannot identify a paper. |
| Lost response or concurrent writes | Stage in the chosen granted directory, verify bytes, publish with no-clobber linking, and reconcile receipts on retry. Inject cancellation, checksum errors, file collisions, and directory replacement. | Filesystem tests verify read-back, preservation, cleanup, and committed-byte warnings. A file plus its receipt is not one transaction. |
| Client-dependent cancellation | Test actual loopback HTTP, assert negotiated revisions and no session IDs, cancel during a staged transfer, and observe final-file behavior. | Modern SDK client cancels and cleans up; forced legacy handler completes after Stop. Both are tested in separate server processes. |
| Host compatibility | Register the local endpoint with the intended desktop host using its private bearer configuration. List tools, choose a token, write to a granted folder, reject an ungranted folder, and record protocol/cancellation behavior. | Installed Claude Code 2.1.292 and Codex CLI 0.160.1 plugins passed actual search, fresh PDF, retry, and denied-folder calls over Streamable HTTP. File/receipt read-back agreed. Interactive desktop UI and host-specific cancellation remain unverified; see [host evidence](plugins.md). |
| Provider coverage | Build a manually checked corpus of about ten works, including manuscript, multiple expressions, missing PDF, and status/rights exclusions where available. Check title/citation and main-article identity; retain source timestamps and hashes without publishing local paths. | **Pending broad qualification.** One public work's PDF/JATS and retry were verified live. Frozen fixtures cover gaps and exclusions. |
| Expansion risk | Add TXT, supplements, or another provider only behind an adapter with explicit role, rights, version, network, and rate rules. Repeat the identity and publication gates before exposing it through tools. | Deferred until the local path and intended host are accepted. No generic URL fetcher or waterfall is exposed. |

Start remaining qualification with the small real corpus, followed by interactive desktop and host-specific cancellation checks. If either exposes a mismatch, fix the responsible domain rule or adapter and add a focused regression case before adding providers. Multiple workers require a shared upstream rate limiter and a new concurrency gate; the initial service is one process.
