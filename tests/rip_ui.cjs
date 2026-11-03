
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
const context = vm.createContext({document, Node: Element, URL, URLSearchParams, assert, console, setTimeout, clearTimeout, location: {hash: ""}});
vm.runInContext(fs.readFileSync(process.argv[2], "utf8").replace(/\nstart\(\);\s*$/, ""), context);
vm.runInContext(`
const calls = [];
api = async (path, options = {}) => {
  calls.push({path, body:options.body});
  if (path === "/sources/search") return {candidates: [{provider:"youtube",url:"https://youtu.be/abcdefghijk",title:"Exact Dub",uploader:"Artist"}]};
  if (path === "/downloads") return {job_id:"job_1",state:"completed",counts:{succeeded:1}};
  if (path.endsWith("/refresh")) return {counts:{satisfied:1}};
  return {revision:7};
};
const testPromise = (async () => {
  let refreshed = 0;
  const root = acquireTrack({item_id:"item_1",input:{artist:"Artist",title:"Title",version:"Dub"}},"request_1",async()=>{refreshed++;});
  assert.equal(calls.length,0);
  const actions = root.querySelectorAll("summary").map(node => node.children.join(""));
  assert.deepEqual(actions,["Rip","Shop"]);
  assert(root.querySelectorAll("details").every(node => !("open" in node.attributes)));
  const source = root.querySelectorAll("input")[0];
  const button = label => root.querySelectorAll("button").find(node=>node.children[0]===label);
  assert(button("Rip MP3").disabled);
  source.value="https://www.youtube.com.evil.test/watch?v=abcdefghijk";
  source.handlers.input();
  assert(button("Rip MP3").disabled);
  await button("Find on YouTube").handlers.click();
  assert.equal(calls.length,1);
  assert.equal(calls[0].path,"/sources/search");
  assert(button("Rip MP3").disabled);
  await button("Use this source").handlers.click();
  assert.equal(calls.length,1);
  assert(!button("Rip MP3").disabled);
  await button("Rip MP3").handlers.click();
  assert.equal(calls.filter(call=>call.path==="/downloads").length,1);
  assert.deepEqual(calls[1].body.tracks,[{url:"https://www.youtube.com/watch?v=abcdefghijk",artist:"Artist",title:"Title",version:"Dub"}]);
  assert.equal(calls[3].body.revision,7);
  assert.equal(refreshed,1);
  return "Rip/Shop interaction passed";
})();
`, context);
vm.runInContext("testPromise",context).then(console.log).catch(error=>{console.error(error);process.exitCode=1;});
