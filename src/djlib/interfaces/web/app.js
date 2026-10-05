// djlib review page. Every catalog string is untrusted (file tags, web metadata):
// the DOM is built with createElement/textContent only, never parsed as markup.
"use strict";

const TOKEN_KEY = "djlib.token";
const WORKSPACE_KEY = "djlib.workspace";
const ACTIVE = new Set(["queued", "running"]);
const state = { token: null, workspace: null, render: 0, timer: null };

// -- access handoff ---------------------------------------------------------------------------

function readHandoff() {
  const hash = location.hash.slice(1);
  if (hash.includes("token=")) {
    const params = new URLSearchParams(hash);
    const token = params.get("token");
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    if (params.get("ws")) sessionStorage.setItem(WORKSPACE_KEY, params.get("ws"));
    else sessionStorage.removeItem(WORKSPACE_KEY);
    // Keep the token out of history, bookmarks and screenshots of the address bar.
    history.replaceState(null, "", location.pathname + "#/overview");
  }
  state.token = sessionStorage.getItem(TOKEN_KEY);
  state.workspace = sessionStorage.getItem(WORKSPACE_KEY);
}

class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function api(path, { method = "GET", body, params } = {}) {
  if (!state.token) throw new ApiError("AUTH_REQUIRED", "Missing access link.", 401);
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const headers = { Authorization: `Bearer ${state.token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      "SERVICE_UNREACHABLE",
      "The background service is not running. Run djlib ui in a terminal to start it.",
      0,
    );
  }
  let reply;
  try {
    reply = await response.json();
  } catch {
    throw new ApiError("INVALID_REPLY", "The service sent an unreadable reply.", response.status);
  }
  if (reply === null || typeof reply !== "object" || !("ok" in reply)) {
    throw new ApiError(
      "UNSUPPORTED",
      `This djlib version can't do that from the page (HTTP ${response.status}). Update djlib or use the terminal.`,
      response.status,
    );
  }
  if (!reply.ok) {
    const error = reply.error || {};
    throw new ApiError(error.code || "REQUEST_FAILED", error.message || "The request failed.", response.status);
  }
  return reply.result;
}

// -- DOM helpers ------------------------------------------------------------------------------

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function table(columns, rows, { onRow } = {}) {
  // Optional columns disappear when no row has a value, as in the terminal.
  columns = columns.filter((column) => !column.optional || rows.some((row) => column.optional(row)));
  const head = h("tr", null, columns.map((column) => h("th", { class: column.class }, column.label)));
  const body = h("tbody");
  for (const row of rows) {
    const cells = columns.map((column) => {
      const value = column.cell(row);
      return h("td", { class: column.class, "data-label": column.label || null }, value);
    });
    const tr = h("tr", onRow ? { class: "link" } : null, cells);
    if (onRow) tr.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      onRow(row);
    });
    body.append(tr);
  }
  return h("div", { class: "table-wrap" }, h("table", null, h("thead", null, head), body));
}

function empty(...lines) {
  return h("div", { class: "empty" }, lines.map((line) => h("p", null, line)));
}

function notice(tone, message, code) {
  return h("div", { class: `notice ${tone}`, role: tone === "bad" ? "alert" : "status" },
    message, code ? h("span", { class: "code" }, code) : null);
}

function pageHead(title, subtitle, ...extra) {
  return h("div", { class: "head" },
    h("div", null, h("h1", null, title), subtitle ? h("p", { class: "sub" }, subtitle) : null),
    extra);
}

function crumb(href, label) {
  return h("a", { class: "crumb", href }, label);
}

// -- formatting -------------------------------------------------------------------------------

function plural(count, word, many) {
  return `${count} ${count === 1 ? word : many || `${word}s`}`;
}

