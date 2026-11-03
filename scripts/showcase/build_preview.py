"""Publish the real review-page frontend with an explicitly read-only demo transport.

Inputs are the isolated, generated-tone captures. No user catalog or credentials are read.
"""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "src/djlib/interfaces/web"
PUBLIC = ROOT / "docs/preview"

ADAPTER = r""""use strict";
// Public demo transport only. app.js is the production frontend with auto-start deferred.
(async () => {
  const response = await fetch("catalog.json", {cache: "no-store"});
  const catalog = await response.json();
  readHandoff = () => { state.token = "public-demo-only"; state.workspace = null; };
  api = async (path, {method = "GET", params = {}} = {}) => {
    if (method !== "GET") {
      throw new ApiError("DEMO_READ_ONLY",
        "This public preview does not download or change files. " +
        "Run djlib ui in your terminal to use this action with your music.", 409);
    }
    if (!(path in catalog)) {
      throw new ApiError("NOT_FOUND", "This item is not in the demo library.", 404);
    }
    const reply = structuredClone(catalog[path]);
    if (path === "/library" && params.query) {
      const query = String(params.query).toLowerCase();
      reply.tracks = reply.tracks.filter(track =>
        [track.artist, track.title, track.version].join(" ").toLowerCase().includes(query));
      reply.total = reply.tracks.length;
    }
    return reply;
  };
  const originalErrorNotice = errorNotice;
  errorNotice = error => error.code === "DEMO_READ_ONLY"
    ? notice("info", error.message) : originalErrorNotice(error);
  await start();
})().catch(error => {
  document.getElementById("view").textContent = "The demo could not load. Please reload the page.";
  console.error(error);
});
"""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def frontend():
    source = (WEB / "app.js").read_text()
    assert source.endswith("\nstart();\n"), "Review startup changed; update the demo adapter."
    script = source.removesuffix("\nstart();\n") + "\n"
    page = (WEB / "index.html").read_text()
    page = page.replace("<title>djlib</title>", "<title>djlib / public demo</title>")
    page = page.replace(
        '<main id="view"',
        '<aside class="demo-notice">PUBLIC DEMO · generated library · '
        "illustrative BPM/key · downloads and device actions require the local app</aside>\n"
        '<main id="view"',
    )
    page = page.replace(
        '<script src="app.js" defer></script>',
        '<script src="app.js" defer></script>\n<script src="demo.js" defer></script>',
    )
    # Pages caches static files independently; keep every published UI update coherent.
    assets = {
        "app.js": script.encode(),
        "app.css": (WEB / "app.css").read_bytes(),
        "demo.js": ADAPTER.encode(),
    }
    for name, value in assets.items():
        page = page.replace(f'"{name}"', f'"{name}?v={digest(value)[:12]}"')
    return {
        "index.html": page.encode(),
        **assets,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refresh-ui", action="store_true")
    args = parser.parse_args()
    files = frontend()
    if args.check:
        for name, value in files.items():
            assert (PUBLIC / name).read_bytes() == value, f"Regenerate public frontend: {name}"
        print("Public preview matches the production frontend; demo transport is read-only.")
        return
    if args.refresh_ui:
        assert (PUBLIC / "catalog.json").is_file(), "Build the demo catalog first."
        for name, value in files.items():
            (PUBLIC / name).write_bytes(value)
        files["catalog.json"] = (PUBLIC / "catalog.json").read_bytes()
        manifest = json.loads((PUBLIC / "manifest.json").read_text())
        manifest["files"] = {name: digest(value) for name, value in files.items()}
        (PUBLIC / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print("Updated public frontend; retained the checked demo catalog.")
        return
    if not args.captures:
        parser.error("--captures is required when generating")
    catalog, lists = {}, {"/requests": [], "/collections": [], "/library": []}
    specs = json.loads((Path(__file__).parent / "sets.json").read_text())
    for spec in specs:
        capture = json.loads((args.captures / f"{spec['id']}.json").read_text())
        for endpoint, value in capture["preview"].items():
            if endpoint in lists:
                key = {
                    "/requests": "requests",
                    "/collections": "collections",
                    "/library": "tracks",
                }[endpoint]
                lists[endpoint] += value[key]
            else:
                catalog[endpoint] = value
    for endpoint, values in lists.items():
        key = {"/requests": "requests", "/collections": "collections", "/library": "tracks"}[
            endpoint
        ]
        catalog[endpoint] = {key: values, "total": len(values), "next_cursor": None}

    # These paths belong only to the generated fixtures; publish no workstation paths.
    def sanitize(value):
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        if isinstance(value, dict):
            return {key: sanitize(v) for key, v in value.items()}
        if isinstance(value, str) and (
            value.startswith("/private/") or value.startswith("/Users/")
        ):
            return "/demo/" + Path(value).name
        return value

    files["catalog.json"] = (json.dumps(sanitize(catalog), indent=2) + "\n").encode()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        (PUBLIC / name).write_bytes(value)
    (PUBLIC.parent / ".nojekyll").touch()
    (PUBLIC / "manifest.json").write_text(
        json.dumps(
            {
                "files": {name: digest(value) for name, value in files.items()},
                "frontend": "production source; only auto-start is deferred",
                "data": "generated original-tone fixtures",
                "writes": "disabled",
            },
            indent=2,
        )
        + "\n"
    )
    print("Built public preview from five real CLI/API captures; all write actions are disabled.")


if __name__ == "__main__":
    main()
