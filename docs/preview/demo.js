"use strict";
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
      // Match the local catalog's word search across artist, title and mix.
      const letters = {"ø":"o", "æ":"ae", "œ":"oe", "đ":"d", "ł":"l", "ð":"d", "ß":"ss"};
      const fold = value => String(value || "").toLowerCase().normalize("NFKD")
        .replace(/\p{M}/gu, "").replace(/[øæœđłðß]/g, letter => letters[letter]);
      const terms = fold(params.query).trim().split(/\s+/).filter(Boolean).slice(0, 12);
      reply.tracks = reply.tracks.filter(track => {
        const fields = [track.artist, track.title, track.version].map(fold);
        return terms.every(term => fields.some(field => field.includes(term)));
      });
      reply.total = reply.tracks.length;
    }
    return reply;
  };
  const originalErrorNotice = errorNotice;
  errorNotice = error => error.code === "DEMO_READ_ONLY"
    ? notice("info", error.message) : originalErrorNotice(error);
  // A first visit starts on one short example; deep links and the Sets tab still work.
  const examples = catalog["/requests"]?.requests || [];
  const example = examples.find(row => row.name === "Four Tet / Lollapalooza 2024") || examples[0];
  if ((!location.hash || location.hash === "#/") && example) {
    history.replaceState(null, "", location.pathname + location.search +
      `#/sets/${encodeURIComponent(example.request_id)}`);
  }
  await start();
})().catch(error => {
  document.getElementById("view").textContent = "The demo could not load. Please reload the page.";
  console.error(error);
});