function ago(iso) {
  if (!iso) return "";
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return "";
  const seconds = Math.max(0, Math.round((Date.now() - moment.getTime()) / 1000));
  if (seconds < 45) return "just now";
  const units = [[604800, "w"], [86400, "d"], [3600, "h"], [60, "m"]];
  for (const [size, unit] of units) {
    if (seconds >= size) {
      const amount = Math.floor(seconds / size);
      if (unit === "w" && amount > 8) return moment.toLocaleDateString();
      return `${amount}${unit} ago`;
    }
  }
  return "just now";
}

function duration(seconds) {
  if (typeof seconds !== "number" || seconds < 0) return "";
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

function audioFormat(track) {
  const properties = track.properties || {};
  const path = track.path || track.last_known_path || "";
  const extension = (path.split(".").pop() || "").toUpperCase();
  const container = path.includes(".") ? extension : String(properties.codec || "").toUpperCase();
  const codec = String(properties.codec || "");
  const lossless = codec.startsWith("pcm") || codec === "flac" || codec === "alac";
  let quality = "";
  if (lossless && properties.sample_rate) {
    quality = `${properties.sample_rate / 1000}k${properties.bit_depth ? `/${properties.bit_depth}` : ""}`;
  } else if (properties.bitrate_bps) {
    quality = `${Math.round(properties.bitrate_bps / 1000)}k`;
  }
  return `${container} ${quality}`.trim();
}

function fileTail(path) {
  if (!path) return h("span", { class: "blank" }, "no location");
  const parts = path.split(/[\\/]/).filter(Boolean);
  return h("span", { class: "tail", title: path }, parts.slice(-2).join("/"));
}

function trackLabel(track) {
  return h("span", null,
    track.artist || "Unknown artist", " – ",
    h("b", null, track.title || "Untitled"),
    track.version ? h("span", { class: "muted" }, ` (${track.version})`) : null);
}

const SOURCES = {
  rekordbox_analysis: "rekordbox analysis",
  native_tag: "file tag",
  operator: "entered by you",
};

function readout(value, kind, source, verified) {
  if (value === null || value === undefined || value === "") return h("span", { class: "blank" }, "—");
  const text = kind === "bpm" ? Number(value).toFixed(Number.isInteger(Number(value)) ? 0 : 2) : value;
  const where = SOURCES[source] || source || "unknown source";
  const title = `${kind === "bpm" ? "BPM" : "Key"} from ${where}${verified ? ", verified" : ", not verified"}`;
  return h("span", { class: `readout ${kind}`, title }, text);
}

function commandText(...parts) {
  const quote = (value) => (/^[\w@%+=:,./~-]+$/.test(value) ? value : `'${value.replace(/'/g, `'"'"'`)}'`);
  const words = ["djlib"];
  if (state.workspace) words.push("--workspace", state.workspace);
  return words.concat(parts.map((part) => quote(String(part)))).join(" ");
}

function copyable(text) {
  const button = h("button", { class: "small", type: "button" }, "Copy");
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => { button.textContent = "Copy"; }, 1600);
  });
  return h("div", { class: "command" }, h("code", null, text), button);
}

// -- status vocabulary (mirrors the terminal) -------------------------------------------------

function jobBadge(job) {
  const { state: value, outcome } = job;
  if (value === "completed") {
    return outcome === "completed_with_gaps" ? chip("warn", "!", "Partial") : chip("ok", "✓", "Done");
  }
  const map = {
    queued: ["pending", "○", "Queued"],
    running: ["info running", "◐", "Running"],
    paused: ["warn", "‖", "Paused"],
    needs_attention: ["warn", "!", "Needs review"],
    failed: ["bad", "✗", "Failed"],
    cancelled: ["pending", "–", "Cancelled"],
  };
  const [tone, glyph, label] = map[value] || ["pending", "○", value || "Unknown"];
  return chip(tone, glyph, label);
}

function chip(tone, glyph, label) {
  return h("span", { class: `chip ${tone}` }, h("span", { class: "g", "aria-hidden": "true" }, glyph), label);
}

const COUNT_TONES = [
  ["succeeded", "ok", "succeeded"],
  ["failed", "bad", "failed"],
  ["skipped", "warn", "skipped"],
  ["needs_input", "warn", "need review"],
  ["running", "info", "running"],
  ["pending", "pending", "pending"],
  ["cancelled", "pending", "cancelled"],
];

