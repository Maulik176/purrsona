// This is the only file of the extension that talks to the bridge.
const DEFAULT_BRIDGE = "http://127.0.0.1:8765";

async function bridgeUrl() {
  const { bridgeUrl } = await chrome.storage.local.get("bridgeUrl");
  return bridgeUrl || DEFAULT_BRIDGE;
}

// Your dashboard lives on the bridge. The toolbar icon opens it, and so does "dashboard ↗" in the cat's chat.
async function openDashboard() {
  chrome.tabs.create({ url: (await bridgeUrl()) + "/dashboard/" });
}
chrome.action.onClicked.addListener(openDashboard);

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "dashboard") { openDashboard(); return; }
  if (msg.type !== "bridge") return;
  (async () => {
    try {
      const res = await fetch((await bridgeUrl()) + msg.path, msg.body === undefined ? {} : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg.body) });
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
