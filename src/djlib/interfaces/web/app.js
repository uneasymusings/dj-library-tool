// djlib review page. Every catalog string is untrusted (file tags, web metadata):
// the DOM is built with createElement/textContent only, never parsed as markup.
"use strict";

const TOKEN_KEY = "djlib.token";
const WORKSPACE_KEY = "djlib.workspace";
const HOME = "#/sets";
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
    history.replaceState(null, "", location.pathname + HOME);
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

// `kind` picks the phone layout: "t-set" and "t-lib" are track rows, "t-list" saved lists,
// and "stack" (the default) stacks each cell under its label.
function table(columns, rows, { onRow, kind = "stack", rowAttrs, onRender } = {}) {
  // Optional columns disappear when no row has a value, as in the terminal.
  columns = columns.filter((column) => !column.optional || rows.some((row) => column.optional(row)));
  const head = h("tr", null, columns.map((column) => h("th", { class: column.class }, column.label)));
  const body = h("tbody");
  for (const row of rows) {
    const cells = columns.map((column) => {
      const value = column.cell(row);
      return h("td", { class: column.class, "data-label": column.label || null }, value);
    });
    const attrs = { ...(rowAttrs ? rowAttrs(row) : {}) };
    if (onRow) attrs.class = `${attrs.class || ""} link`.trim();
    const tr = h("tr", attrs, cells);
    if (onRow) tr.addEventListener("click", (event) => {
      if (event.target.closest("a, button")) return;
      onRow(row);
    });
    if (onRender) onRender(row, tr);
    body.append(tr);
  }
  return h("div", { class: `table-wrap ${kind}` }, h("table", null, h("thead", null, head), body));
}

function empty(...lines) {
  return h("div", { class: "empty" }, lines.map((line) => h("p", null, line)));
}

// Error codes are for scripts; the page says what happened in words.
function notice(tone, message) {
  return h("div", { class: `notice ${tone}`, role: tone === "bad" ? "alert" : "status" }, message);
}

function pageHead(title, subtitle, ...extra) {
  return h("div", { class: "head" },
    h("div", null, h("h1", null, title), subtitle ? h("p", { class: "sub" }, subtitle) : null),
    extra);
}

function crumb(href, label) {
  return h("a", { class: "crumb", href }, h("span", { "aria-hidden": "true" }, "‹ "), label);
}

// -- formatting -------------------------------------------------------------------------------

// "1,000": counts read with thousands separators, as in the terminal.
function number(count) {
  return typeof count === "number" ? count.toLocaleString("en-US") : String(count ?? "");
}