function countsText(counts) {
  const parts = COUNT_TONES.filter(([key]) => (counts || {})[key])
    .map(([key, tone, label]) => h("span", { class: tone }, `${counts[key]} ${label}`));
  return parts.length ? h("span", { class: "counts" }, parts) : h("span", { class: "blank" }, "—");
}

const JOB_KINDS = {
  scan: "Scan",
  collection: "Collection",
  download: "Download",
  export: "Export",
  delivery: "Delivery prep",
  delivery_check: "Delivery check",
  organize: "Organize",
  organization: "Notes",
  reconcile: "Reconcile",
};

function jobTable(jobs) {
  return table(
    [
      { label: "State", cell: jobBadge },
      { label: "Kind", cell: (job) => JOB_KINDS[job.kind] || job.kind },
      { label: "Name", class: "clip", cell: (job) => job.name || "", optional: (job) => job.name },
      { label: "Items", cell: (job) => countsText(job.counts) },
      { label: "Updated", cell: (job) => h("span", { class: "muted" }, ago(job.updated_at)) },
      { label: "Job", cell: (job) => h("span", { class: "id" }, job.job_id) },
    ],
    jobs,
  );
}

// -- views ------------------------------------------------------------------------------------

async function overview() {
  const [library, collections, requests, deliveries, jobs] = await Promise.all([
    api("/library", { params: { limit: 1 } }),
    api("/collections", { params: { limit: 1 } }),
    api("/requests", { params: { limit: 1 } }),
    api("/deliveries", { params: { limit: 1 } }),
    api("/jobs", { params: { limit: 8 } }),
  ]);
  const n = (value) => h("span", { class: "n" }, value);
  const view = h("section");
  view.append(h("h1", null, "Overview"));
  if (!library.total) {
    view.append(
      h("p", { class: "sub" }, "Nothing indexed yet."),
      empty("Index a music folder from a terminal, then come back here:", copyable(commandText("scan"))),
    );
  } else {
    view.append(h("p", { class: "summary" },
      "Your library has ", n(library.total), library.total === 1 ? " track" : " tracks",
      " in ", n(collections.total), collections.total === 1 ? " collection" : " collections",
      ". You have ", n(requests.total), requests.total === 1 ? " request list" : " request lists",
      " and ", n(deliveries.total), deliveries.total === 1 ? " delivery." : " deliveries."));
  }
  view.append(h("h2", null, "Recent jobs"));
  view.append(jobs.jobs.length ? jobTable(jobs.jobs) : empty("No jobs yet."));
  if (jobs.jobs.some((job) => ACTIVE.has(job.state))) refreshSoon();
  return view;
}

function libraryTable(tracks) {
  return table(
    [
      { label: "Artist", class: "clip", cell: (t) => t.artist },
      { label: "Title", class: "clip title", cell: (t) => t.title },
      { label: "Version", class: "clip muted", cell: (t) => t.version || "", optional: (t) => t.version },
      { label: "BPM", class: "num", cell: (t) => readout(t.dj?.bpm, "bpm", t.dj?.bpm_source, t.dj?.bpm_verified) },
      { label: "Key", class: "num", cell: (t) => readout(t.dj?.key, "key", t.dj?.key_source, t.dj?.key_verified) },
      { label: "Time", class: "num", cell: (t) => duration(t.properties?.duration_seconds) },
      { label: "Format", class: "muted", cell: audioFormat },
      { label: "File", class: "file", cell: (t) => fileTail(t.path || t.last_known_path) },
    ],
    tracks,
  );
}

