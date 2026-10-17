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

  //Text boxes: find the nearest editable element.
  const isEditable = (el) => !!el && (el.tagName === "TEXTAREA" || el.isContentEditable || el.getAttribute?.("role") === "textbox");
  // Some sites (new Reddit) put their text boxes inside web components: step out of those too.
  const up = (el) => el.parentElement || (el.getRootNode?.() instanceof ShadowRoot ? el.getRootNode().host : null);
  function editableFrom(el) {
    while (el && el !== document.body && el.nodeType === 1) {
      if (isEditable(el)) return el.tagName === "TEXTAREA" ? el : (el.closest('[contenteditable="true"]') || el);
      el = up(el);
    }
    return null;
  }
  function closestDeep(el, selector) {
    for (; el && el.nodeType === 1; el = up(el)) if (el.matches(selector)) return el;
    return null;
  }
  const deepActive = () => { let a = document.activeElement; while (a?.shadowRoot?.activeElement) a = a.shadowRoot.activeElement; return a; };
  const realTarget = (e) => e.composedPath?.()[0] || e.target;
  const MENTION = '[data-type="mention"]';
  // Read the box, without @mentions (LinkedIn starts a reply to a comment with one).
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
    for (let el = up(box), i = 0; el && el !== document.body && i < maxUp; el = up(el), i++) {
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
    {
      // Reddit: public posts and comments. New Reddit keeps the author in attributes
      // (<shreddit-post author=…>, <shreddit-comment author=…>); old Reddit in .thing[data-author].
      name: "reddit",
      match: () => /(^|\.)reddit\.com$/.test(location.hostname),
      kind: (box) => (closestDeep(box, "shreddit-composer, comment-composer-host, shreddit-comment, .usertext-edit, form.usertext")
        ? "reply" : null),
      context(box) {
        const old = closestDeep(box, ".thing[data-author]") || (closestDeep(box, ".commentarea") && document.querySelector("#siteTable .thing.link[data-author]"));
        if (old) {
          const author = old.dataset.author;
          const said = old.querySelector(".usertext-body .md")?.innerText || old.querySelector("a.title")?.innerText || "";
          return { mode: "reply", person_id: `reddit-${author.toLowerCase()}`, person_name: `u/${author}`, platform: "reddit",
                   post_text: norm(said).slice(0, 3000) };
        }
        const comment = closestDeep(box, "shreddit-comment");
        const post = document.querySelector("shreddit-post");
        const target = comment || post;
        const author = target?.getAttribute("author");
        if (!author) return null;
        const said = comment ? comment.querySelector('[slot="comment"]')?.innerText
          : `${post.getAttribute("post-title") || ""}\n${post.querySelector('[slot="text-body"]')?.innerText || ""}`;
        return { mode: "reply", person_id: `reddit-${author.toLowerCase()}`, person_name: `u/${author}`, platform: "reddit",
                 post_text: norm(said).slice(0, 3000) };
      },
      isSend(btn, box) {
        if (!/^(comment|reply|save)$/i.test(norm(btn.innerText))) return false;
        const scope = closestDeep(box, "shreddit-composer, comment-composer-host, form");
        return !!scope && (scope.contains(btn) || closestDeep(btn, "shreddit-composer, comment-composer-host, form") === scope);
      },
    },
  ];
  // Your-words mode, for every other message box on our sites (Slack, Teams, Gmail, DMs). Private
  // conversations are off limits (the hackathon's data rule, and plain respect): the cat never reads the
  // page here, only what you typed, and asks who it's for.
  const PLATFORMS = [[/linkedin\.com$/, "linkedin"], [/(^|\.)(x|twitter)\.com$/, "x"], [/reddit\.com$/, "reddit"],
                     [/slack\.com$/, "slack"], [/teams\.(microsoft|live)\.com$/, "teams"], [/mail\.google\.com$/, "email"]];
  const platformOf = (box) => box?.closest?.("[data-platform]")?.dataset.platform
    || (PLATFORMS.find(([re]) => re.test(location.hostname)) || [])[1] || "web";
  const WORDS = {
    name: "words",
    kind(box) {
      if (closestDeep(box, 'header, nav, [role="search"], [role="combobox"]')) return null;
      const r = box.getBoundingClientRect();
      return r.width >= 120 && r.height >= 16 ? "note" : null;  // a message box, not a tiny field
    },
    context: (box) => ({ mode: "note", platform: platformOf(box) }),
    isSend: (btn) => /^send\b/i.test(norm(btn.innerText) || btn.getAttribute("aria-label") || btn.getAttribute("data-tooltip") || ""),
    sendsOnEnter: () => !/mail\.google\.com$/.test(location.hostname),  // chat apps send on Enter; email doesn't
  };
  // The site's own adapter if it knows this box (a reply or a post), else your-words mode.
  function adapterFor(box) {
    if (!box) return null;
    const site = ADAPTERS.find((a) => a.match());
    const kind = site?.kind(box);
    if (kind) return { adapter: site, kind };
    const words = WORDS.kind(box);
    return words ? { adapter: WORDS, kind: words } : null;
  }
  const kindOf = (box) => adapterFor(box)?.kind || null;

  //The cat comes and goes with your typing 
  let current = null, last = null, busy = false, learning = false, circles = ["Friends", "Work", "Clients"];
  let hideTimer = null, lingerUntil = 0, lastLesson = null, hinted = false;
  const inCat = (e) => e.composedPath().some((n) => n.id === "purrsona-cat");

  document.addEventListener("focusin", (e) => {
    if (inCat(e)) return;  // typing in the cat's chat
    const box = editableFrom(realTarget(e));
    if (kindOf(box)) {
      clearTimeout(hideTimer);
      if (box !== current) cat.closePanel();
      current = box;
      cat.attach(box);
    } else hideSoon();
  }, true);
  document.addEventListener("focusout", () => hideSoon(), true);
  // A click anywhere but the box or the cat closes the chat.
  document.addEventListener("mousedown", (e) => {
    if (cat.panelOpen() && !inCat(e) && !(current && current.contains(e.target))) { cat.closePanel(); hideSoon(); }
  }, true);
  // Hide the cat once you've left the box, unless it's working for you or its chat is open.
  function hideSoon(ms = 250) {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      const wait = lingerUntil - Date.now();
      if (busy || learning || cat.panelOpen() || wait > 0) return hideSoon(Math.max(wait, 250));
      if (current && editableFrom(deepActive()) === current) return;
      current = null;
      cat.detach();
    }, ms);
  }
  const linger = (ms) => { lingerUntil = Date.now() + ms; hideSoon(ms); };

  // ---- Keys: Option+M writes, tapping Option twice opens the chat ----------------------------
  // On a Mac, Option+M also types a character (µ, or ˛ on some keyboard layouts). We stop it, and read
  // your note on keydown, before that character could land.
  let swallowUntil = 0, altAt = 0;
  window.addEventListener("keydown", (e) => {
    if (e.key === "Alt") {
      if (e.repeat) return;
      const now = Date.now();
      if (now - altAt < 400) {  // a second tap, with no other key in between
        altAt = 0;
        const box = editableFrom(realTarget(e)) || current;
        if (kindOf(box)) toggleChat(box);
      } else altAt = now;
      return;
    }
    altAt = 0;
    if (e.altKey && e.code === "KeyM" && !e.metaKey && !e.ctrlKey) {
      const box = editableFrom(realTarget(e)) || current;
      if (!kindOf(box)) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      swallowUntil = Date.now() + 600;
      cat.closePanel();
      draft(box, readBox(box));
      return;
    }
    const inLast = last && editableFrom(realTarget(e)) === last.box;
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && inLast) captureSend(last.box);
    // Chat apps send on a plain Enter: learn from the box before the app clears it.
    else if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.isComposing && inLast
             && adapterFor(last.box)?.adapter.sendsOnEnter?.()) captureSend(last.box);
  }, true);
  window.addEventListener("keypress", (e) => {
    if (e.altKey && Date.now() < swallowUntil) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  window.addEventListener("beforeinput", (e) => {
    if (Date.now() < swallowUntil && /^insert(Text|CompositionText)$/.test(e.inputType) && (e.data || "").length <= 2) {
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true);

  cat.onClick(() => toggleChat(current));
  document.addEventListener("click", (e) => {
    const btn = e.composedPath().find((n) => n.nodeType === 1 && n.matches?.('button, [role="button"]'));
    if (!btn) return;
    ADAPTERS.find((a) => a.match())?.noteClick?.(btn);
    if (last && last.box.isConnected && adapterFor(last.box)?.adapter.isSend?.(btn, last.box)) captureSend(last.box);
  }, true);
  if (IN_EXTENSION) chrome.runtime.onMessage.addListener((m) => { if (m.type === "trigger") draft(current); });

  // Your-words mode can't see the conversation, so you say who it's for (remembered for this chat).
  const chosenFor = {};
  async function choosePerson(ctx) {
    const here = location.pathname + location.hash;
    if (chosenFor[here]) return Object.assign(ctx, chosenFor[here]);
    let people;
    try { people = await bridge("/people"); } catch { return Object.assign(ctx, { person_id: "", person_name: "" }); }
    const pick = await new Promise((resolve) => {
      const b = cat.say(`Who's this message for?
        <div class="row">${people.slice(0, 8).map((p, i) => `<button data-i="${i}">${esc(first(p.name))}</button>`).join("")}</div>
        <form><input maxlength="60" placeholder="someone else: their name"></form>
        <div class="row"><button class="ghost" data-none>no one in particular</button></div>
        <div class="muted">in private apps I only use what you typed, never the conversation</div>`);
      const timer = setTimeout(() => { cat.quiet(); resolve(undefined); }, 45000);
      const done = (v) => { clearTimeout(timer); cat.quiet(); resolve(v); };
      b.querySelectorAll("[data-i]").forEach((el) => el.addEventListener("click", (ev) => {
        ev.stopPropagation(); const p = people[+el.dataset.i]; done({ person_id: p.id, person_name: p.name });
      }));
      b.querySelector("[data-none]").addEventListener("click", (ev) => { ev.stopPropagation(); done({ person_id: "", person_name: "" }); });
      b.querySelector("form").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const t = norm(b.querySelector("input").value);
        if (t) done({ person_id: slug(t), person_name: t.replace(/\b\w/g, (c) => c.toUpperCase()) });
      });
    });
    if (pick === undefined) return undefined;
    chosenFor[here] = pick;
    return Object.assign(ctx, pick);
  }

  // ---- Drafting ----------------------------------------------------------------------------
  // Someone new: tap a circle, or say it in your own words ("my manager"), which the graph maps to a circle.
  // Resolves with {circle}, {relationship}, null for "none of these" (still remembered, just without a
  // circle), or undefined if you walked away without answering.
  function askCircle(ctx) {
    return new Promise((resolve) => {
      const who = esc(first(ctx.person_name));
      const b = cat.say(`<b>${who}</b> is new to me. Who is ${who} to you?
        <div class="row">${circles.map((c) => `<button data-c="${esc(c)}">${esc(c)}</button>`).join("")}</div>
        <form><input maxlength="60" placeholder="or in your words: my manager, a customer…"></form>
        <div class="row"><button class="ghost" data-c="">none of these</button></div>`);
      const timer = setTimeout(() => { cat.quiet(); resolve(undefined); }, 45000);
      const done = (v) => { clearTimeout(timer); cat.quiet(); resolve(v); };
      b.querySelectorAll("button").forEach((el) => el.addEventListener("click", (ev) => {
        ev.stopPropagation(); done(el.dataset.c ? { circle: el.dataset.c } : null);
      }));
      b.querySelector("form").addEventListener("submit", (ev) => {
        ev.preventDefault();
        const t = b.querySelector("input").value.trim();
        if (t) done({ relationship: t });
      });
    });
  }

  // Two ways in: an empty box = "draft a reply for me"; a box with your own words = "polish my note in
  // my voice" (and what you typed is a strong lesson). A new post always starts from your note.
  // Quiet by design: the draft just lands. The why and what the cat learned wait in its chat.
  async function draft(box, noteSnapshot) {
    box = box || editableFrom(deepActive());
    const found = adapterFor(box);
    if (busy || !found) return;
    current = box;
    cat.attach(box);
    const ctx = found.adapter.context(box);
    if (!ctx) { cat.mood("error"); cat.say("I can't see whose post this is 🙀", { ms: 3500 }); linger(3500); return; }
    const note = (noteSnapshot ?? readBox(box)).trim();
    if (ctx.mode === "post" && !note) {
      cat.say("Writing your own post? Type a few words about it, then press ⌥M", { ms: 5000 });
      linger(5000);
      return;
    }
    if (ctx.mode === "note" && !note) {
      cat.say("Type a rough message first, then press ⌥M<div class=\"muted\">here I only use your own words, never the conversation</div>", { ms: 5000 });
      linger(5000);
      return;
    }
    busy = true;
    let showFor = 1500;
    try {
      cat.mood("thinking");
      let plugged = true, remembered = "";
      if (ctx.mode === "note") {
        cat.mood(null);
        if ((await choosePerson(ctx)) === undefined) return;  // no answer: no draft
        cat.mood("thinking");
      }
      if (ctx.mode === "reply" || (ctx.mode === "note" && ctx.person_id)) {
        cat.say(ctx.mode === "reply" ? `reading ${esc(first(ctx.person_name))}'s post…` : `getting your voice for ${esc(first(ctx.person_name))}…`);
        const who = await bridge(`/person?id=${encodeURIComponent(ctx.person_id)}`);
        plugged = who.plugged;
        if (who.circles) circles = who.circles;
        if (who.known) {  // the graph remembers them: say so while it works
          const when = who.last_sent ? `, last on ${new Date(who.last_sent).toLocaleDateString(undefined, { day: "numeric", month: "short" })}` : "";
          const bits = [who.relationship && `your ${who.relationship}`, who.circle,
                        who.sent ? `you've written to ${first(ctx.person_name)} ${who.sent}× with me${when}` : ""].filter(Boolean);
          if (bits.length) remembered = `<div class="muted">${esc(bits.join(" · "))}</div>`;
        }
        // Someone new: ask once who they are to you.
        if (who.plugged && !who.known && !ctx.person_id.startsWith("company-")) {
          cat.mood(null);
          const choice = await askCircle(ctx);
          if (choice === undefined) return;  // no answer: no draft
          cat.mood("thinking");
          if (choice?.relationship) {  // your own words: the graph says which circle that is
            const rel = await bridge("/relationship", { text: choice.relationship });
            ctx.relationship = rel.name;
            ctx.circle_hint = rel.circle;
            remembered = `<div class="muted">${esc(`your ${rel.name}`)} → ${esc(rel.circle || "no circle")}</div>`;
          } else ctx.circle_hint = choice?.circle ?? null;
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
      last = { box, ctx, r, asks: [] };
      cat.mood("happy");
      if (!ok) { cat.say("Your draft is on the clipboard: press ⌘V to paste it", { ms: 6000 }); showFor = 6000; }
      else if (r.mode !== "voice") { cat.say("No key plugged in, so this is a generic draft 🙀", { ms: 3500 }); showFor = 3500; }
      else if (!hinted) { hinted = true; cat.say("✨ click me to change it", { ms: 2500 }); showFor = 2500; }
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

  // ---- The chat: change the draft ("make it shorter"), ask why, see what the cat learned ------
  const CHIPS = [["shorter", "make it shorter"], ["add my emojis", "add my usual emojis"],
                 ["more formal", "make it more formal"], ["more casual", "make it more casual"]];
  const ruleLabel = (x, ctx) => (x.scope === "User" ? "You" : x.scope === "Person" ? first(ctx.person_name)
    : x.scope === "Platform" ? (x.scope_name === "x" ? "X" : "LinkedIn") : x.scope_name);

  function toggleChat(box) {
    if (!kindOf(box)) return;
    if (cat.panelOpen()) { cat.closePanel(); box.focus(); return; }
    current = box;
    cat.attach(box);
    openChat(box);
  }

  function openChat(box) {
    const has = !!last && last.box === box;
    const voice = has && last.r.mode === "voice";
    const status = !has ? "✍️ I'll write it in your voice"
      : !voice ? "no key plugged in: this one is generic"
      : `✨ ${last.ctx.mode === "post" ? "your post" : `for <b>${esc(first(last.ctx.person_name))}</b>`} · ${last.r.rules.length} rules from your graph${
          last.r.critic?.fixed?.length ? ` · the Critic fixed ${last.r.critic.fixed.map((x) => `'${esc(x.rule)}'`).join(", ")}`
          : last.r.critic?.still?.length ? " · the Critic flagged something" : last.r.critic ? " · checked by the Critic" : ""}`;
    const chips = has ? CHIPS.map(([t, a]) => `<button class="chip" data-ask="${esc(a)}">${esc(t)}</button>`).join("")
      : `<button class="chip" data-write>${{ post: "write my post", note: "say it in my voice" }[kindOf(box)] || "write a reply"} · ⌥M</button>`;
    const p = cat.openPanel(`<div class="status"><span>${status}</span>${voice ? '<button class="link" data-why>why?</button>' : ""}</div>
      ${lastLesson ? `<div class="lesson">🐾 ${esc(lastLesson.summary)} <button class="link" data-lesson>show</button></div>` : ""}
      <div class="log"></div><div class="chips">${chips}</div>
      <form><input maxlength="300" placeholder="${has ? "Change it… e.g. mention the go-live" : "Tell me what to say…"}"></form>
      <div class="hint">Enter to send · Esc to close · tap Option twice to open me</div>`);
    cat.badge(null);
    const input = p.querySelector("input"), log = p.querySelector(".log");
    const line = (cls, html) => {
      const d = document.createElement("div");
      d.className = cls; d.innerHTML = html; log.append(d); log.scrollTop = log.scrollHeight;
      return d;
    };
    if (has) last.asks.forEach((a) => line("me", esc(a)));

    function showWhy() {
      const top = [...last.r.rules].sort((a, z) => a.rank - z.rank).slice(0, 5);
      line("it", `<b>Why it sounds like you</b>${top.map((x) => `<div class="rule"><b>${esc(ruleLabel(x, last.ctx))}</b> · ${esc(x.text)}
        <span class="muted">· from ${x.evidence} ${x.evidence === 1 ? "edit" : "edits"}</span></div>`).join("")}
        <div class="muted">the narrowest rule of each kind wins</div>`);
      // The agents' handoffs for this draft: Drafter -> Critic -> (one redo) -> Critic.
      const trail = last.r.trail || [];
      if (trail.length) line("it", `<b>How the agents made it</b><div class="rule">${trail.map((st) =>
        `${esc(st.agent)} ${esc(st.action)}${st.action === "redo" || st.action === "still breaks" ? `: ${esc(st.detail)}` : ""}`).join(" → ")}</div>`);
    }
    async function ask(text) {
      if (busy) return;
      if (!last || last.box !== box) {  // nothing drafted yet: what you typed here is the note to write from
        cat.closePanel();
        return draft(box, [readBox(box).trim(), text].filter(Boolean).join("\n"));
      }
      line("me", esc(text));
      if (/^(why|how did|how come|what rules)\b/i.test(text)) return voice ? showWhy() : line("it", "no key plugged in, so no graph behind this one");
      const reply = line("it", "on it…");
      busy = true;
      cat.mood("thinking");
      try {
        const { ctx } = last;
        const r = await bridge("/refine", { current_text: readBox(box).trim(), instruction: text, person_id: ctx.person_id || "",
          person_name: ctx.person_name || "", platform: ctx.platform, post_text: ctx.post_text || "",
          circle_hint: ctx.circle_hint || null, relationship: ctx.relationship || null, mode: ctx.mode });
        const ok = insertText(box, r.draft);
        last.asks.push(text);
        reply.innerHTML = ok ? "done ✓ edit it, or ask for more" : "it's on your clipboard: press ⌘V";
        cat.mood("happy");
      } catch (e) {
        reply.innerHTML = `🙀 ${esc(e.message)}`;
        cat.mood("error");
      } finally {
        busy = false;
        setTimeout(() => cat.mood(null), 1200);
        input.focus();
      }
    }

    p.querySelectorAll("[data-ask]").forEach((b) => b.addEventListener("click", () => ask(b.dataset.ask)));
    p.querySelector("[data-why]")?.addEventListener("click", showWhy);
    p.querySelector("[data-write]")?.addEventListener("click", () => { cat.closePanel(); draft(box); });
    p.querySelector("[data-lesson]")?.addEventListener("click", (ev) => {
      ev.target.closest(".lesson").innerHTML = lastLesson.lines.map((l) => `🐾 ${esc(l)}`).join("<br>")
        + (lastLesson.saved ? '<div class="muted">saved to your key</div>' : "");
    });
    p.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const t = input.value.trim();
      if (t) { input.value = ""; ask(t); }
    });
    input.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { cat.closePanel(); box.focus(); } });
    setTimeout(() => input.focus(), 0);
  }

  // ---- Learning on send --------------------------------------------------------------------
  // You pressed send: every send leaves a memory. Who you wrote to (and their circle, if they're new),
  // plus either the lessons from your edits and chat requests, or "the draft was right as it was".
  // Quiet: a 🐾 on the cat; the details wait in its chat. (Replies only for now.)
  async function captureSend(box) {
    if (!last || last.box !== box || last.r.mode !== "voice") return;
    if (!(last.ctx.mode === "reply" || (last.ctx.mode === "note" && last.ctx.person_id))) return;
    const sent = readBox(box).trim();   // read now, before the page clears the box
    const { r, ctx, asks } = last;
    last = null;
    cat.closePanel();
    if (!sent) return;
    learning = true;
    try {
      cat.mood("thinking");
      const out = await bridge("/learn", { draft_id: r.draft_id, draft_text: r.draft, sent_text: sent,
        person_id: ctx.person_id, person_name: ctx.person_name, platform: ctx.platform,
        circle: ctx.circle_hint || r.person?.circle || null, note_text: ctx.note || null, asks,
        relationship: ctx.relationship || null });
      const lines = out.learned.map((x) => `learned: ${x.text}`);
      if (out.note) lines.push(out.note);
      const n = out.learned.length;
      lastLesson = { lines, saved: !!out.saved,
        summary: n ? `your last send taught me ${n} new ${n === 1 ? "thing" : "things"}` : "your last send is saved to your key" };
      cat.mood("happy");
      cat.badge("🐾");
    } catch (e) {
      cat.mood("error");
      cat.say(`🙀 couldn't learn: ${esc(e.message)}`, { ms: 4000 });
    } finally {
      learning = false;
      setTimeout(() => cat.mood(null), 1200);
      linger(2500);
    }
  }
})();