function plural(count, word, many) {
  return `${number(count)} ${count === 1 ? word : many || `${word}s`}`;
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

// "05:39" or "1:02:09" in a set, as seconds; null for anything else.
function stampSeconds(stamp) {
  const match = /^(?:(\d+):)?(\d{1,2}):(\d{2})$/.exec(String(stamp || "").trim());
  if (!match) return null;
  return Number(match[1] || 0) * 3600 + Number(match[2]) * 60 + Number(match[3]);
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

// "Poison (Extended Mix)" typed as the title: the mix reads muted, as on identified sets.
// A remixer's country tag may sit inside it: "Bad Love (Antdot & Maz (BR) Remix)".
const TRAILING_MIX = /^(.*\S)\s*\(((?:[^()]|\([A-Z]{2,3}\))*?\b(?:mix|edit|remix|dub|vip|rework|version|edition|bootleg|flip|instrumental)\b(?:[^()]|\([A-Z]{2,3}\))*)\)$/i;

function trackLabel(track) {
  let title = track.title || "Untitled";
  let version = track.version || "";
  const mix = !version && TRAILING_MIX.exec(title);
  if (mix) [title, version] = [mix[1], mix[2]];
  return h("span", null,
    track.artist || "Unknown artist", " – ",
    h("b", null, title),
    version ? h("span", { class: "muted" }, ` (${version})`) : null);
}

// "Artist - Title (Version)": how a DJ types it into a store search or a notes app.
function plainLabel(track) {
  if (!track || track.kind === "unknown") return "ID";
  const base = `${track.artist || "Unknown artist"} - ${track.title || "Untitled"}`;
  return track.version ? `${base} (${track.version})` : base;
}

const SOURCES = {
  rekordbox_analysis: "rekordbox analysis",
  native_tag: "file tag",
  operator: "entered by you",
};

function readout(value, kind, source, verified) {
  if (value === null || value === undefined || value === "") return h("span", { class: "blank" }, "—");
  const text = kind === "bpm" ? String(Number(Number(value).toFixed(2))) : value;
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

// The 8-character handle the terminal prints and accepts, as terminal.short_id makes it.
function shortId(id) {
  const value = String(id || "");
  const cut = value.lastIndexOf("_");
  return cut > 0 && cut < value.length - 1 ? value.slice(cut + 1, cut + 9) : value;
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

// A button that copies lines the page doesn't show; if the browser refuses, the lines
// appear under the button's row, ready to select.
function copyLines(label, lines) {
  const button = h("button", { class: "small copy-lines", type: "button" }, label);
  let shown = null;
  button.addEventListener("click", async () => {
    const text = lines.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      if (!shown) {
        shown = h("pre", { class: "copy-out", tabindex: "0" }, text);
        (button.parentElement || button).after(shown);
      }
      button.textContent = "Select and copy below";
    }
    setTimeout(() => { button.textContent = label; }, 1600);
  });
  return button;
}

// -- links out: listening and store searches --------------------------------------------------
// Only these hosts become links; anything else a page or tracklist says stays plain text.

const YOUTUBE_HOSTS = new Set(["www.youtube.com", "youtube.com", "m.youtube.com"]);

function parsedHttps(value) {
  let url;
  try {
    url = new URL(String(value || ""));
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
  return url;
}

function provider(value) {
  const url = parsedHttps(value);
  if (!url) return null;
  if (YOUTUBE_HOSTS.has(url.hostname) || url.hostname === "youtu.be") return "youtube";
  if (url.hostname === "soundcloud.com") return "soundcloud";
  return null;
}

function clock(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

// The set at that moment: YouTube ?t=, SoundCloud #t=. Null for any other link.
function listenUrl(sourceUrl, seconds) {
  const url = parsedHttps(sourceUrl);
  if (!url || typeof seconds !== "number") return null;
  const at = Math.max(0, Math.floor(seconds));
  let video = null;
  if (YOUTUBE_HOSTS.has(url.hostname)) video = url.searchParams.get("v");
  else if (url.hostname === "youtu.be") video = url.pathname.slice(1);
  if (video !== null) {
    return /^[\w-]{6,20}$/.test(video) ? `https://www.youtube.com/watch?v=${video}&t=${at}s` : null;
  }
  if (url.hostname === "soundcloud.com" && /^\/[\w-]+\/[\w-]+\/?$/.test(url.pathname)) {
    return `https://soundcloud.com${url.pathname}#t=${clock(at)}`;
  }
  return null;
}

// The terminal's three stores, in its order.
const STORES = [
  ["Beatport", "www.beatport.com", (query) => `https://www.beatport.com/search?q=${query}`],
  ["Bandcamp", "bandcamp.com", (query) => `https://bandcamp.com/search?q=${query}&item_type=t`],
  ["Traxsource", "www.traxsource.com", (query) => `https://www.traxsource.com/search?term=${query}`],
];

// Search words without the noise stores trip over: featured artists, (Original Mix), [LABEL].
function storeQuery(track) {
  const clean = (value) => String(value || "").replace(/\s*\[[^\]]*\]\s*$/, "").trim();
  const artist = clean(track.artist).replace(/\s+(?:feat\.?|ft\.?|featuring)\s.*$/i, "");
  const title = clean(track.title).replace(/\s*\((?:feat\.?|ft\.?|featuring)\s[^)]*\)/i, "");
  const version = /^original mix$/i.test(clean(track.version)) ? "" : clean(track.version);
  return [artist, title, version].filter(Boolean).join(" ");
}

// Store searches for a song you don't own; djlib never opens them itself.
function storeLinks(item) {
  const shop = item.shop || {};
  const query = encodeURIComponent(storeQuery(item.input || {}));
  return STORES.map(([name, host, build]) => {
    const given = parsedHttps(shop[name.toLowerCase()]);
    return [name, given && given.hostname === host ? given.href : build(query)];
  });
}

function storeLinksNode(item) {
  const links = storeLinks(item).map(([name, url]) =>
    h("a", { href: url, target: "_blank", rel: "noopener noreferrer", title: `Search ${name}` }, name));
  return h("span", { class: "buy" }, links.flatMap((link, index) => (index ? [h("span", { "aria-hidden": "true" }, " · "), link] : [link])));
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

// A status pill; on a phone only its glyph shows, and the word stays for screen readers.
function pill(tone, glyph, label, title) {
  return h("span", { class: `pill ${tone}`, title: title || null },
    h("span", { class: "g", "aria-hidden": "true" }, glyph), h("span", { class: "l" }, label));
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
  identify: "Set ID",
  scan: "Scan",
  collection: "Crate",
  download: "Download",
  export: "Export",
  delivery: "Delivery prep",
  delivery_check: "Delivery check",
  organize: "Crate",
  organization: "Crate",
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
    ],
    jobs,
    { rowAttrs: (job) => ({ title: job.job_id }) },
  );
}

// -- what you own, per track ------------------------------------------------------------------

function otherMixes(item) {
  const names = (item.candidates || [])
    .filter((candidate) => candidate.identity_match === "different_version")
    .map((candidate) => (candidate.version ? `${candidate.title} (${candidate.version})` : candidate.title));
  return [...new Set(names.filter(Boolean))];
}

// You own the exact mix: one copy, two to pick from, or a file djlib can't read right now.
const OWNED_STATES = new Set(["satisfied", "ambiguous", "unavailable"]);

// A file with the same title and mix under a shorter or longer artist credit.
function maybeFile(item) {
  if (item.state !== "missing" || item.input?.kind === "unknown") return null;
  return (item.candidates || []).find((candidate) => candidate.identity_match === "similar_credits") || null;
}

// owned | maybe | missing | other | id: the same answers as the terminal's Own column.
function ownKind(item) {
  if (OWNED_STATES.has(item.state)) return "owned";
  if (item.input?.kind === "unknown") return "id";
  if (maybeFile(item)) return "maybe";
  return otherMixes(item).length ? "other" : "missing";
}

// Not in stores yet, so no store search and not on the list to buy.
function unreleased(input) {
  return /\bunreleased\b/i.test(`${input?.title || ""} ${input?.version || ""}`);
}

// The same tally as a /requests row's counts, for items the page has in hand. "to_check"
// counts owned songs waiting on a pick or a file (they are in "owned" too).
function countsOf(items) {
  const counts = { owned: 0, missing: 0, other_mix: 0, ids: 0, to_check: 0, maybe: 0 };
  const keys = { owned: "owned", missing: "missing", other: "other_mix", id: "ids", maybe: "maybe" };
  for (const item of items) {
    counts[keys[ownKind(item)]] += 1;
    if (item.state === "ambiguous" || item.state === "unavailable") counts.to_check += 1;
  }
  return counts;
}

// "12 owned, 1 to check, 1 maybe yours, 1 other mix, 7 missing, 2 IDs", as `djlib set` says it:
// owned is what the crate holds; an owned song waiting on a pick or a file is to check.
function tally(counts) {
  const check = counts.to_check || 0;
  const parts = [
    ["owned", (counts.owned || 0) - check, "owned"],
    ["check", check, "to check"],
    ["maybe", counts.maybe || 0, "maybe yours"],
    ["other", counts.other_mix || 0, "other mix"],
    ["missing", counts.missing || 0, "missing"],
    ["id", counts.ids || 0, counts.ids === 1 ? "ID" : "IDs"],
  ];
  return parts.filter(([kind, count]) => count || kind === "owned")
    .map(([kind, count, word]) => h("span", { class: `t ${kind}` }, h("b", null, number(count)), ` ${word}`));
}

function tallyText(counts) {
  return tally(counts).map((node) => node.textContent).join(", ");
}

// One equal segment per track, in set order: the set at a glance.
function strip(items, { compact = false, onPick } = {}) {
  const bar = h("div", {
    class: `strip${compact ? " compact" : ""}${items.length > 60 ? " dense" : ""}`,
    role: "img",
    "aria-label": tallyText(countsOf(items)),
  });
  for (const item of items) {
    const input = item.input || {};
    const where = input.timestamp || `#${item.position}`;
    const what = input.kind === "unknown" ? idName(input.label) : plainLabel(input).replace(" - ", " – ");
    const segment = h("span", { class: `seg ${ownKind(item)}`, title: `${where}  ${what}` });
    if (onPick) segment.addEventListener("click", () => onPick(item));
    bar.append(segment);
  }
  return bar;
}

// For lists too long to fetch for a glance: the same colours, grouped.
function countsStrip(counts) {
  const bar = h("div", { class: "strip compact grouped", role: "img", "aria-label": tallyText(counts) });
  for (const [kind, key] of [["owned", "owned"], ["maybe", "maybe"], ["other", "other_mix"], ["missing", "missing"], ["id", "ids"]]) {
    if (!counts[key]) continue;
    const segment = h("span", { class: `seg ${kind}` });
    segment.style.setProperty("flex-grow", String(counts[key]));
    bar.append(segment);
  }
  return bar;
}

// Where the set came from: the set's own page (djlib set keeps it), else the link its IDs
// carry, or a Shazam list's notes.
function setSource(items, ledger) {
  const given = ledger?.source;
  if (given) {
    const kind = provider(given.url);
    if (kind) return { kind, url: parsedHttps(given.url).href };
    if (/^shazam$/i.test(given.provider || "")) return { kind: "shazam", url: null };
  }
  for (const item of items) {
    const url = item.input?.source_url;
    const kind = provider(url);
    if (kind) return { kind, url };
  }
  const named = items.filter((item) => item.input?.kind !== "unknown");
  if (named.length && named.every((item) => /^Shazamed\b/.test(item.input?.notes || ""))) {
    return { kind: "shazam", url: null };
  }
  return null;
}

const SOURCE_NAMES = { youtube: "YouTube", soundcloud: "SoundCloud", shazam: "Shazam" };

function sourceBadge(source) {
  return source ? h("span", { class: `src ${source.kind}` }, SOURCE_NAMES[source.kind]) : null;
}

// How sure djlib is, in the terminal's words: a confirmed line quietly, and an ID says "ID"
// in its Own column; the lines worth a listen before you play them stand out.
const SURE = {
  confirmed: ["✓", "confirmed", "Confirmed: the audio bears it out"],
  likely: ["◐", "likely", "Likely: one good source, such as a tracklist. Worth a listen"],
  weak: ["!", "weak", "Weak: heard once, or a listener's guess. Listen before you play it"],
  conflict: ["✗", "conflict", "Conflict: listed as one song, heard as another"],
};

function sureWord(confidence) {
  const word = SURE[confidence];
  if (!word) return "";
  const [glyph, label, title] = word;
  return h("span", { class: `sure-word ${confidence}`, title, "aria-label": title },
    h("span", { class: "g", "aria-hidden": "true" }, glyph), h("span", { class: "l" }, label));
}

const UNSURE = new Set(["likely", "weak", "conflict"]);

// "possibly X (Shazam heard it at 50:09 (3×))" or a15's "(Shazam, 1 window at 51:09)": one
// guess per "; " part, so a match never runs into the next guess.
const GUESS = /^possibly (.+?) \([A-Z][\w.&' -]*?(?: heard |, )[^;]*\d:\d\d[^;]*\)$/i;

// An ID's lead, "maybe X" or "likely X", out of its notes; the rest of the notes stay.
function idHint(notes) {
  const text = String(notes || "");
  const likely = /^ID \(likely (.+?)\): /.exec(text);
  if (likely) return { hint: `likely ${likely[1]}`, rest: text.slice(likely[0].length) };
  const parts = text.split("; ");
  const at = parts.findIndex((part) => GUESS.test(part));
  if (at < 0) return { hint: null, rest: text };
  const hint = `maybe ${GUESS.exec(parts[at])[1]}`;
  return { hint, rest: parts.filter((_, index) => index !== at).join("; ") };
}

function capital(text) {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

// "ID", "ID - ID", "? - ?": nothing known. "Kaytranada - ID" keeps the artist, the one clue.
const BARE_ID = /^\s*(?:id\s*-\s*)?id\s*$/i;
const UNKNOWN_PART = /^(?:id|\?+|unknown|unreleased id|tba)$/i;

function idName(label) {
  const text = String(label || "").trim();
  if (!text || BARE_ID.test(text) || text.split(/\s+[-–—]\s+/).every((part) => UNKNOWN_PART.test(part))) return "ID";
  return text.replace(/\s+-\s+/, " – ");
}

function trackCell(item, { timed = true, at = null } = {}) {
  const input = item.input || {};
  if (input.kind === "unknown") {
    const { hint, rest } = idHint(input.notes);
    const name = idName(input.label);
    // Without a Time column an ID keeps its time beside it ("ID · 15:00"), as in the terminal.
    const when = !timed && input.timestamp ? [" · ", at ? at(item) : input.timestamp] : null;
    return [
      h("div", { class: "name id" }, h("b", null, name), when, hint ? h("span", { class: "muted" }, ` — ${hint}`) : null),
      rest ? h("div", { class: "note", title: input.notes }, capital(rest)) : null,
    ];
  }
  return [
    h("div", { class: "name" }, trackLabel(input)),
    input.notes ? h("div", { class: "note", title: input.notes }, input.notes) : null,
  ];
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
      ". You have ", n(requests.total), requests.total === 1 ? " set" : " sets",
      ", ", n(collections.total), collections.total === 1 ? " crate" : " crates",
      " and ", n(deliveries.total), deliveries.total === 1 ? " delivery." : " deliveries."));
  }
  view.append(h("h2", null, "Recent jobs"));
  view.append(jobs.jobs.length ? jobTable(jobs.jobs) : empty("No jobs yet."));
  if (jobs.jobs.some((job) => ACTIVE.has(job.state))) refreshSoon();
  return view;
}

function libraryTable(tracks, { numbered = false } = {}) {
  const position = new Map(tracks.map((track, index) => [track, index + 1]));
  const dj = (key) => (t) => t.dj?.[key] !== null && t.dj?.[key] !== undefined && t.dj?.[key] !== "";
  return table(
    [
      numbered ? { label: "#", class: "num muted c-pos", cell: (t) => position.get(t) } : null,
      { label: "Title", class: "clip title c-title", cell: (t) => t.title },
      { label: "Artist", class: "clip c-artist", cell: (t) => t.artist },
      { label: "Version", class: "clip muted c-ver", cell: (t) => t.version || "", optional: (t) => t.version },
      // Shown once some track has them: until rekordbox's analysis is read, a column of
      // dashes says nothing.
      {
        label: "BPM", class: "num c-bpm",
        cell: (t) => readout(t.dj?.bpm, "bpm", t.dj?.bpm_source, t.dj?.bpm_verified),
        optional: dj("bpm"),
      },
      {
        label: "Key", class: "num c-key",
        cell: (t) => readout(t.dj?.key, "key", t.dj?.key_source, t.dj?.key_verified),
        optional: dj("key"),
      },
      { label: "Time", class: "num c-time", cell: (t) => duration(t.properties?.duration_seconds) },
      { label: "Format", class: "muted c-format", cell: audioFormat },
      { label: "File", class: "file c-file", cell: (t) => fileTail(t.path || t.last_known_path) },
    ].filter(Boolean),
    tracks,
    { kind: numbered ? "t-lib numbered" : "t-lib" },
  );
}

async function library() {
  const view = h("section");
  const input = h("input", {
    class: "search",
    type: "search",
    placeholder: "Search artist, title or mix",
    "aria-label": "Search the library",
    autocomplete: "off",
  });
  const subtitle = h("p", { class: "sub" }, "Type words from the artist, title or mix; every word must match.");
  const results = h("div");
  view.append(h("div", { class: "head" }, h("div", null, h("h1", null, "Library"), subtitle), input), results);
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
      if (!query && total) subtitle.textContent = `${plural(total, "track")}. Type words from the artist, title or mix; every word must match.`;
      const none = query
        ? empty(`No tracks match “${query}”.`)
        : empty("No tracks yet. Index your music folder from a terminal:", copyable(commandText("scan")));
      // BPM and key come from rekordbox's analysis, once djlib has read it.
      const unread = !query && tracks.length && !tracks.some((t) => t.dj?.bpm || t.dj?.key);
      results.replaceChildren(...[
        unread ? h("div", { class: "hint-box" },
          h("p", { class: "hint" }, "No BPM or key yet. To read them from rekordbox's analysis, run:"),
          copyable(commandText("rekordbox", "sync"))) : null,
        tracks.length ? libraryTable(tracks) : none,
        tracks.length ? h("p", { class: "muted more" }, `${number(tracks.length)} of ${plural(total, "track")}`) : null,
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

function listView({ title, subtitle, path, key, columns, emptyText, href, rowTitle, kind }) {
  return async () => {
    const reply = await api(path, { params: { limit: 100 } });
    const rows = reply[key];
    const view = h("section", null, pageHead(title, rows.length ? subtitle(reply.total) : null));
    view.append(rows.length
      ? table(columns, rows, {
        onRow: (row) => { location.hash = href(row); },
        rowAttrs: rowTitle ? (row) => ({ title: rowTitle(row) }) : null,
        kind,
      })
      : emptyText());
    return view;
  };
}

// Run `work` over values, a few at a time; one failure leaves the others alone.
async function eachLimited(values, limit, work) {
  let next = 0;
  const runner = async () => {
    while (next < values.length) {
      const value = values[next++];
      try {
        await work(value);
      } catch {
        // The row keeps the tally it already has.
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, values.length) }, runner));
}

const GLANCE_ITEMS = 100;

// A set's name, its dates kept whole ("2013-09-27" never breaks at a hyphen).
function setName(name) {
  const parts = String(name || "").split(/(\d{4}-\d{2}-\d{2})/);
  return parts.map((part, index) => (index % 2 ? h("span", { class: "nowrap" }, part) : part));
}

function setRow(row, { twin = false } = {}) {
  const href = `#/sets/${encodeURIComponent(row.request_id)}`;
  const badge = h("span", { class: "src-slot", hidden: true });
  const bar = h("div", { class: "strip compact loading-strip", "aria-hidden": "true" });
  const counts = h("p", { class: "tally" }, row.counts ? tally(row.counts) : null);
  // The list is newest first by when each set was last checked: a set checked again comes back on top.
  const when = ago(row.updated_at || row.created_at);
  const node = h("li", null,
    h("a", { class: "set", href },
      h("div", { class: "set-name" },
        h("span", { class: "name", title: row.name }, ...setName(row.name)),
        // Two sets of one name (pastes of one tracklist): the code tells them apart.
        h("span", { class: "meta" }, badge, plural(row.total_items, "track"), when ? ` · ${when}` : null,
          twin ? ` · ${shortId(row.request_id)}` : null)),
      bar,
      counts));
  // The first page of tracks is enough for the badge; a longer list's strip comes from
  // its counts, grouped, rather than from fetching every page.
  async function fill() {
    const ledger = await api(`/requests/${encodeURIComponent(row.request_id)}`, { params: { limit: GLANCE_ITEMS } });
    const whole = ledger.next_offset === null || ledger.next_offset === undefined;
    const glance = whole ? ledger.items : null;
    if (glance) bar.replaceWith(strip(glance, { compact: true }));
    else if (row.counts) bar.replaceWith(countsStrip(row.counts));
    const source = setSource(ledger.items, ledger);
    if (source) badge.replaceWith(sourceBadge(source));
    if (!row.counts && glance) counts.replaceChildren(...tally(countsOf(glance)));
  }
  return { node, fill };
}

async function setsList() {
  const first = await api("/requests", { params: { limit: 50 } });
  if (!first.requests.length) {
    return h("section", null, pageHead("Sets"), empty(
      "No sets yet. Copy a set's link or tracklist, then run this in a terminal:",
      copyable(commandText("set", "paste")),
      "Or check the songs you Shazamed:",
      copyable(commandText("shazam", "status")),
    ));
  }
  const list = h("ol", { class: "sets" });
  const more = h("div");
  const view = h("section", null,
    pageHead("Sets", `${plural(first.total, "set")}, last checked first. Owned tracks are in a crate, in set order.`),
    list, more);
  const names = new Map();
  const show = (reply) => {
    for (const row of reply.requests) names.set(row.name, (names.get(row.name) || 0) + 1);
    const rows = reply.requests.map((row) => setRow(row, { twin: names.get(row.name) > 1 }));
    list.append(...rows.map((row) => row.node));
    more.replaceChildren(reply.next_cursor
      ? h("button", {
        type: "button",
        class: "more",
        onclick: () => api("/requests", { params: { limit: 50, after: reply.next_cursor } })
          .then(show).catch((error) => more.replaceChildren(errorNotice(error))),
      }, "Load more")
      : h("p", { class: "hint" }, "Check another set: copy its link or tracklist, then run ",
        h("code", { class: "inline" }, commandText("set", "paste"))));
    eachLimited(rows, 4, (row) => row.fill());
  };
  show(first);
  return view;
}

// The spelling an owned song's file matched under (accepted.matched_as): only the artist when
// the title and mix are the song's own ("as Robert Glasper"), else the whole label.
function spelledAs(item) {
  const label = item.accepted?.matched_as || "";
  if (!label) return "";
  const input = item.input || {};
  const credits = Array.isArray(input.also) ? input.also : [];
  const same = (one, other) => String(one || "").trim().toLowerCase() === String(other || "").trim().toLowerCase();
  const credit = credits.find((c) => plainLabel({ ...c, kind: "named" }) === label) || credits[0];
  if (credit?.artist && same(credit.title, input.title) && same(credit.version, input.version)) return credit.artist;
  return label;
}

// The MixesDB page a set's tracklist comes from: credited and linked under the tally.
function mixesdbCredit(credit) {
  if (!credit) return null;
  const url = parsedHttps(credit.page_url);
  const ours = url && ["www.mixesdb.com", "mixesdb.com"].includes(url.hostname);
  const name = ours
    ? h("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", title: credit.page_title || null }, "Tracklist from MixesDB ↗")
    : h("span", null, "Tracklist from MixesDB");
  return h("p", { class: "credit" }, name,
    credit.tracklist === "incomplete" ? h("span", { class: "warn" }, " · MixesDB marks it incomplete") : null);
}

// Above this many tracks (a Shazam history) the set opens on the songs to act on, a page at a
// time; the strip and the tally stay whole.
const LONG_SET = 60;
const PAGE_ROWS = 50;
const FILTERS = [
  ["missing", "Missing"],
  ["maybe", "Maybe yours"],
  ["other", "Other mix"],
  ["owned", "Owned"],
  ["id", "IDs"],
  ["all", "All"],
];

// A Shazam list reads newest first: by the day you Shazamed, then by its place in the list.
function newestFirst(items) {
  const day = (item) => (/Shazamed (\d{4}-\d{2}-\d{2})/.exec(item.input?.notes || "") || [])[1] || "";
  return [...items].sort((a, b) => day(b).localeCompare(day(a)) || (b.position || 0) - (a.position || 0));
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
  const counts = countsOf(items);
  const source = setSource(items, ledger);
  const setUrl = source?.url || null;
  // Only songs with one readable copy go into the crate, one file each however often the set
  // lists the song; the rest of what you own waits.
  const ready = new Set(items.filter((item) => item.state === "satisfied")
    .map((item) => item.accepted?.asset_revision_id || item.item_id)).size;
  const twice = items.filter((item) => item.state === "ambiguous").length;
  const unread = items.filter((item) => item.state === "unavailable").length;
  const maybe = items.filter((item) => ownKind(item) === "maybe").length;
  const message = h("div");
  const waiting = [
    twice ? `You own ${plural(twice, "song")} twice: pick the copy for the crate below.` : null,
    unread ? `djlib can't read ${plural(unread, "song")} you own right now (is the drive plugged in?), so the crate leaves ${unread === 1 ? "it" : "them"} out.` : null,
    maybe ? `${maybe === 1 ? "One song" : `${maybe} songs`} may be yours under another artist name: if it's the same song, Use this.` : null,
  ].filter(Boolean);
  const view = h("section", { class: "set-page" }, crumb("#/sets", "Sets"));

  // The crate djlib set already made: open it. Build one, or update it when you own more now.
  const crate = ledger.crate?.collection_id ? ledger.crate : null;
  const current = crate && crate.tracks === ready;
  const crateHref = (id) => `#/collections/${encodeURIComponent(id)}`;
  const action = current
    ? h("a", { class: "button primary", href: crateHref(crate.collection_id) }, `Open crate (${plural(crate.tracks, "track")})`)
    : h("button", { type: "button", class: "primary", disabled: !ready },
      !ready ? "Build crate" : `${crate ? "Update" : "Build"} crate (${plural(ready, "track")})`);
  const when = ago(ledger.updated_at || ledger.created_at);
  view.append(h("div", { class: "set-head" },
    h("div", { class: "set-title" },
      h("p", { class: "meta" }, sourceBadge(source), plural(ledger.total_items, "track"), when ? ` · checked ${when}` : null),
      h("h1", null, ledger.name)),
    action));
  if (waiting.length) view.append(notice("warn", waiting.join(" ")));
  view.append(message);

  // Where the crate goes next, as on the crate's own page.
  const handoff = (collectionId) => h("div", { class: "handoff" },
    h("div", null, h("h2", null, "Put it in rekordbox"), copyable(commandText("rekordbox", "push", shortId(collectionId)))),
    h("div", null, h("h2", null, "Or straight onto your USB"), copyable(commandText("rekordbox", "usb", shortId(collectionId)))));

  const rows = new Map();
  let filter = "all";
  let limit = Infinity;
  const tableSlot = h("div");
  const filterBar = h("div", { class: "filters", role: "group", "aria-label": "Show" });
  const moreSlot = h("div");
  const sure = items.some((item) => UNSURE.has(item.input?.confidence));
  const shazam = source?.kind === "shazam";
  const order = shazam ? newestFirst(items) : items;
  const long = items.length > LONG_SET;
  const pick = (item) => {
    if (!rows.has(item)) {
      // A track the list hides: show it, then go there.
      filter = "all";
      limit = Math.max(limit, order.indexOf(item) + 1);
      render();
    }
    const row = rows.get(item);
    if (!row) return;
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    row.scrollIntoView({ block: "center", behavior: still ? "auto" : "smooth" });
    row.classList.remove("flash");
    void row.offsetWidth; // restart the highlight when the same track is picked twice
    row.classList.add("flash");
  };
  view.append(strip(items, { onPick: pick }));

  // The songs to buy: missing outright, and in a store already.
  const missing = items.filter((item) => ownKind(item) === "missing" && !unreleased(item.input));
  view.append(h("div", { class: "set-tally" },
    h("p", { class: "tally big" }, tally(counts)),
    missing.length ? copyLines(`Copy ${number(missing.length)} to buy`, missing.map((item) => plainLabel(item.input))) : null));
  const credit = mixesdbCredit(ledger.mixesdb);
  if (credit) view.append(credit);
  if (current) view.append(handoff(crate.collection_id));

  if (!current) {
    action.addEventListener("click", async () => {
      action.disabled = true;
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
            `Crate ready with ${plural(job.result.selected_count ?? ready, "track")}, in set order. `,
            h("a", { href: crateHref(collectionId) }, "Open the crate")), handoff(collectionId));
        } else {
          message.replaceChildren(notice("bad", "The crate couldn't be built. See why in a terminal: djlib jobs"));
        }
      } catch (error) {
        message.replaceChildren(errorNotice(error));
      } finally {
        action.disabled = false;
      }
    });
  }

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
        document.getElementById("view").prepend(notice("bad", "This set changed elsewhere, so it was reloaded. Choose again."));
      } else {
        message.replaceChildren(errorNotice(error));
        button.disabled = false;
      }
    }
  }

  // A Time column when the tracklist times its songs; an ID's time alone goes beside it.
  const timed = items.some((item) => item.input?.kind !== "unknown" && item.input?.timestamp);
  const stamped = (item) => {
    const stamp = item.input?.timestamp;
    const link = setUrl && listenUrl(setUrl, stampSeconds(stamp));
    return link
      ? h("a", { class: "time", href: link, target: "_blank", rel: "noopener noreferrer", title: `Play the set from ${stamp}` }, stamp)
      : h("span", { class: "time" }, stamp);
  };
  const moment = (item) => {
    if (!timed) return h("span", { class: "pos" }, item.position);
    if (!item.input?.timestamp) return h("span", { class: "pos", title: `Track ${item.position}: no time for it` }, "—");
    return stamped(item);
  };

  const own = (item) => {
    const kind = ownKind(item);
    const accepted = item.accepted || {};
    if (kind === "owned" && item.state === "ambiguous") {
      // Only readable copies of this exact mix; djlib refuses anything else.
      const copies = (item.candidates || []).filter((candidate) => candidate.availability === "verified"
        && !["different_version", "different_labels", "similar_credits"].includes(candidate.identity_match));
      return [
        pill("warn", "!", "Owned, pick a copy", "You own this mix more than once"),
        copies.length ? h("ul", { class: "candidates" }, copies.map((candidate) => {
          const button = h("button", { type: "button", class: "small" }, "Use this");
          button.addEventListener("click", () => choose(item, candidate, button));
          return h("li", null, button, fileTail(candidate.path || (candidate.locations || [])[0]));
        })) : null,
      ];
    }
    if (kind === "owned" && item.state === "unavailable") {
      return pill("warn", "!", "Owned, file unavailable", "djlib can't read the file right now. Is the drive plugged in?");
    }
    if (kind === "owned") {
      const spelling = spelledAs(item);
      return [
        pill("ok", "✓", "Owned", accepted.path || null),
        spelling ? h("div", { class: "own-note" }, `as ${spelling}`) : null,
      ];
    }
    if (kind === "id") return pill("id", "○", "ID");
    if (kind === "maybe") {
      // The same title and mix under other credits: the DJ says whether it's the song.
      const file = maybeFile(item);
      const button = h("button", { type: "button", class: "small" }, "Use this");
      button.addEventListener("click", () => choose(item, file, button));
      return [
        pill("maybe", "?", "Maybe yours", "A file with the same title and mix, under another artist name"),
        h("div", { class: "own-note" }, `You have ${plainLabel({ ...file, kind: "named" })} `, button),
      ];
    }
    if (kind === "other") {
      return [pill("other", "≈", "Other mix"), h("div", { class: "own-note" }, `You own ${otherMixes(item).join(", ")}`)];
    }
    if (item.state === "source_selected") {
      return [pill("info", "◐", "Source picked"), h("div", { class: "own-note path" }, item.source_selection?.source_url || "")];
    }
    return [
      pill("bad", "✗", "Missing"),
      // On a phone the store links go on the line under the track.
      unreleased(item.input) ? h("div", { class: "own-note" }, "Not in stores yet") : storeLinksNode(item),
    ];
  };

  if (long) {
    const kinds = new Map(FILTERS.map(([kind]) => [kind, 0]));
    for (const item of items) kinds.set(ownKind(item), kinds.get(ownKind(item)) + 1);
    filter = ["missing", "maybe", "other"].find((kind) => kinds.get(kind)) || "all";
    limit = PAGE_ROWS;
    for (const [kind, label] of FILTERS) {
      const amount = kind === "all" ? items.length : kinds.get(kind);
      if (!amount) continue;
      const button = h("button", { type: "button", class: "small", "data-kind": kind }, `${label} (${amount})`);
      button.addEventListener("click", () => {
        filter = kind;
        limit = PAGE_ROWS;
        render();
      });
      filterBar.append(button);
    }
    view.append(filterBar);
  }

  function render() {
    for (const button of filterBar.querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(button.dataset.kind === filter));
    }
    const shown = order.filter((item) => filter === "all" || ownKind(item) === filter);
    const visible = shown.slice(0, limit);
    rows.clear();
    tableSlot.replaceChildren(table(
      [
        { label: timed ? "Time" : "#", class: "c-time", cell: moment },
        { label: "Sure", class: "c-sure", cell: (item) => sureWord(item.input?.confidence), optional: (item) => UNSURE.has(item.input?.confidence) },
        { label: "Track", class: "c-track", cell: (item) => trackCell(item, { timed, at: stamped }) },
        { label: "Own", class: "c-own", cell: own },
      ],
      visible,
      {
        kind: `t-set${timed ? " timed" : ""}${sure ? " sure" : ""}`,
        rowAttrs: (item) => ({ class: `own-${ownKind(item)}` }),
        onRender: (item, tr) => rows.set(item, tr),
      },
    ));
    const left = shown.length - visible.length;
    moreSlot.replaceChildren(left > 0
      ? h("button", {
        type: "button",
        class: "more",
        onclick: () => {
          limit += PAGE_ROWS;
          render();
        },
      }, `Show ${Math.min(left, PAGE_ROWS)} more (${left} left)`)
      : "");
  }
  render();
  view.append(tableSlot, moreSlot);
  view.append(h("p", { class: "foot" },
    "Matched to your files by exact artist, title and mix. Another mix is never swapped in.",
    shazam ? " Newest Shazams first." : null,
    sure ? " Lines marked likely or weak are worth a listen before you play them." : null));
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
  title: "Crates",
  subtitle: (total) => `${plural(total, "crate")}, newest first. A crate keeps its set's order.`,
  path: "/collections",
  key: "collections",
  kind: "t-list",
  href: (row) => `#/collections/${encodeURIComponent(row.collection_id)}`,
  rowTitle: (row) => shortId(row.collection_id),
  emptyText: () => empty(["No crates yet. Crates appear when you run ", h("code", { class: "inline" }, "djlib set"), "."]),
  columns: [
    { label: "Name", class: "title c-name", cell: (row) => h("a", { href: `#/collections/${encodeURIComponent(row.collection_id)}` }, row.name) },
    {
      label: "Tracks", class: "num c-count",
      cell: (row) => [row.track_count, h("span", { class: "unit" }, row.track_count === 1 ? " track" : " tracks")],
    },
    { label: "Created", class: "c-date", cell: (row) => h("span", { class: "muted" }, ago(row.created_at)) },
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
  const view = h("section", null, crumb("#/collections", "Crates"),
    pageHead(page.name, plural(page.track_count ?? tracks.length, "track")));
  if (!tracks.length) {
    view.append(empty("This crate has no tracks."));
    return view;
  }
  const short = shortId(collectionId);
  view.append(h("div", { class: "handoff" },
    h("div", null, h("h2", null, "Put it in rekordbox"), copyable(commandText("rekordbox", "push", short))),
    h("div", null, h("h2", null, "Or straight onto your USB"), copyable(commandText("rekordbox", "usb", short)))));
  view.append(libraryTable(tracks, { numbered: true }));
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
  rowTitle: (row) => row.delivery_id,
  emptyText: () => empty(
    "No deliveries yet. Plan one from a crate:",
    copyable(commandText("delivery", "plan", "--collection", "ID", "--workflow", "rekordbox_import", "--app-version", "VERSION")),
  ),
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
    h("dt", null, "Delivery"), h("dd", { class: "ident" }, delivery.delivery_id),
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
  return notice("bad", error.message || "Something went wrong.");
}