async function library() {
  const view = h("section");
  const input = h("input", {
    class: "search",
    type: "search",
    placeholder: "Search artist, title or version",
    "aria-label": "Search the library",
    autocomplete: "off",
  });
  const results = h("div");
  view.append(pageHead("Library", "Every word must match; case and accents are ignored.", input), results);
  let pending = 0;

  async function load(query) {
    const run = ++pending;
    const tracks = [];
    let after = null;
    let total = 0;
    async function page() {
      const reply = await api("/library", { params: { query, limit: 50, after } });
      if (run !== pending) return;
      tracks.push(...reply.tracks);
      total = reply.total;
      after = reply.next_cursor;
      results.replaceChildren(...[
        tracks.length ? libraryTable(tracks) : empty(query ? `No tracks match “${query}”.` : "Your library is empty."),
        tracks.length ? h("p", { class: "muted more" }, `${tracks.length} of ${plural(total, "track")}`) : null,
        after ? h("button", { type: "button", class: "more", onclick: () => page().catch(show) }, "Load more") : null,
      ].filter(Boolean));
    }
    const show = (error) => results.replaceChildren(errorNotice(error));
    await page().catch(show);
  }

  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(input.value.trim()), 220);
  });
  await load("");
  return view;
}

function listView({ title, subtitle, path, key, columns, emptyText, href }) {
  return async () => {
    const reply = await api(path, { params: { limit: 100 } });
    const rows = reply[key];
    const view = h("section", null, pageHead(title, subtitle(reply.total)));
    view.append(rows.length ? table(columns, rows, { onRow: (row) => { location.hash = href(row); } }) : emptyText());
    return view;
  };
}

const requestsList = listView({
  title: "Requests",
  subtitle: (total) => `${plural(total, "saved list")} of songs you want.`,
  path: "/requests",
  key: "requests",
  href: (row) => `#/requests/${encodeURIComponent(row.request_id)}`,
  emptyText: () => empty(
    "No request lists yet.",
    "Ask your assistant to save the songs you want, or create a list from a terminal with djlib requests create.",
  ),
  columns: [
    { label: "Name", class: "title", cell: (row) => h("a", { href: `#/requests/${encodeURIComponent(row.request_id)}` }, row.name) },
    { label: "Songs", class: "num", cell: (row) => row.total_items },
    { label: "Updated", cell: (row) => h("span", { class: "muted" }, ago(row.updated_at || row.created_at)) },
    { label: "Request", cell: (row) => h("span", { class: "id" }, row.request_id) },
  ],
});

const REQUEST_STATES = {
  satisfied: ["ok", "✓", "Owned", "owned"],
  missing: ["bad", "✗", "Missing", "missing"],
  ambiguous: ["warn", "!", "Pick a version", "to pick"],
  unknown: ["pending", "○", "Unknown ID", "unknown IDs"],
  unavailable: ["warn", "!", "File unavailable", "unavailable"],
  source_selected: ["info", "◐", "Source picked", "with a source"],
};

function requestChip(item) {
  const otherVersion = item.state === "missing" &&
    (item.candidates || []).some((candidate) => candidate.identity_match === "different_version");
  if (otherVersion) return chip("warn", "≈", "Other version owned");
  const [tone, glyph, label] = REQUEST_STATES[item.state] || ["pending", "○", item.state];
  return chip(tone, glyph, label);
}

