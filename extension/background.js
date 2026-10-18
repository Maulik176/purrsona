// This is the only file of the extension that talks to the bridge.
const DEFAULT_BRIDGE = "http://127.0.0.1:8765";

async function bridgeUrl() {
  const { bridgeUrl } = await chrome.storage.local.get("bridgeUrl");
  return bridgeUrl || DEFAULT_BRIDGE;  // set in the extension's options: this laptop or the online demo
}

// The online demo gives every visitor their own key. The extension keeps its visitor id here and sends it
// with every call, so the dashboard it opens shows the same key. (Your own bridge ignores it.)
async function visitorKey() {
  let { visitorKey } = await chrome.storage.local.get("visitorKey");
  if (!visitorKey) {
    const abc = "abcdefghijklmnopqrstuvwxyz0123456789";
    visitorKey = "v-" + [...crypto.getRandomValues(new Uint8Array(12))].map((n) => abc[n % abc.length]).join("");
    await chrome.storage.local.set({ visitorKey });
  }
  return visitorKey;
}

// Your dashboard lives on the bridge. The toolbar icon opens it, and so does "dashboard ↗" in the cat's chat.
async function openDashboard() {
  chrome.tabs.create({ url: `${await bridgeUrl()}/dashboard/?key=${await visitorKey()}` });
}
chrome.action.onClicked.addListener(openDashboard);

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "dashboard") { openDashboard(); return; }
  if (msg.type !== "bridge") return;
  (async () => {
    try {
      const headers = { "X-Purrsona-Key": await visitorKey() };
      const res = await fetch((await bridgeUrl()) + msg.path, msg.body === undefined ? { headers } : {
        method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(msg.body) });
      reply({ ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) });
    } catch (e) {
      reply({ ok: false, status: 0, data: { detail: "Purrsona's bridge isn't running (start it on port 8765)" } });
    }
  })();
  return true; // we reply asynchronously
});

// Option+M as a Chrome shortcut: Chrome takes the key before the page sees it (so macOS never types its
// "˛" mark) and tells the page's cat, or the demo console, to draft. Set in chrome://extensions/shortcuts.
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "draft-reply" && tab?.id) chrome.tabs.sendMessage(tab.id, { type: "trigger" });
});
