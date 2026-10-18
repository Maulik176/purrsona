/* One native event stream per page; reconnects receive the current key state. */
window.PurrsonaKey = {
  label(state) {
    return ({ waiting: "Connect your key", connecting: "Connecting your key…",
      awaiting_button: "Press B on your key", loading: "Loading memories…",
      connected: "Memory connected", drafting: "Drafting with your voice…", learning: "Saving memory…",
      forgetting: "Forgetting…", forgotten: "Forgotten", needs_pairing: "Pair your key first",
      choose_port: "Select your USB key", connection_failed: "Reconnect your key and press B",
      offline: "Bridge disconnected · reconnecting…" })[state.phase] || "Connect your key";
  },
  subscribe(onState) {
    let source;
    const open = () => {
      source = new EventSource("/key/events");
      source.addEventListener("key", (event) => onState(JSON.parse(event.data)));
      source.onerror = () => onState({ plugged: false, phase: "offline", generation: null });
    };
    open();
    window.addEventListener("pagehide", () => source.close());
    window.addEventListener("pageshow", (event) => { if (event.persisted) open(); });
    return () => source.close();
  }
};
