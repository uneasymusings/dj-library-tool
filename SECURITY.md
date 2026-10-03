# Security and privacy

## Scope

`djlib` is a trusted-user local utility. Its coordinator binds only to `127.0.0.1`, uses a private bearer token, rejects unexpected browser origins, and validates its runtime endpoint before client discovery. CLI and MCP clients share the same workspace authority. It is not a multi-user remote service or a substitute for host assistant execution permissions.

Source paths resolve symlinks against configured roots. Downloads use fixed output names, an optional isolated yt-dlp subprocess, and bounded staging/time/output. Initial URLs are restricted to public HTTPS provider domains. Provider redirect/CDN handling is delegated to yt-dlp; this is not a hardened network sandbox. Do not expose the coordinator to the network or run untrusted users' acquisitions on a shared host.

This release never writes native DJ databases, USB devices, embedded audio tags, or unrelated originals. An archive profile copies media into the workspace. Token/runtime files are private on POSIX; Windows filesystem ACL isolation still needs validation. A process running as the same OS user can access the workspace and its authority.

## Local data

The catalog and manifests contain local file paths, labels, hashes, and source URLs. Logs may contain diagnostic paths. MCP responses send requested catalog results to your chosen assistant host; that host's data handling applies. The engine has no telemetry or model service connection. Web acquisition contacts the selected source providers.

Keep workspaces outside the source checkout. The repository ignores common media, database, environment, and log files, but `.gitignore` is not a privacy boundary. Inspect what you commit. No credential is required for the synthetic demo.

## Reporting

Use this repository's GitHub private vulnerability reporting if enabled, or contact the maintainer privately through GitHub before disclosing exploit details. Do not post personal catalog dumps, service tokens, account credentials, or unredacted logs in a public issue.

Version 0.1.0a1 is experimental and has no security audit or runtime compatibility certification. Current security-sensitive gaps are documented in [architecture](docs/ARCHITECTURE.md) and [status](docs/STATUS.md).
