# pmc-mcp

A local scientific-artifact MCP server. Search for papers, choose an exact artifact, and save validated files into a directory you grant the process.

The implementation is being developed in stacked pull requests. It separates deterministic domain rules from provider, network, and filesystem effects. No cloud deployment, object-store cache, or external state service is required.

See [the design](docs/design.md) for the scope and acceptance gates.
