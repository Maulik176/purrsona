// Purrsona content script: the cat by the box you're typing in, Option+M to draft, learning from what you send.
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
  const slug = (name) => norm(name).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");

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

  // ---- Text boxes --------------------------------------------------------------------------
  const isEditable = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable || el.getAttribute?.("role") === "textbox");
  function editableFrom(el) {
    while (el && el !== document.body) {
      if (isEditable(el)) return el.tagName === "TEXTAREA" ? el : (el.closest('[contenteditable="true"]') || el);
      el = el.parentElement;
    }
    return null;
  }
  const MENTION = '[data-type="mention"]';
  // What you wrote in the box, without @mentions (LinkedIn starts a reply to a comment with one).
  function readBox(box) {
    if (box.tagName === "TEXTAREA") return box.value;
    if (!box.querySelector(MENTION)) return box.innerText;
    const copy = box.cloneNode(true);
    copy.querySelectorAll(MENTION).forEach((m) => m.remove());
    return [...copy.childNodes].map((n) => n.textContent).join("\n");
  }

  // Put the whole draft in at once. Rich editors (LinkedIn's ProseMirror, X's Draft.js) keep their own
  // copy of the text, so we paste instead of setting it, check it landed, and fall back step by step.
  // An @mention at the start stays: the draft replaces only what comes after it.
  let composing = false;
  document.addEventListener("compositionstart", () => { composing = true; }, true);
  document.addEventListener("compositionend", () => { composing = false; }, true);
  function insertText(box, text) {
    if (composing) box.blur();  // finish a half-typed accent first (macOS Option keys start one)
    box.focus();
    if (box.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(box, text);
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    const mention = box.querySelector(MENTION);
    if (mention) text = ` ${text}`;
    const select = () => {
      const range = document.createRange();
      range.selectNodeContents(box);
      const m = box.querySelector(MENTION);
      if (m) range.setStartAfter(m);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    };
    const landed = () => norm(readBox(box)) === norm(text);
    select();
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    dt.setData("text/html", text.split(/\n{2,}/).map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join(""));
    box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    if (landed()) return true;
    select();
    document.execCommand("insertText", false, text);
    if (landed()) return true;
    navigator.clipboard?.writeText(text.trim()).catch(() => {});
    return false;
  }

  // The nearest button (walking up from the box) that passes the test: finds the box's own send button.
  function nearestButton(box, test, maxUp = 16) {
    for (let el = box.parentElement, i = 0; el && el !== document.body && i < maxUp; el = el.parentElement, i++) {
      const b = [...el.querySelectorAll("button")].find(test);
      if (b) return { scope: el, button: b };
    }
    return null;
  }

  // ---- Site adapters -----------------------------------------------------------------------
  // kind(box): is this a box we help with? "reply", "post" (your own new post) or null (no cat).
  //   Checked on focus from the page's structure only; nothing is read until you ask for a draft.
  // context(box): who you're replying to and what they wrote, read when you press Option+M or click the cat.
  // isSend(button, box): is this the button that sends what's in the box?

  // LinkedIn, as checked on the real page (Oct 16): post cards have no stable class names, but every
  // post has an "Open control menu for post by <name>" button; the first profile link in a card can be
  // someone who only liked it; and "Reply" under a comment starts the box with an @mention of the commenter.
  const LI_MENU = 'button[aria-label^="Open control menu for post by"]';
  const LI_COMMENT = '[componentkey^="replaceableComment_"]';
  let liReplyTo = null;  // the comment whose "Reply" you clicked last
  function liCard(box) {
    for (let el = box.parentElement; el && el !== document.body; el = el.parentElement) {
      const n = el.querySelectorAll(LI_MENU).length;
      if (n) return n === 1 ? el : null;  // more than one: we walked out of the post into the feed
    }
    return null;
  }
  function liProfile(scope, name) {
    const links = [...scope.querySelectorAll('a[href*="/in/"], a[href*="/company/"]')];
    const a = links.find((l) => norm(l.innerText).startsWith(name) || (l.getAttribute("aria-label") || "").includes(name));
    const m = a?.getAttribute("href").match(/\/(in|company)\/([^/?#]+)/);
    if (!m) return slug(name);
    return m[1] === "company" ? `company-${decodeURIComponent(m[2])}` : decodeURIComponent(m[2]);
  }
  const liCommentAuthor = (c) => norm((c.querySelector('button[aria-label^="View more options for"]')
    ?.getAttribute("aria-label") || "").match(/View more options for (.+?)[’']s comment/)?.[1]);
  const sameName = (a, b) => !!a && !!b && (a.startsWith(b) || b.startsWith(a));
  const liComposer = (box) => {
    const d = box.closest("dialog");
    return location.pathname.startsWith("/sharing/compose")
      || (!!d && [...d.querySelectorAll("button")].some((b) => /^post$/i.test(norm(b.innerText))));
  };

  const ADAPTERS = [
    {
      name: "practice",
      match: () => location.pathname.startsWith("/practice"),
      kind: (box) => (box.closest("[data-post]") ? "reply" : null),
      context(box) {
        const card = box.closest("[data-post]");
        return { mode: "reply", person_id: card.dataset.authorId, person_name: card.dataset.authorName,
                 platform: card.dataset.platform || "linkedin", post_text: card.querySelector(".post-text")?.innerText || "" };
      },
      isSend: (btn, box) => btn.matches("[data-send]") && btn.closest("[data-post]") === box.closest("[data-post]"),
    },
    {
      name: "linkedin",
      match: () => location.hostname.endsWith("linkedin.com"),
      kind(box) {
        if (box.closest('header, nav, [role="search"], [role="combobox"]')) return null;
        if (liCard(box)) return "reply";
        return liComposer(box) ? "post" : null;  // anything else (messages, search…): no cat
      },
      noteClick(btn) {
        const c = btn.closest(LI_COMMENT);
        if (c && /^reply$/i.test(norm(btn.innerText) || btn.getAttribute("aria-label") || "")) liReplyTo = c;
      },
      context(box) {
        const card = liCard(box);
        if (!card) return liComposer(box) ? { mode: "post", platform: "linkedin" } : null;
        const author = norm(card.querySelector(LI_MENU).getAttribute("aria-label").replace(/^Open control menu for post by/, ""));
        const postText = norm(card.querySelector('[data-testid="expandable-text-box"]')?.innerText).slice(0, 3000);
        const mention = norm(box.querySelector(MENTION)?.innerText);
        if (mention) {  // replying to a comment: the commenter is the person, their comment is the "post"
          const c = (liReplyTo?.isConnected && card.contains(liReplyTo) && sameName(liCommentAuthor(liReplyTo), mention))
            ? liReplyTo : [...card.querySelectorAll(LI_COMMENT)].find((x) => sameName(liCommentAuthor(x), mention));
          if (c) {
            const name = liCommentAuthor(c);
            const said = norm(c.querySelector('[data-testid="expandable-text-box"]')?.innerText).slice(0, 2000);
            return { mode: "reply", person_id: liProfile(c, name), person_name: name, platform: "linkedin",
                     post_text: `${said}\n\n(Their comment is on ${author}'s post: ${postText.slice(0, 600)})` };
          }
        }
        if (!author) return null;
        return { mode: "reply", person_id: liProfile(card, author), person_name: author, platform: "linkedin", post_text: postText };
      },
      isSend(btn, box) {
        const test = (b) => /^(comment|reply|post)$/i.test(norm(b.innerText))
          && !(b.closest(LI_COMMENT) && !b.closest(LI_COMMENT).contains(box));  // not a comment's own "Reply" link
        if (!test(btn)) return false;
        const near = nearestButton(box, test);
        return !!near && near.scope.contains(btn);
      },
    },
    {
      name: "x",
      match: () => /(^|\.)(x|twitter)\.com$/.test(location.hostname),
      kind(box) {
        if (!(box.closest('[data-testid^="tweetTextarea_"]') || box.querySelector('[data-testid^="tweetTextarea_"]'))) return null;
        return this.tweetAbove(box) ? "reply" : "post";
      },
      tweetAbove(box) {
        const top = box.getBoundingClientRect().top;
        const scope = box.closest('[role="dialog"]') || document;
        return [...scope.querySelectorAll('article[data-testid="tweet"]')].filter((a) => a.getBoundingClientRect().top < top).pop();
      },
      context(box) {
        const above = this.tweetAbove(box);
        if (!above) return { mode: "post", platform: "x" };
        const user = above.querySelector('[data-testid="User-Name"]')?.innerText || "";
        const handle = (user.match(/@(\w+)/) || [])[1];
        if (!handle) return null;
        return { mode: "reply", person_id: handle.toLowerCase(), person_name: user.split("\n")[0] || handle, platform: "x",
                 post_text: above.querySelector('[data-testid="tweetText"]')?.innerText || "" };
      },
      isSend: (btn) => btn.matches('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]'),
    },
  ];
  const adapter = () => ADAPTERS.find((a) => a.match());
  const kindOf = (box) => (box && adapter()?.kind(box)) || null;

  // ---- The cat comes and goes with your typing ---------------------------------------------
  let current = null, last = null, busy = false, learning = false, circles = ["Friends", "Work", "Clients"];
  let hideTimer = null, lingerUntil = 0;

  document.addEventListener("focusin", (e) => {
    const box = editableFrom(e.target);
    if (kindOf(box)) { clearTimeout(hideTimer); current = box; cat.attach(box); }
    else hideSoon();
  }, true);
  document.addEventListener("focusout", () => hideSoon(), true);
  // Hide the cat once you've left the box, unless it's working for you or still showing you something.
  function hideSoon(ms = 250) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      const wait = lingerUntil - Date.now();
      if (busy || learning || wait > 0) return hideSoon(Math.max(wait, 250));
      if (current && editableFrom(document.activeElement) === current) return;
      current = null;
      cat.detach();
    }, ms);
  }
  const linger = (ms) => { lingerUntil = Date.now() + ms; hideSoon(ms); };

  // ---- Option+M -----------------------------------------------------------------------------
  // On a Mac, Option+M also types a character (µ, or ˛ on some keyboard layouts). We stop it, and read
  // your note on keydown, before that character could land.
  let swallowUntil = 0;
  window.addEventListener("keydown", (e) => {
    if (e.altKey && e.code === "KeyM" && !e.metaKey && !e.ctrlKey) {
      const box = editableFrom(e.target) || current;
      if (!kindOf(box)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      swallowUntil = Date.now() + 600;
      draft(box, readBox(box));
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && last && editableFrom(e.target) === last.box) captureSend(last.box);
  }, true);
  window.addEventListener("keypress", (e) => {
    if (e.altKey && Date.now() < swallowUntil) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  window.addEventListener("beforeinput", (e) => {
    if (Date.now() < swallowUntil && /^insert(Text|CompositionText)$/.test(e.inputType) && (e.data || "").length <= 2) {
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true);

  cat.onClick(() => draft(current));
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.("button");
    if (!btn) return;
    const ad = adapter();
    ad?.noteClick?.(btn);
    if (last && last.box.isConnected && ad?.isSend?.(btn, last.box)) captureSend(last.box);
  }, true);
  if (IN_EXTENSION) chrome.runtime.onMessage.addListener((m) => { if (m.type === "trigger") draft(current); });

  // ---- Drafting ----------------------------------------------------------------------------
  // Resolves with the circle, null for "skip", or undefined if you walked away without answering.
  function askCircle(ctx) {
    return new Promise((resolve) => {
      const b = cat.say(`<b>${esc(first(ctx.person_name))}</b> is new to me. Who is ${esc(first(ctx.person_name))} to you?
        <div class="row">${circles.map((c) => `<button data-c="${esc(c)}">${esc(c)}</button>`).join("")}
        <button class="ghost" data-c="">skip</button></div>`);
      const timer = setTimeout(() => { cat.quiet(); resolve(undefined); }, 30000);
      b.querySelectorAll("button").forEach((el) => el.addEventListener("click", (ev) => {
        ev.stopPropagation(); clearTimeout(timer); cat.quiet(); resolve(el.dataset.c || null);
      }));
    });
  }

  function showDone(r, ctx) {
    if (r.mode !== "voice") { cat.say("No key plugged in, so this is a generic draft 🙀", { ms: 5000 }); return 5000; }
    const forWhom = ctx.mode === "post" ? "your post, in your voice" : `in your voice for <b>${esc(first(ctx.person_name))}</b>`;
    const b = cat.say(`✨ ${forWhom} · ${r.rules.length} rules
      <div class="muted">${ctx.mode === "post" ? "edit it if you like, then post it yourself"
                                                : "edit it if you like, then send it: I learn from what you send"}</div>
      <div class="row"><button data-why>why?</button></div>`, { ms: 9000 });
    b.querySelector("[data-why]").addEventListener("click", (ev) => {
      ev.stopPropagation();
      const top = [...r.rules].sort((a, z) => a.rank - z.rank).slice(0, 5);
      const label = (x) => (x.scope === "User" ? "You" : x.scope === "Person" ? first(ctx.person_name)
        : x.scope === "Platform" ? (x.scope_name === "x" ? "X" : "LinkedIn") : x.scope_name);
      cat.say(`<b>Why it sounds like you</b>${top.map((x) => `<div class="rule"><b>${esc(label(x))}</b> · ${esc(x.text)}
        <span class="muted">· from ${x.evidence} ${x.evidence === 1 ? "edit" : "edits"}</span></div>`).join("")}
        <div class="muted" style="margin-top:6px">the narrowest rule of each kind wins</div>`, { ms: 12000 });
      linger(12000);
    });
    return 9000;
  }

  // Two ways in: an empty box = "draft a reply for me"; a box with your own words = "polish my note in
  // my voice" (and what you typed is a strong lesson). A new post always starts from your note.
  async function draft(box, noteSnapshot) {
    box = box || editableFrom(document.activeElement);
    if (busy || !kindOf(box)) return;
    current = box;
    cat.attach(box);
    const ctx = adapter().context(box);
    if (!ctx) { cat.mood("error"); cat.say("I can't see whose post this is 🙀", { ms: 3500 }); linger(3500); return; }
    const note = (noteSnapshot ?? readBox(box)).trim();
    if (ctx.mode === "post" && !note) {
      cat.say("Writing your own post? Type a few words about it, then press ⌥M", { ms: 5000 });
      linger(5000);
      return;
    }
    busy = true;
    let showFor = 2500;
    try {
      cat.mood("thinking");
      let plugged = true, remembered = "";
      if (ctx.mode === "reply") {
        cat.say(`reading ${esc(first(ctx.person_name))}'s post…`);
        const who = await bridge(`/person?id=${encodeURIComponent(ctx.person_id)}`);
        plugged = who.plugged;
        if (who.circles) circles = who.circles;
        if (who.known) {  // the graph remembers them: say so
          const when = who.last_sent ? `, last on ${new Date(who.last_sent).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "";
          remembered = `<div class="muted">${esc(who.circle)}${
            who.sent ? ` · you've written to ${esc(first(ctx.person_name))} ${who.sent}× with me${when}` : ""}</div>`;
        }
        if (who.plugged && !who.known) {
          cat.mood(null);
          const choice = await askCircle(ctx);
          if (choice === undefined) return;  // no answer: no draft
          ctx.circle_hint = choice;
          cat.mood("thinking");
        }
      }
      cat.say((ctx.mode === "post" ? "writing your post in your voice…"
              : note ? "polishing your note in your voice…"
              : plugged ? "checking your voice graph…" : "no key plugged in: writing a generic reply…") + remembered);
      const r = await bridge("/draft", { ...ctx, user_note: note || null });
      ctx.note = note || null;
      cat.quiet();
      const walking = cat.walkAlong();
      const ok = insertText(box, r.draft);
      await walking;
      last = { box, ctx, r };
      cat.mood("happy");
      if (ok) showFor = showDone(r, ctx);
      else { cat.say("Your draft is on the clipboard: press ⌘V to paste it", { ms: 6000 }); showFor = 6000; }
    } catch (e) {
      cat.mood("error");
      cat.say(`🙀 ${esc(e.message)}`, { ms: 5000 });
      showFor = 5000;
    } finally {
      busy = false;
      setTimeout(() => cat.mood(null), 1200);
      linger(showFor);
    }
  }

  // You pressed send: every send leaves a memory. Who you wrote to (and their circle, if they're new),
  // plus either the lessons from your edits or "the draft was right as it was".
  // (Replies only for now: the Learner saves rules about a person.)
  async function captureSend(box) {
    if (!last || last.box !== box || last.r.mode !== "voice" || last.ctx.mode !== "reply") return;
    const sent = readBox(box).trim();   // read now, before the page clears the box
    const { r, ctx } = last;
    last = null;
    if (!sent) return;
    learning = true;
    let showFor = 4000;
    try {
      cat.mood("thinking");
      cat.say("learning from what you sent…");
      const out = await bridge("/learn", { draft_id: r.draft_id, draft_text: r.draft, sent_text: sent,
        person_id: ctx.person_id, person_name: ctx.person_name, platform: ctx.platform,
        circle: ctx.circle_hint || r.person?.circle || null, note_text: ctx.note || null });
      cat.mood("happy");
      const lines = out.learned.map((x) => `🐾 learned: ${esc(x.text)}`);
      if (out.note) lines.push(`🐾 ${esc(out.note)}`);
      cat.say(`${lines.join("<br>") || "🐾 nothing new"}${out.saved ? '<div class="muted">saved to your key</div>' : ""}`, { ms: 7000 });
      showFor = 7000;
    } catch (e) {
      cat.mood("error");
      cat.say(`🙀 couldn't learn: ${esc(e.message)}`, { ms: 4000 });
    } finally {
      learning = false;
      setTimeout(() => cat.mood(null), 1200);
      linger(showFor);
    }
  }
})();
