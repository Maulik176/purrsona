// Purrsona content script: the cat by your reply box, Option+M to draft, learning from what you send.
// Same file runs in the Chrome extension (talks to the bridge through background.js) and on the
// practice page (talks to the bridge directly, same origin).
(() => {
  if (window.__purrsona) return;
  window.__purrsona = true;
  const IN_EXTENSION = typeof chrome !== "undefined" && !!(chrome.runtime && chrome.runtime.id);
  document.documentElement.dataset.purrsonaExt = IN_EXTENSION ? "1" : "page";

  const cat = window.PurrsonaCat;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const first = (name) => (name || "").split(" ")[0];
  const norm = (s) => String(s || "").replace(/\s+/g, " ").trim();

  async function bridge(path, body) {
    if (IN_EXTENSION) {
      const r = await chrome.runtime.sendMessage({ type: "bridge", path, body });
      if (!r || !r.ok) throw new Error(r?.data?.detail || (r?.status === 0 ? "Purrsona's bridge isn't running" : `error ${r?.status}`));
      return r.data;
    }
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data;
  }

  // ---- Reply boxes -------------------------------------------------------------------------
  const isEditable = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable || el.getAttribute?.("role") === "textbox");
  function editableFrom(el) {
    while (el && el !== document.body) {
      if (isEditable(el)) return el.tagName === "TEXTAREA" ? el : (el.closest('[contenteditable="true"]') || el);
      el = el.parentElement;
    }
    return null;
  }
  const readBox = (box) => (box.tagName === "TEXTAREA" ? box.value : box.innerText);

  // Put the whole draft in at once. Rich editors (LinkedIn's ProseMirror, X's Draft.js) keep their own
  // copy of the text, so we paste instead of setting it, check it landed, and fall back step by step.
  function insertText(box, text) {
    box.focus();
    if (box.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(box, text);
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    const range = document.createRange();
    range.selectNodeContents(box);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    dt.setData("text/html", text.split(/\n{2,}/).map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join(""));
    box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    if (norm(readBox(box)) === norm(text)) return true;
    document.execCommand("selectAll", false);
    document.execCommand("insertText", false, text);
    if (norm(readBox(box)) === norm(text)) return true;
    navigator.clipboard?.writeText(text).catch(() => {});
    return false;
  }

  // ---- Site adapters: who wrote the post, what it says, where the send button is ------------
  const ADAPTERS = [
    {
      name: "practice",
      match: () => location.pathname.startsWith("/practice"),
      context(box) {
        const card = box.closest("[data-post]");
        if (!card) return null;
        return { person_id: card.dataset.authorId, person_name: card.dataset.authorName,
                 platform: card.dataset.platform || "linkedin", post_text: card.querySelector(".post-text")?.innerText || "" };
      },
      sendButton: (box) => box.closest("[data-post]")?.querySelector("[data-send]"),
    },
    {
      name: "linkedin",
      match: () => location.hostname.endsWith("linkedin.com"),
      context(box) {
        const card = box.closest('[data-urn], [componentkey*="update"], .feed-shared-update-v2, article');
        const link = card?.querySelector('a[href*="/in/"]');
        const slug = link?.getAttribute("href")?.match(/\/in\/([^/?#]+)/)?.[1];
        if (!slug) return null;
        const name = (card.querySelector('.update-components-actor__title span[aria-hidden="true"], .update-components-actor__title')?.innerText
                      || link.innerText || slug).split("\n")[0].trim();
        const text = card.querySelector('[data-testid="expandable-text-box"], .update-components-text, .feed-shared-inline-show-more-text')?.innerText || "";
        return { person_id: decodeURIComponent(slug), person_name: name, platform: "linkedin", post_text: text.trim().slice(0, 3000) };
      },
      sendButton(box) {
        const form = box.closest("form") || box.closest('[class*="comment-box"]');
        return form?.querySelector('button[type="submit"], button[class*="submit"]');
      },
    },
    {
      name: "x",
      match: () => /(^|\.)(x|twitter)\.com$/.test(location.hostname),
      context(box) {
        const top = box.getBoundingClientRect().top;
        const above = [...document.querySelectorAll('article[data-testid="tweet"]')].filter((a) => a.getBoundingClientRect().top < top).pop();
        const user = above?.querySelector('[data-testid="User-Name"]')?.innerText || "";
        const handle = (user.match(/@(\w+)/) || [])[1];
        if (!handle) return null;
        return { person_id: handle.toLowerCase(), person_name: user.split("\n")[0] || handle, platform: "x",
                 post_text: above.querySelector('[data-testid="tweetText"]')?.innerText || "" };
      },
      sendButton: () => document.querySelector('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]'),
    },
  ];
  const adapter = () => ADAPTERS.find((a) => a.match());

  // ---- The flow ----------------------------------------------------------------------------
  let current = null, last = null, busy = false, circles = ["Friends", "Work", "Clients"];

  document.addEventListener("focusin", (e) => {
    const box = editableFrom(e.target);
    if (box && adapter()) { current = box; cat.attach(box); }
  }, true);
  cat.onClick(() => draft());
  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.code === "KeyM" && !e.metaKey && !e.ctrlKey) { e.preventDefault(); e.stopPropagation(); draft(); }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && last && editableFrom(e.target) === last.box) captureSend(last.box);
  }, true);
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("button");
    const send = last && adapter()?.sendButton?.(last.box);
    if (btn && send && (btn === send || send.contains(btn) || btn.contains(send))) captureSend(last.box);
  }, true);
  if (IN_EXTENSION) chrome.runtime.onMessage.addListener((m) => { if (m.type === "trigger") draft(); });

  function askCircle(ctx) {
    return new Promise((resolve) => {
      const b = cat.say(`<b>${esc(first(ctx.person_name))}</b> is new to me. Who is ${esc(first(ctx.person_name))} to you?
        <div class="row">${circles.map((c) => `<button data-c="${esc(c)}">${esc(c)}</button>`).join("")}
        <button class="ghost" data-c="">skip</button></div>`);
      b.querySelectorAll("button").forEach((el) => el.addEventListener("click", (ev) => {
        ev.stopPropagation(); cat.quiet(); resolve(el.dataset.c || null);
      }));
    });
  }

  function showDone(r, ctx) {
    if (r.mode !== "voice") { cat.say("No key plugged in, so this is a generic reply 🙀", { ms: 5000 }); return; }
    const b = cat.say(`✨ in your voice for <b>${esc(first(ctx.person_name))}</b> · ${r.rules.length} rules
      <div class="muted">edit it if you like, then send it: I learn from what you send</div>
      <div class="row"><button data-why>why?</button></div>`, { ms: 9000 });
    b.querySelector("[data-why]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const top = [...r.rules].sort((a, z) => a.rank - z.rank).slice(0, 5);
      const label = (x) => (x.scope === "User" ? "You" : x.scope === "Person" ? first(ctx.person_name)
        : x.scope === "Platform" ? (x.scope_name === "x" ? "X" : "LinkedIn") : x.scope_name);
      cat.say(`<b>Why it sounds like you</b>${top.map((x) => `<div class="rule"><b>${esc(label(x))}</b> · ${esc(x.text)}
        <span class="muted">· from ${x.evidence} ${x.evidence === 1 ? "edit" : "edits"}</span></div>`).join("")}
        <div class="muted" style="margin-top:6px">the narrowest rule of each kind wins</div>`, { ms: 12000 });
    });
  }

  async function draft() {
    if (busy) return;
    const box = current || editableFrom(document.activeElement);
    const ad = adapter();
    if (!box || !ad) return;
    cat.attach(box);
    const ctx = ad.context(box);
    if (!ctx) { cat.mood("error"); cat.say("I can't tell who you're replying to here 🙀", { ms: 3500 }); return; }
    busy = true;
    try {
      cat.mood("thinking");
      cat.say(`reading ${esc(first(ctx.person_name))}'s post…`);
      const who = await bridge(`/person?id=${encodeURIComponent(ctx.person_id)}`);
      if (who.circles) circles = who.circles;
      if (who.plugged && !who.known) {
        cat.mood(null);
        ctx.circle_hint = await askCircle(ctx);
        cat.mood("thinking");
      }
      const note = readBox(box).trim();  // you already started typing: polish your words, don't replace them
      cat.say(note ? "polishing your note in your voice…"
                   : who.plugged ? "checking your voice graph…" : "no key plugged in: writing a generic reply…");
      const r = await bridge("/draft", { ...ctx, user_note: note || null });
      ctx.note = note || null;
      cat.quiet();
      const walking = cat.walkAlong();
      const ok = insertText(box, r.draft);
      await walking;
      last = { box, ctx, r };
      cat.mood("happy");
      if (ok) showDone(r, ctx);
      else cat.say("Your draft is on the clipboard: press ⌘V to paste it", { ms: 6000 });
    } catch (e) {
      cat.mood("error");
      cat.say(`🙀 ${esc(e.message)}`, { ms: 5000 });
    } finally {
      busy = false;
      setTimeout(() => cat.mood(null), 1200);
    }
  }

  // You pressed send: learn from the difference between the draft and what you actually sent.
  async function captureSend(box) {
    if (!last || last.box !== box || last.r.mode !== "voice") return;
    const sent = readBox(box).trim();   // read now, before the page clears the box
    const { r, ctx } = last;
    last = null;
    if (!sent) return;
    try {
      cat.mood("thinking");
      cat.say("learning from what you sent…");
      const out = await bridge("/learn", { draft_id: r.draft_id, draft_text: r.draft, sent_text: sent,
        person_id: ctx.person_id, person_name: ctx.person_name, platform: ctx.platform,
        circle: ctx.circle_hint || r.person?.circle || null, note_text: ctx.note || null });
      cat.mood("happy");
      cat.say(out.learned.length
        ? `🐾 learned: ${out.learned.map((x) => esc(x.text)).join("<br>🐾 ")}<div class="muted">saved to your key</div>`
        : `🐾 ${esc(out.note || "nothing new to learn")}`, { ms: 7000 });
    } catch (e) {
      cat.mood("error");
      cat.say(`🙀 couldn't learn: ${esc(e.message)}`, { ms: 4000 });
    } finally {
      setTimeout(() => cat.mood(null), 1200);
    }
  }
})();
