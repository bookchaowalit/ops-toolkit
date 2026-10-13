# Running the core quality gate

Run this workflow from the operations checkout. It verifies local code and
synthetic browser journeys; a passing result is not production release approval,
hosted Google OIDC acceptance, or business UAT.

## Select the owning repositories

The gate's stable target IDs differ from the renamed repository folders. Supply
the supported overrides rather than renaming target IDs or creating duplicate
checkouts. From this repository:

```bash
export SOLO_OPS_ROOT="$PWD"
export SOLO_MCP_ROOT="$PWD/../platform-mcp-aggregator"
export SOLO_PLATFORM_ROOT="$PWD/../platform-empire"
export SOLO_CELL_KIT_ROOT="$PWD/../solo-cell-kit"
export SOLO_WEB_ROOT="$PWD/../life-dashboard-web"
```

Different checkout names are supported by setting these variables to their
actual locations. No path back to the registry hub is needed.

## Test prerequisites

- Node dependencies in the MCP and web repositories; Playwright Chromium and
  its Linux shared libraries for the web journeys.
- The platform and cell-kit development Python environments, including their
  disposable PostgreSQL test dependency.
- ImageMagick **7** (`magick`), FFmpeg, and FFprobe on `PATH`. The art pipeline
  exercises real image/video rendering; missing tools block the strict gate.
- An installed Python copy of the sibling service-auth package for the web's
  Python/TypeScript authorization parity test:

```bash
uv venv .runtime/quality/auth-venv --python 3.12
uv pip install --python .runtime/quality/auth-venv/bin/python ../platform-auth-sdk
export SERVICE_AUTH_PYTHON="$PWD/.runtime/quality/auth-venv/bin/python"
```

The operations suite also needs its declared Python development dependencies,
including Markdown, pypdf and the Python Playwright adapter used by offline
docpack regression fixtures. Keep them in an isolated test environment:

```bash
uv venv .runtime/quality/ops-venv --python 3.12 --system-site-packages
uv pip install --python .runtime/quality/ops-venv/bin/python -r infra/requirements-dev.txt
export PATH="$PWD/.runtime/quality/ops-venv/bin:$PATH"
```

The platform/cell-kit use their own development environments; the web's
authorization parity interpreter remains the separate `SERVICE_AUTH_PYTHON`.

On this Ubuntu 24.04 workstation, the missing media tools and browser libraries
were prepared in the ignored `.runtime/quality/` directory without administrator
access. Reuse them with:

```bash
export PATH="$PWD/.runtime/quality/toolchain/bin:$PATH"
export LD_LIBRARY_PATH="$PWD/.runtime/quality/system-libs/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

The media downloads came from the [official ImageMagick download page](https://imagemagick.org/download/)
and the Linux build publisher linked by the [official FFmpeg download page](https://ffmpeg.org/download.html).
Their SHA-256 hashes were checked against the release asset digests before use.
The local `.runtime/quality/toolchain/manifest.json` records the downloaded URLs
and hashes. Its ImageMagick wrapper registers the installed DejaVu Sans font as
the `sans` alias used by the English synthetic overlay test; the alias points
to a real font and rendering is exercised. Thai font coverage is not established
by this fixture. Browser libraries came from Ubuntu's configured package archive and
were extracted with `dpkg-deb`; no system packages or shell profiles were changed.
These prepared files are machine-local prerequisites, not tracked dependencies.
A fresh workstation must install the same required tools and browser libraries;
the gate performs no installation itself.

## Run and interpret the evidence

```bash
task quality:core
```

The report is `.runtime/quality/core-latest.json`. Accept the complete local gate
only when `scope` is `core-local`, `full_core_gate` is `true`, and all five targets
passed at unchanged checkout fingerprints. A subset, skip, missing dependency,
timeout, or checkout change is not a full pass. Fix the reported cause and rerun
the affected check; do not waive it or add retries solely to obtain green output.

The cell kit has two explicitly named resource-specific not-applicable cases;
they remain visible in its report and are not missing-dependency exceptions.

The added portfolio manifest acceptance test requires three explicit inputs:
`--manifest`, `--commercial-catalog` and `--clone-root`. The standard operations
runner currently does not forward those arguments. Without them that script
reports partial coverage and blocks the complete core gate. Do not fabricate
inputs, remove its case or report a green core result. A separately invoked
manifest test is separate evidence until the runner has a supported way to pass
the declared inputs; checking every portfolio product also requires its own scope.

For a release candidate, also run `task quality:core -- --require-clean` after
all intended changes have been reviewed and committed. Other sessions' dirty
files must remain intact; local fingerprint-bound evidence from a shared dirty
checkout is not proof of a clean, reproducible release candidate.

Hosted OIDC, production smoke, and business acceptance require separate evidence
and the appropriate approval. The report deliberately keeps `release_ready`
false. Retain the final report and the platform recovery rehearsal evidence as
distinct artifacts; local PostgreSQL restore does not prove Neon recovery.