async function requestDetail(requestId) {
  const items = [];
  let ledger = null;
  let after = 0;
  do {
    ledger = await api(`/requests/${encodeURIComponent(requestId)}`, { params: { after, limit: 100 } });
    items.push(...ledger.items);
    after = ledger.next_offset;
  } while (after !== null && after !== undefined);
  const counts = ledger.counts || {};
  const message = h("div");
  const view = h("section", null, crumb("#/requests", "Requests"));
  const build = h("button", { type: "button", class: "primary", disabled: !counts.satisfied }, "Build crate from owned tracks");
  view.append(pageHead(ledger.name, `${plural(ledger.total_items, "song")}, revision ${ledger.revision}`, build), message);

  const tally = h("p", { class: "tally" });
  for (const [stateName, [tone, , , label]] of Object.entries(REQUEST_STATES)) {
    if (counts[stateName]) tally.append(h("span", { class: tone }, h("b", null, counts[stateName]), ` ${label}`));
  }
  view.append(tally);

  build.addEventListener("click", async () => {
    build.disabled = true;
    message.replaceChildren(notice("ok", "Building the crate…"));
    try {
      let job = await api(`/requests/${encodeURIComponent(requestId)}/collection`, {
        method: "POST",
        body: { revision: ledger.revision, name: null },
      });
      job = await waitForJob(job);
      const collectionId = job.result?.collection_id;
      if (job.state === "completed" && collectionId) {
        message.replaceChildren(h("div", { class: "notice ok" },
          `Crate ready with ${plural(job.result.selected_count ?? counts.satisfied, "track")}. `,
          h("a", { href: `#/collections/${encodeURIComponent(collectionId)}` }, "Open the collection")));
      } else {
        message.replaceChildren(notice("bad", `The crate job ended as ${job.state}.`, job.job_id));
      }
    } catch (error) {
      message.replaceChildren(errorNotice(error));
    } finally {
      build.disabled = false;
    }
  });

  async function choose(item, candidate, button) {
    button.disabled = true;
    try {
      await api(`/requests/${encodeURIComponent(requestId)}/items/${encodeURIComponent(item.item_id)}`, {
        method: "POST",
        body: {
          revision: ledger.revision,
          action: "satisfy",
          recording_id: candidate.recording_id,
          asset_revision_id: candidate.asset_revision_id,
          notes: "Chosen in the djlib review page",
        },
      });
      await route();
    } catch (error) {
      if (error.code === "REQUEST_STALE") {
        await route();
        document.getElementById("view").prepend(notice("bad", "This list changed elsewhere, so it was reloaded. Choose again.", error.code));
      } else {
        message.replaceChildren(errorNotice(error));
        button.disabled = false;
      }
    }
  }

  const describe = (item) => {
    const input = item.input || {};
    if (input.kind === "unknown") {
      return h("span", null, h("span", { class: "muted" }, input.label || "Unknown"),
        input.timestamp ? h("span", { class: "muted" }, ` at ${input.timestamp}`) : null);
    }
    return trackLabel(input);
  };

  const detail = (item) => {
    if (item.accepted?.path) return fileTail(item.accepted.path);
    const candidates = item.candidates || [];
    if (item.state === "ambiguous" && candidates.length) {
      return h("ul", { class: "candidates" }, candidates.map((candidate) => {
        const button = h("button", { type: "button", class: "small" }, "Use this");
        button.addEventListener("click", () => choose(item, candidate, button));
        return h("li", null, button, fileTail(candidate.path || (candidate.locations || [])[0]),
          h("span", { class: "id" }, (candidate.sha256 || "").slice(0, 10)));
      }));
    }
    const others = candidates.filter((candidate) => candidate.identity_match === "different_version");
    if (others.length) {
      return h("span", { class: "muted" }, "You own: ", others.map((candidate) => candidate.version || candidate.title).join(", "));
    }
    if (item.source_selection?.source_url) return h("span", { class: "path" }, item.source_selection.source_url);
    return "";
  };

  view.append(table(
    [
      { label: "#", class: "num muted", cell: (item) => item.position },
      { label: "Status", cell: requestChip },
      { label: "Requested", cell: describe },
      { label: "In your library", cell: detail },
    ],
    items,
  ));
  view.append(h("p", { class: "muted more" }, "Matched by artist, title and version labels; djlib does not listen to audio."));
  return view;
}

async function waitForJob(job) {
  while (ACTIVE.has(job.state)) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    job = await api(`/jobs/${encodeURIComponent(job.job_id)}`);
  }
  return job;
}

const collectionsList = listView({
  title: "Collections",
  subtitle: (total) => `${plural(total, "collection")}. A track can sit in several without duplicating files.`,
  path: "/collections",
  key: "collections",
  href: (row) => `#/collections/${encodeURIComponent(row.collection_id)}`,
  emptyText: () => empty("No collections yet. Build one from a request list or ask your assistant."),
  columns: [
    { label: "Name", class: "title", cell: (row) => h("a", { href: `#/collections/${encodeURIComponent(row.collection_id)}` }, row.name) },
    { label: "Tracks", class: "num", cell: (row) => row.track_count },
    { label: "Created", cell: (row) => h("span", { class: "muted" }, ago(row.created_at)) },
    { label: "Collection", cell: (row) => h("span", { class: "id" }, row.collection_id) },
  ],
});