// A link to a set or crate that's gone (deleted, or from another djlib folder).
function goneNotice(tab) {
  const [what, href, back] = tab === "collections"
    ? ["This crate isn't here any more.", "#/collections", "See your crates"]
    : ["This set isn't here any more.", "#/sets", "See your sets"];
  return h("div", { class: "notice bad", role: "alert" }, what, " ", h("a", { href }, back));
}

function locked() {
  return h("section", { class: "locked" },
    h("h1", null, "Open this page from djlib"),
    h("p", null, "This page opens from its private link. Run this in a terminal:"),
    copyable("djlib ui"),
    h("p", { class: "muted" }, "The link only works on this computer, while djlib is running."));
}

function refreshSoon() {
  clearTimeout(state.timer);
  state.timer = setTimeout(() => route({ quiet: true }), 2000);
}

// Sets, Crates and Library are in the navigation; the older pages keep their links.
const ROUTES = [
  [/^#\/sets$/, "sets", setsList],
  [/^#\/sets\/([\w-]+)$/, "sets", requestDetail],
  [/^#\/requests$/, "sets", setsList],
  [/^#\/requests\/([\w-]+)$/, "sets", requestDetail],
  [/^#\/collections$/, "collections", collectionsList],
  [/^#\/collections\/([\w-]+)$/, "collections", collectionDetail],
  [/^#\/library$/, "library", library],
  [/^#\/overview$/, null, overview],
  [/^#\/deliveries$/, null, deliveriesList],
  [/^#\/deliveries\/([\w-]+)$/, null, deliveryDetail],
  [/^#\/jobs$/, null, jobs],
];

async function route({ quiet = false } = {}) {
  clearTimeout(state.timer);
  const main = document.getElementById("view");
  const render = ++state.render;
  const hash = location.hash || HOME;
  const match = ROUTES.map(([pattern, tab, view]) => [hash.match(pattern), tab, view]).find(([found]) => found);
  const [found, tab, view] = match || [[HOME], "sets", setsList];
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
    if (render !== state.render) return;
    main.replaceChildren(node);
    // The tab reads like the page: "Kaytranada Boiler Room · djlib".
    const heading = node.querySelector("h1")?.textContent;
    document.title = heading ? `${heading} · djlib` : "djlib";
  } catch (error) {
    if (render !== state.render) return;
    const gone = error.code === "NOT_FOUND" && ["sets", "collections"].includes(tab);
    main.replaceChildren(error.status === 401 ? locked() : gone ? goneNotice(tab) : errorNotice(error));
  }
}

async function start() {
  readHandoff();
  window.addEventListener("hashchange", () => {
    route();
    window.scrollTo(0, 0);
  });
  await route();
}

start();
