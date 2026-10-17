// The demo console drafts on its own; this hands it the extension's Option+M shortcut (Chrome takes the
// key before the page sees it, which also keeps macOS from typing its "˛" mark).
chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "trigger") window.dispatchEvent(new CustomEvent("purrsona:trigger"));
});