async function collectionDetail(collectionId) {
  const tracks = [];
  let after = 0;
  let page;
  do {
    page = await api(`/collections/${encodeURIComponent(collectionId)}`, { params: { after, limit: 100 } });
    tracks.push(...page.tracks);
    after = page.next_cursor;
  } while (after !== null && after !== undefined);
  const view = h("section", null, crumb("#/collections", "Collections"),
    pageHead(page.name, plural(page.track_count ?? tracks.length, "track")));
  if (!tracks.length) {
    view.append(empty("This collection has no tracks."));
    return view;
  }
  const position = new Map(tracks.map((track, index) => [track, index + 1]));
  view.append(table(
    [
      { label: "#", class: "num muted", cell: (track) => position.get(track) },
      { label: "Artist", class: "clip", cell: (t) => t.artist },
      { label: "Title", class: "clip title", cell: (t) => t.title },
      { label: "Version", class: "clip muted", cell: (t) => t.version || "", optional: (t) => t.version },
      { label: "Time", class: "num", cell: (t) => duration(t.properties?.duration_seconds) },
      { label: "Format", class: "muted", cell: audioFormat },
      { label: "File", class: "file", cell: (t) => fileTail(t.path || t.last_known_path) },
    ],
    tracks,
  ));
  view.append(h("h2", null, "Hand it to your DJ app"));
  view.append(copyable(commandText("export", collectionId)));
  return view;
}

const WORKFLOWS = {
  rekordbox_import: "rekordbox import",
  serato_import: "Serato import",
  rekordbox_usb: "rekordbox USB",
  serato_portable: "Serato portable USB",
};

const STAGE_LABELS = {
  prepare_working_copies: "Working copies prepared",
  bind_target_usb: "USB volume bound",
  imported: "Imported in the app",
  analyzed: "Analyzed in the app",
  native_exported: "Exported by the app",
  device_library_checked: "Device library checked",
  hardware_playback: "Played on hardware",
  device_audio_hash_readback: "USB audio verified",
  app_working_file_readback: "Working files verified",
};
const STAGE_EVIDENCE = { app_working_file_readback: "app_readback", device_audio_hash_readback: "readback" };
const APP_STAGES = ["prepare_working_copies", "imported", "analyzed", "app_working_file_readback"];
const USB_STAGES = [
  "prepare_working_copies", "bind_target_usb", "imported", "analyzed", "native_exported",
  "device_library_checked", "hardware_playback", "device_audio_hash_readback",
];
const NATIVE = new Set(["imported", "analyzed", "native_exported", "device_library_checked", "hardware_playback"]);

const deliveriesList = listView({
  title: "Deliveries",
  subtitle: () => "Stored observations; run a verify command for a fresh check.",
  path: "/deliveries",
  key: "deliveries",
  href: (row) => `#/deliveries/${encodeURIComponent(row.delivery_id)}`,
  emptyText: () => empty("No deliveries yet. Plan one from a collection:", copyable(commandText("delivery", "targets"))),
  columns: [
    { label: "Name", class: "title", cell: (row) => h("a", { href: `#/deliveries/${encodeURIComponent(row.delivery_id)}` }, row.name) },
    { label: "Workflow", cell: (row) => `${WORKFLOWS[row.workflow] || row.workflow} ${row.phase || ""}`.trim() },
    {
      label: "App stages",
      cell: (row) => {
        const names = row.workflow?.endsWith("_import")
          ? ["imported", "analyzed"]
          : ["imported", "analyzed", "native_exported", "device_library_checked", "hardware_playback"];
        return h("span", { class: "chip" }, names.map((name) => {
          const outcome = row[name];
          return outcome === "passed" ? h("span", { class: "ok", title: STAGE_LABELS[name] }, "✓")
            : outcome === "failed" ? h("span", { class: "bad", title: STAGE_LABELS[name] }, "✗")
            : h("span", { class: "pending", title: STAGE_LABELS[name] }, "○");
        }));
      },
    },
    { label: "Updated", cell: (row) => h("span", { class: "muted" }, ago(row.updated_at)) },
    { label: "Delivery", cell: (row) => h("span", { class: "id" }, row.delivery_id) },
  ],
});

