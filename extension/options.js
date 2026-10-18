// Pick the bridge the cat talks to: your own laptop, or the online demo (your own key there too).
const LOCAL = "http://127.0.0.1:8765";
chrome.storage.local.get("bridgeUrl", ({ bridgeUrl }) => {
  const current = bridgeUrl || LOCAL;
  const radio = document.querySelector(`input[value="${current}"]`);
  if (radio) radio.checked = true;
});
document.querySelectorAll("input[name=bridge]").forEach((r) => r.addEventListener("change", () => {
  chrome.storage.local.set({ bridgeUrl: r.value }, () => {
    document.getElementById("saved").textContent = "Saved. Refresh the tab you're writing in, then press Option+M.";
  });
}));
