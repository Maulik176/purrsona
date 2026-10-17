// This is the only file of the extension that talks to the bridge.
const DEFAULT_BRIDGE = "http://127.0.0.1:8765";

async function bridgeUrl() {
  const { bridgeUrl } = await chrome.storage.local.get("bridgeUrl");
  return bridgeUrl || DEFAULT_BRIDGE;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
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

// An optional shortcut you can set yourself in chrome://extensions/shortcuts (none by default: a Chrome
// shortcut would grab Option+M before the page's own handler sees it).
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "draft-reply" && tab?.id) chrome.tabs.sendMessage(tab.id, { type: "trigger" });
});
