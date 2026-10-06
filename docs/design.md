# Local scientific artifact retrieval

The model searches, receives explicit paper and artifact choices, and downloads selected artifacts to a user-granted directory on the same computer. Start with PMC main-article PDF and JATS. Other providers and supplements are separate future adapters.

## Boundaries

Pure domain functions normalize identifiers, rank candidates, validate selection claims, compare metadata fingerprints, and make rights and retry decisions. Application services coordinate provider and filesystem ports. Thin MCP tools map typed inputs and results. No candidate map or conversational state is held between calls.

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
