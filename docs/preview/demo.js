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
      const query = String(params.query).toLowerCase();
      reply.tracks = reply.tracks.filter(track =>
        [track.artist, track.title, track.version].join(" ").toLowerCase().includes(query));
      reply.total = reply.tracks.length;
    }
    return reply;
  };
  await start();
})().catch(error => {
  document.getElementById("view").textContent = "The demo could not load. Please reload the page.";
  console.error(error);
});