function deliveryNext(delivery) {
  const id = delivery.delivery_id;
  const revision = String(delivery.revision);
  const step = delivery.next_step || "";
  if (step === "prepare_working_copies") return ["Prepare the working copies", commandText("delivery", "prepare", id, "--revision", revision)];
  if (step === "bind_target_usb") return ["Bind the USB volume", commandText("delivery", "bind-device", id, "/Volumes/USB", "--revision", revision)];
  if (step === "hardware_playback") return ["After playing every pilot track, record it", commandText("delivery", "observe", id, "--file", "observation.json")];
  if (NATIVE.has(step)) return [`After checking it in the app, record “${STAGE_LABELS[step].toLowerCase()}”`, commandText("delivery", "observe", id)];
  if (step === "app_working_file_readback") return ["Verify the working files", commandText("delivery", "verify-app", id, "--revision", revision)];
  if (step === "verify_app_before_use") return ["Re-check before you play", commandText("delivery", "verify-app", id, "--revision", revision)];
  if (step === "device_audio_hash_readback") return ["Verify the USB", commandText("delivery", "verify-device", id, "--revision", revision)];
  if (step === "verify_device_before_departure") return ["Re-check before you leave", commandText("delivery", "verify-device", id, "--revision", revision)];
  if (step === "eject_safely") return ["Eject the USB safely from your computer", null];
  return null;
}

async function deliveryDetail(deliveryId) {
  const delivery = await api(`/deliveries/${encodeURIComponent(deliveryId)}`);
  const request = delivery.request || {};
  const workflow = request.workflow || "";
  const evidence = delivery.evidence || {};
  const blockers = delivery.blockers || [];
  const tracks = (delivery.snapshot || {}).tracks || [];
  const appOnly = workflow.endsWith("_import");
  const app = workflow.startsWith("serato") ? "Serato" : "rekordbox";
  const subtitle = [WORKFLOWS[workflow] || workflow, request.phase, plural(tracks.length, "track"),
    request.app_version ? `${app} ${request.app_version}` : null].filter(Boolean).join(", ");
  const view = h("section", null, crumb("#/deliveries", "Deliveries"), pageHead(request.name || "Delivery", subtitle));

  const chain = h("ol", { class: "stages" });
  for (const stage of appOnly ? APP_STAGES : USB_STAGES) {
    const record = evidence[STAGE_EVIDENCE[stage] || stage] || {};
    const failed = record.outcome === "failed";
    const done = !blockers.includes(stage) && !failed;
    const when = record.observed_at || record.checked_at;
    chain.append(h("li", { class: failed ? "failed" : done ? "done" : "todo" },
      h("span", { class: "dot", "aria-hidden": "true" }, failed ? "✗" : done ? "✓" : ""),
      h("span", null, STAGE_LABELS[stage] || stage),
      done && when ? h("span", { class: "when" }, ago(when)) : null,
      failed ? h("span", { class: "bad" }, "failed") : null));
  }
  view.append(chain);

  const met = delivery.requirements_met_at_last_check;
  if (met) {
    const checked = delivery.last_app_readback_at || delivery.last_audio_readback_at;
    view.append(notice("ok", `${appOnly ? "Ready in the app" : "Ready to take out"} as of the last check, ${ago(checked)}. Based on your recorded observations plus djlib's file checks.`));
  }

  const playlists = ((delivery.preparation_job || {}).result || {}).playlists || [];
  if (NATIVE.has(delivery.next_step) && (delivery.native_steps || []).length) {
    view.append(h("h2", null, `In ${app}`));
    view.append(h("ol", { class: "steps" }, delivery.native_steps.map((step) => h("li", null, step.replace(`In ${app}: `, "")))));
  }
  if (playlists.length) {
    view.append(h("h2", null, "Playlists to import"));
    for (const playlist of playlists) {
      view.append(h("p", { class: "muted" }, `${playlist.name}, ${plural(playlist.track_count || 0, "track")}`), copyable(playlist.path));
    }
  }

  const next = deliveryNext(delivery);
  if (next) {
    view.append(h("h2", null, "Next"), h("p", null, next[0]));
    if (next[1]) view.append(copyable(next[1]));
  }
  view.append(h("h2", null, "Details"));
  view.append(h("dl", { class: "kv" },
    h("dt", null, "Delivery"), h("dd", { class: "id" }, delivery.delivery_id),
    h("dt", null, "Revision"), h("dd", null, delivery.revision),
    h("dt", null, "Audio"), h("dd", null, request.audio_mode === "preserve" ? "Original format kept" : request.audio_mode),
    h("dt", null, "Created"), h("dd", null, ago(delivery.created_at))));
  return view;
}

