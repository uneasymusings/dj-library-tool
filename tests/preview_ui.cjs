const fs = require("fs"), vm = require("vm"), assert = require("assert");
const catalog = {"/library": {tracks: [
  {artist:"Four Tet", title:"Gliding Through Everything", version:""},
  {artist:"Björk", title:"Jóga", version:"Dub"},
  {artist:"DJ Ø", title:"Straße", version:"Extended Mix"},
], total:3, next_cursor:null}};
const context = vm.createContext({URL, URLSearchParams, structuredClone,
  fetch:async () => ({json:async () => catalog}), console});
vm.runInContext(fs.readFileSync(process.argv[2], "utf8").replace(/\nstart\(\);\s*$/, ""), context);
vm.runInContext("start = async () => {}", context);
(async () => {
  await vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), context);
  const search = query => vm.runInContext(`api("/library", {params:{query:${JSON.stringify(query)}}})`, context);
  for (const [query, title] of [["everything FOUR", "Gliding Through Everything"],
    ["  joga   BJORK dub ", "Jóga"], ["strasse o mix", "Straße"]]) {
    const reply = await search(query);
    assert.equal(reply.total, 1);
    assert.equal(reply.tracks[0].title, title);
  }
  assert.equal((await search("Four missing")).total, 0);
  assert.equal((await search("")).total, 3);
  assert.equal(catalog["/library"].tracks.length, 3, "Search mutated the demo catalog");
  await assert.rejects(vm.runInContext('api("/downloads", {method:"POST",body:{}})', context),
    error => error.code === "DEMO_READ_ONLY");
  console.log("Public demo word search and read-only transport passed");
})().catch(error => {console.error(error);process.exitCode = 1;});
