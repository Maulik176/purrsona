// The Purrsona cat: a small pet that sits by the box you're typing in, and only while you're in it.
// Runs inside the Chrome extension and directly on the practice page (same file).
// Everything lives in a shadow DOM so the host page's CSS can't touch it.
(() => {
  if (window.PurrsonaCat) return;

  const CAT_SVG = `
<svg class="cat" viewBox="0 0 64 64" width="46" height="46" aria-hidden="true">
  <path class="tail" d="M44 50 C58 50 60 36 54 30" fill="none" stroke="#2f3340" stroke-width="5" stroke-linecap="round"/>
  <ellipse cx="31" cy="45" rx="16" ry="12.5" fill="#3b4050"/>
  <ellipse cx="31" cy="48" rx="8" ry="7" fill="#5a6072"/>
  <g class="head">
    <polygon points="18,19 21,5 28,14" fill="#3b4050"/>
    <polygon points="34,14 41,5 44,19" fill="#3b4050"/>
    <polygon points="21,15 22,9 26,13" fill="#f4a3b4"/>
    <polygon points="36,13 40,9 41,15" fill="#f4a3b4"/>
    <circle cx="31" cy="24" r="13" fill="#3b4050"/>
    <g class="eyes">
      <ellipse cx="26" cy="23" rx="2.6" ry="3.3" fill="#ffc44d"/>
      <ellipse cx="36" cy="23" rx="2.6" ry="3.3" fill="#ffc44d"/>
      <ellipse cx="26.6" cy="23.4" rx="1" ry="2.1" fill="#1b1d24"/>
      <ellipse cx="36.6" cy="23.4" rx="1" ry="2.1" fill="#1b1d24"/>
    </g>
    <polygon points="29.5,28 32.5,28 31,29.8" fill="#f4a3b4"/>
    <path d="M31 30 q-2 2 -4 1 M31 30 q2 2 4 1" stroke="#c9ccd6" stroke-width="0.8" fill="none"/>
    <path d="M22 28 l-8 -1 M22 30 l-8 1 M40 28 l8 -1 M40 30 l8 1" stroke="#c9ccd6" stroke-width="0.7"/>
  </g>
  <ellipse class="paw p1" cx="24" cy="56" rx="4.5" ry="3" fill="#5a6072"/>
  <ellipse class="paw p2" cx="38" cy="56" rx="4.5" ry="3" fill="#5a6072"/>
</svg>`;

  const CSS = `
:host { all: initial; }
.wrap { position: fixed; z-index: 2147483000; left: 0; top: 0; width: 46px; height: 46px; pointer-events: none;
        font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.wrap.moving { transition: left .9s cubic-bezier(.45,.05,.55,.95), top .4s ease; }
.wrap.hidden { display: none; }
.wrap.offscreen { visibility: hidden; }
.body { position: relative; width: 46px; height: 46px; cursor: pointer; pointer-events: auto; transform-origin: 50% 100%; }
.body.flip { transform: scaleX(-1); }
.cat { display: block; filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
.tail { transform-origin: 44px 50px; animation: tail 2.6s ease-in-out infinite; }
.eyes { transform-origin: 31px 23px; animation: blink 4.5s infinite; }
.walking .paw.p1 { animation: step .32s ease-in-out infinite; }
.walking .paw.p2 { animation: step .32s ease-in-out infinite .16s; }
.walking .cat { animation: bob .32s ease-in-out infinite; }
.thinking .inner { animation: pace 2.2s ease-in-out infinite; }
.thinking .paw.p1 { animation: step .32s ease-in-out infinite; }
.thinking .paw.p2 { animation: step .32s ease-in-out infinite .16s; }
.thinking .tail { animation: tail .7s ease-in-out infinite; }
.happy .inner { animation: hop .45s ease-out 2; }
.error .inner { animation: shake .4s ease-in-out 2; }
@keyframes tail { 0%,100% { transform: rotate(0deg); } 50% { transform: rotate(14deg); } }
@keyframes blink { 0%,94%,100% { transform: scaleY(1); } 97% { transform: scaleY(.1); } }
@keyframes step { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-2.5px); } }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-1.5px); } }
@keyframes pace { 0% { transform: translateX(0) scaleX(1); } 45% { transform: translateX(-26px) scaleX(1); }
                  50% { transform: translateX(-26px) scaleX(-1); } 95% { transform: translateX(0) scaleX(-1); } 100% { transform: translateX(0) scaleX(1); } }
@keyframes hop { 0%,100% { transform: translateY(0); } 40% { transform: translateY(-12px); } }
@keyframes shake { 0%,100% { transform: translateX(0); } 25% { transform: translateX(-4px); } 75% { transform: translateX(4px); } }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
.bubble { position: absolute; bottom: 52px; right: -6px; min-width: 150px; max-width: 280px; padding: 8px 11px;
          background: #1c1f29; color: #eef0f6; border: 1px solid #343a4c; border-radius: 12px; box-shadow: 0 6px 20px rgba(0,0,0,.3);
          pointer-events: auto; opacity: 0; transform: translateY(4px); transition: opacity .2s, transform .2s; }
.bubble.show { opacity: 1; transform: none; }
.bubble::after { content: ""; position: absolute; bottom: -6px; right: 20px; width: 10px; height: 10px; background: #1c1f29;
                 border-right: 1px solid #343a4c; border-bottom: 1px solid #343a4c; transform: rotate(45deg); }
.bubble .row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 7px; }
.bubble button { all: unset; cursor: pointer; padding: 4px 10px; border-radius: 999px; background: #ff9f43; color: #1a1205; font-weight: 600; font-size: 12px; }
.bubble button.ghost { background: #2a2f3e; color: #eef0f6; }
.bubble .rule { margin-top: 6px; padding: 5px 7px; border-radius: 8px; background: #11131a; font-size: 12px; }
.bubble .rule b { color: #ffc078; font-weight: 600; }
.bubble .muted { color: #9aa1b3; font-size: 11px; }
.wrap.leftside .bubble, .wrap.leftside .panel { right: auto; left: -6px; }
.wrap.leftside .bubble::after { right: auto; left: 20px; }
.wrap.below .bubble, .wrap.pbelow .panel { bottom: auto; top: 52px; }
.wrap.below .bubble::after { display: none; }
.badge { position: absolute; top: -4px; left: -4px; min-width: 18px; height: 18px; border-radius: 999px; background: #ff9f43;
         color: #1a1205; font: 700 11px/18px -apple-system, sans-serif; text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.35);
         pointer-events: none; transform: scale(0); transition: transform .2s; }
.badge.show { transform: scale(1); }
/* The chat: click the cat (or tap Option twice) to change the draft or ask why. */
.panel { position: absolute; bottom: 52px; right: -6px; width: 300px; padding: 10px; display: none; pointer-events: auto; overflow-y: auto;
         background: #1c1f29; color: #eef0f6; border: 1px solid #343a4c; border-radius: 14px; box-shadow: 0 10px 28px rgba(0,0,0,.35); }
.panel.show { display: block; }
.panel .status { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 12px; color: #c9cede; }
.panel .link { all: unset; cursor: pointer; color: #ffc078; font-size: 12px; white-space: nowrap; }
.panel .lesson { margin-top: 6px; font-size: 12px; color: #c9cede; }
.panel .log { max-height: 170px; overflow: auto; margin: 8px 0; display: flex; flex-direction: column; gap: 5px; }
.panel .log:empty { display: none; }
.panel .me { align-self: flex-end; max-width: 85%; padding: 4px 9px; border-radius: 10px 10px 2px 10px; background: #ff9f43; color: #1a1205; font-size: 12px; }
.panel .it { align-self: flex-start; max-width: 94%; padding: 5px 9px; border-radius: 10px 10px 10px 2px; background: #11131a; font-size: 12px; }
.panel .it .rule { margin-top: 4px; }
.panel .it b { color: #ffc078; font-weight: 600; }
.panel .chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0; }
.panel .chip { all: unset; cursor: pointer; padding: 3px 9px; border-radius: 999px; background: #2a2f3e; color: #eef0f6; font-size: 12px; }
.panel .chip:hover { background: #3a4154; }
.panel form { display: flex; gap: 6px; }
.panel input { all: unset; flex: 1; min-width: 0; padding: 7px 9px; border-radius: 9px; background: #11131a; border: 1px solid #343a4c; color: #eef0f6; font-size: 13px; }
.panel input::placeholder { color: #7d8497; }
.panel input:focus { border-color: #ff9f43; }
.panel .hint, .panel .muted { color: #7d8497; font-size: 10.5px; }
.panel .hint { margin-top: 6px; }
`;

  const host = document.createElement("div");
  host.id = "purrsona-cat";
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${CSS}</style><div class="wrap hidden"><div class="panel"></div><div class="bubble"></div>
    <div class="body" title="Click me to change the draft or ask why"><div class="inner">${CAT_SVG}</div><div class="badge"></div></div></div>`;
  const wrap = root.querySelector(".wrap");
  const body = root.querySelector(".body");
  const bubble = root.querySelector(".bubble");
  const panel = root.querySelector(".panel");
  const badge = root.querySelector(".badge");
  let box = null, bubbleTimer = null, onClick = () => {}, origin = { x: 0, y: 0 };

  // A modal (LinkedIn's "Start a post") sits in the browser's top layer, above any z-index,
  // so the cat has to live inside that dialog to be seen.
  const mount = (el) => {
    const into = el.closest("dialog") || document.body || document.documentElement;
    if (host.parentElement !== into) into.appendChild(host);
  };
  // Where the cat sits: on the top-right edge of the box. null = the box has scrolled out of view
  // (then the cat hides instead of clinging to the window's edge).
  const home = () => {
    if (!box || !box.isConnected) return null;
    const r = box.getBoundingClientRect();
    if (!r.width || r.bottom < 24 || r.top > window.innerHeight - 24) return null;
    return { left: Math.min(window.innerWidth - 54, r.right - 54), top: Math.max(4, r.top - 44) };
  };
  const place = (pos) => { wrap.style.left = `${pos.left - origin.x}px`; wrap.style.top = `${pos.top - origin.y}px`; };
  const reposition = () => {
    if (wrap.classList.contains("moving")) return;
    if (box && !box.isConnected) return;  // the page swapped the box out (e.g. after sending): stay put
    const h = home();
    wrap.classList.toggle("offscreen", !h);
    if (h) {
      place(h);
      wrap.classList.toggle("leftside", h.left < 260);   // room for the bubble and chat on the screen
      wrap.classList.toggle("below", h.top < 240);
      fitPanel(h.top);
    }
  };
  // The chat opens above the cat when there's room (so you still see the box), else below; it scrolls if it must.
  const fitPanel = (top) => {
    const below = top < 220;
    wrap.classList.toggle("pbelow", below);
    panel.style.maxHeight = `${Math.max(160, below ? window.innerHeight - top - 64 : top - 12)}px`;
  };
  // Inside a dialog with a transform, "fixed" is measured from the dialog, not the window: find our origin.
  const measure = () => {
    wrap.style.left = "0px"; wrap.style.top = "0px";
    const r = wrap.getBoundingClientRect();
    origin = { x: r.left, y: r.top };
  };
  window.addEventListener("scroll", () => requestAnimationFrame(reposition), true);
  window.addEventListener("resize", () => requestAnimationFrame(reposition));
  body.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); onClick(); });
  // Clicking the cat or its bubble keeps focus in the box (the chat's own text field can take focus).
  wrap.addEventListener("mousedown", (e) => { if (!e.target.closest("input")) e.preventDefault(); });
  // Keys typed in the chat stay in the chat.
  panel.addEventListener("keydown", (e) => e.stopPropagation());

  function setMood(mood) {
    body.classList.remove("thinking", "happy", "error", "walking");
    if (mood) body.classList.add(mood);
  }

  function say(html, { ms = 0 } = {}) {
    clearTimeout(bubbleTimer);
    if (panel.classList.contains("show")) return bubble;  // the chat is open: it shows the news instead
    bubble.innerHTML = html;
    bubble.classList.add("show");
    if (ms) bubbleTimer = setTimeout(() => bubble.classList.remove("show"), ms);
    return bubble;
  }
  const quiet = () => { clearTimeout(bubbleTimer); bubble.classList.remove("show"); };

  window.PurrsonaCat = {
    attach(el) {
      mount(el);
      box = el;
      wrap.classList.remove("hidden", "moving");
      measure();
      reposition();
    },
    detach() { box = null; wrap.classList.add("hidden"); quiet(); panel.classList.remove("show"); },
    openPanel(html) { quiet(); panel.innerHTML = html; panel.classList.add("show"); reposition(); return panel; },
    closePanel() { panel.classList.remove("show"); },
    panelOpen: () => panel.classList.contains("show"),
    badge(text) { if (text) badge.textContent = text; badge.classList.toggle("show", !!text); },
    onClick(fn) { onClick = fn; },
    mood: setMood,
    say, quiet,
    // Walk along the top of the reply box and back, while the draft lands in it.
    async walkAlong() {
      const start = home();
      if (!start) return;
      const r = box.getBoundingClientRect();
      wrap.classList.add("moving");
      setMood("walking");
      body.classList.add("flip");
      place({ left: Math.max(4, r.left + 4), top: start.top });
      await new Promise((ok) => setTimeout(ok, 950));
      body.classList.remove("flip");
      place(start);
      await new Promise((ok) => setTimeout(ok, 950));
      wrap.classList.remove("moving");
      setMood(null);
    },
  };
})();