async function jobs() {
  const reply = await api("/jobs", { params: { limit: 50 } });
  const view = h("section", null, pageHead("Jobs", "Background work keeps running when you close this page."));
  view.append(reply.jobs.length ? jobTable(reply.jobs) : empty("No jobs yet."));
  if (reply.jobs.some((job) => ACTIVE.has(job.state))) refreshSoon();
  return view;
}

// -- shell ------------------------------------------------------------------------------------

function errorNotice(error) {
  return notice("bad", error.message || "Something went wrong.", error.code);
}

function locked() {
  return h("section", { class: "locked" },
    h("h1", null, "Open this page from djlib"),
    h("p", null, "This page needs the private link that includes your workspace's access token. Run this in a terminal:"),
    copyable("djlib ui"),
    h("p", { class: "muted" }, "The link only works on this computer, while djlib's background service is running."));
}

function refreshSoon() {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => route({ quiet: true }), 2000);
}

const ROUTES = [
  [/^#\/overview$/, "overview", overview],
  [/^#\/library$/, "library", library],
  [/^#\/requests$/, "requests", requestsList],
  [/^#\/requests\/([\w-]+)$/, "requests", requestDetail],
  [/^#\/collections$/, "collections", collectionsList],
  [/^#\/collections\/([\w-]+)$/, "collections", collectionDetail],
  [/^#\/deliveries$/, "deliveries", deliveriesList],
  [/^#\/deliveries\/([\w-]+)$/, "deliveries", deliveryDetail],
  [/^#\/jobs$/, "jobs", jobs],
];

async function route({ quiet = false } = {}) {
  clearTimeout(state.timer);
  const main = document.getElementById("view");
  const render = ++state.render;
  const hash = location.hash || "#/overview";
  const match = ROUTES.map(([pattern, tab, view]) => [hash.match(pattern), tab, view]).find(([found]) => found);
  const [found, tab, view] = match || [["#/overview"], "overview", overview];
  for (const link of document.querySelectorAll(".tabs a")) {
    if (link.dataset.tab === tab) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (!state.token) {
    main.replaceChildren(locked());
    return;
  }
  if (!quiet) main.replaceChildren(h("p", { class: "loading" }, "Loading…"));
  try {
    const node = await view(...found.slice(1).map(decodeURIComponent));
    if (render === state.render) main.replaceChildren(node);
  } catch (error) {
    if (render !== state.render) return;
    main.replaceChildren(error.status === 401 ? locked() : errorNotice(error));
  }
}

async function start() {
  readHandoff();
  window.addEventListener("hashchange", () => route());
  if (state.token) {
    api("/capabilities")
      .then((caps) => { document.getElementById("version").textContent = caps.application_version || ""; })
      .catch(() => {});
  }
  await route();
}

start();
