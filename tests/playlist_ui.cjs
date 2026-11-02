
const fs = require("fs"), vm = require("vm"), assert = require("assert");
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.handlers = {}; this.value = ""; this.disabled = false; }
  setAttribute(key, value) { this.attributes[key] = value; if (key === "disabled") this.disabled = true; if (key === "value") this.value = value; }
  append(...nodes) { this.children.push(...nodes); if (this.tag === "select" && !this.value) this.value = this.children[0]?.value || ""; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
  querySelectorAll(tag) { return this.children.flatMap(node => node instanceof Element ? [...(node.tag === tag ? [node] : []), ...node.querySelectorAll(tag)] : []); }
}
const document = {createElement: tag => new Element(tag), createTextNode: text => String(text)};
const context = vm.createContext({document, Node: Element, URL, URLSearchParams, console, setTimeout, clearTimeout, location: {hash: ""}});
vm.runInContext(fs.readFileSync(process.argv[2], "utf8").replace(/\nstart\(\);\s*$/, ""), context);
vm.runInContext(`
  const calls = [];
  api = async (path, options) => {
    calls.push({path, body: options.body});
    return path === "/playlists/preview" ? {selected_rows: 200, total_rows: 9498, order: "newest_first", warnings: [], tracks: [{source_position: 42, artist: "Artist", title: "<script>not markup</script>", added_at: "2026-11-02T00:00:00Z"}]} : {request: {request_id: "request_test"}};
  };
  const testPromise = (async () => {
    const root = await playlistImport();
    const form = root.querySelectorAll("form")[0];
    const controls = [...root.querySelectorAll("input"), ...root.querySelectorAll("textarea"), ...root.querySelectorAll("select")];
    const get = id => controls.find(node => node.attributes.id === id);
    const save = root.querySelectorAll("button").find(node => node.children[0] === "Check against my music");
    if (!save.disabled) throw Error("Import must require preview");
    get("playlist-name").value = "Friday";
    get("playlist-text").value = "Artist - One";
    get("playlist-selection").value = "latest";
    await get("playlist-selection").handlers.change();
    if (get("playlist-count").disabled) throw Error("Latest count is disabled");
    await form.handlers.submit({preventDefault(){}});
    if (save.disabled || calls[0].body.latest !== 200) throw Error("Preview did not enable the reviewed selection");
    get("playlist-count").value = "100";
    get("playlist-count").handlers.input();
    if (!save.disabled) throw Error("Changing selection reused a stale preview");
    await form.handlers.submit({preventDefault(){}});
    await save.handlers.click();
    if (calls[2].body.latest !== 100 || location.hash !== "#/sets/request_test") throw Error("Imported the wrong selection");
    return "passed";
  })();
`, context);
vm.runInContext("testPromise", context).then(result => console.log(result)).catch(error => {console.error(error);process.exitCode=1;});
