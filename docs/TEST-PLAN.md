# Purrsona manual test plan

Run by hand in Chrome with the extension loaded. Mark each case ✅ (works), ❌ (broken: add what happened) or ⏭ (skipped), with the date.

**Use made-up content only:** your own posts and test chats, a Slack workspace or Teams chat with just yourself, emails to yourself. Never test on someone's private messages.

## 0. Before you start

- [ ] Bridge running (`cd api && uv run uvicorn purrsona.app:app --port 8765 --reload`)
- [ ] Key plugged in (practice page header says "Key plugged in")
- [ ] Extension reloaded (`chrome://extensions` → ↻ on Purrsona) and every test tab refreshed afterwards
- [ ] Goldfish quit (it also listens for the Option key)

## 1. The cat itself (try on the practice page, then on any site)

| # | Steps | Expected | Result |
|---|---|---|---|
| 1.1 | Click into a reply box | The cat sits on the box's top-right edge | |
| 1.2 | Click somewhere else on the page | The cat leaves within a moment | |
| 1.3 | Click into the site's search bar | No cat | |
| 1.4 | Focus a box, scroll it out of view | The cat hides; it comes back when the box does | |
| 1.5 | Empty reply box, press Option+M | No stray character (˛ or µ) in the box; the cat paces, then the draft lands | |
| 1.6 | Tap Option twice in a box | The chat opens above the cat; Esc closes it | |
| 1.7 | Click the cat | The chat opens; clicking outside closes it | |
| 1.8 | Open the chat when the box is near the top of the screen | Nothing is cut off; the top line is visible | |

## 2. Practice page: reply mode (`localhost:8765/practice/`)

| # | Steps | Expected | Result |
|---|---|---|---|
| 2.1 | Priya's post: empty box, Option+M | A Work-style draft, no question asked | |
| 2.2 | Kabir's post (new): Option+M | "Kabir is new to me. Who is Kabir to you?" with circle buttons, a text box and "none of these" | |
| 2.3 | Same, type "my new manager" in the text box, Enter | Moves on while working ("your new manager → Work"); a Work-style draft | |
| 2.4 | Type a rough note in a box ("kabir the legend, new role"), then Option+M | Your words kept and polished, nickname kept | |
| 2.5 | After a draft, click the cat → **shorter** | The draft gets shorter in the box; the chat says "done ✓" | |
| 2.6 | Chat → **why?** | The top rules with owner and edit counts, then "How the agents made it: Drafter wrote → Critic …" | |
| 2.7 | Chat → type your own request ("mention the go-live"), Enter | The draft changes as asked | |
| 2.8 | Send a draft unchanged (Reply) | 🐾 badge; opening the chat shows "your last send is saved to your key" | |
| 2.9 | Edit a draft clearly (add a nickname), then Reply | 🐾 badge; chat → "your last send taught me 1 new thing" → show | |
| 2.10 | Draft for the same person again | The new habit is used; while it works the cat mentions "you've written to X n× with me" | |
| 2.11 | Unplug (header button), then Option+M | "No key plugged in, so this is a generic draft" | |
| 2.12 | Plug back in, draft for the person from 2.9 | The learned habit is still used (memory replayed from the key) | |

## 3. Practice page: your-words mode (the made-up `# team-launch` chat at the bottom)

| # | Steps | Expected | Result |
|---|---|---|---|
| 3.1 | Empty chat box, Option+M | "Type a rough message first… here I only use your own words, never the conversation" | |
| 3.2 | Type a rough note, Option+M | "Who's this message for?" with your people, a name box and "no one in particular" | |
| 3.3 | Pick Neha | Your note rewritten in Neha's voice, nothing added that you didn't write | |
| 3.4 | Option+M again in the same chat | No question this time (remembered for this chat) | |
| 3.5 | Press Enter to send | The message appears in the chat; 🐾 badge | |
| 3.6 | Shift+Enter while typing | New line, not sent | |
| 3.7 | Type a new name ("Zoya Khan") in the picker | The circle question appears; then the draft | |
| 3.8 | Choose "no one in particular" | Your note polished with only your own and platform rules; nothing learned on send | |
| 3.9 | **Privacy check:** Neha's message mentions "the demo tomorrow at 3"; your note doesn't mention it | The draft does **not** mention the demo (the cat never read her message) | |

## 4. LinkedIn (reply mode on posts, your-words mode in messages)

| # | Steps | Expected | Result |
|---|---|---|---|
| 4.1 | Comment box on a person's post, Option+M | Draft addressed to the post's author (not someone who only liked it) | |
| 4.2 | Comment box on a company's post | No circle question; a draft | |
| 4.3 | **Reply** under someone's comment, Option+M | The @mention stays at the start; the draft answers that commenter | |
| 4.4 | Start a post: type a topic, Option+M | Your own post in your voice | |
| 4.5 | Start a post: empty, Option+M | "Writing your own post? Type a few words about it…" | |
| 4.6 | Send a comment (Comment button) | 🐾 badge | |
| 4.7 | Chat → why? after a draft | "checked by the Critic" (or "the Critic fixed …") and the agents' trail | |
| 4.8 | Messaging: type a rough note to yourself or a test contact, Option+M | Your-words mode: "Who's this message for?" | |
| 4.9 | Messaging: Send / Enter | 🐾 badge | |

## 5. X

| # | Steps | Expected | Result |
|---|---|---|---|
| 5.1 | Open a tweet, click the reply box, Option+M | Draft replying to that tweet's author (@handle) | |
| 5.2 | Reply from the timeline's reply icon (pop-up) | Same, inside the pop-up | |
| 5.3 | Home composer: type a topic, Option+M | Your own post; under 240 characters, no hashtags (X rules) | |
| 5.4 | Send a reply (Reply button) | 🐾 badge | |
| 5.5 | DM to yourself or a test account: rough note, Option+M | Your-words mode | |

## 6. Reddit (not tested by Claude: Reddit is blocked for its browser tools)

| # | Steps | Expected | Result |
|---|---|---|---|
| 6.1 | New Reddit: comment box under a post, Option+M | Draft replying to the post (u/author) | |
| 6.2 | New Reddit: Reply under a comment, Option+M | Draft answering that commenter | |
| 6.3 | Send with the Comment button | 🐾 badge | |
| 6.4 | old.reddit.com: comment box, Option+M | Draft replying to the post | |
| 6.5 | If the cat doesn't appear at all | Note which box (screenshot), so the adapter can be fixed | |

## 7. Slack web (`app.slack.com`, a test workspace or a DM to yourself)

| # | Steps | Expected | Result |
|---|---|---|---|
| 7.1 | Message box: rough note, Option+M | "Who's this message for?" → your message rewritten | |
| 7.2 | Enter sends | 🐾 badge; the message is posted by Slack as normal | |
| 7.3 | Shift+Enter | New line, nothing sent, nothing learned | |
| 7.4 | Thread reply box | Same as 7.1 | |
| 7.5 | **Privacy check:** a fact that's only in the conversation never shows up in the draft | | |

## 8. Teams web (`teams.microsoft.com` or `teams.live.com`, a chat with yourself)

| # | Steps | Expected | Result |
|---|---|---|---|
| 8.1 | Chat box: rough note, Option+M | "Who's this message for?" → rewritten | |
| 8.2 | Enter sends | 🐾 badge | |
| 8.3 | **Privacy check:** as 7.5 | | |

## 9. Gmail (`mail.google.com`, an email to yourself)

| # | Steps | Expected | Result |
|---|---|---|---|
| 9.1 | Compose: rough note in the body, Option+M | "Who's this message for?" → rewritten in an email style | |
| 9.2 | Click Send | 🐾 badge | |
| 9.3 | Cmd+Enter to send | 🐾 badge | |
| 9.4 | Enter in the body | A new line, nothing learned (email doesn't send on Enter) | |
| 9.5 | Reply in a thread | Your-words mode; the draft doesn't use anything from the thread | |
| 9.6 | The To and Subject fields | No cat (only the body) | |

## 10. The Critic (a reliable redo for the video)

| # | Steps | Expected | Result |
|---|---|---|---|
| 10.1 | Practice page, Kabir: edit a draft down to **one short sentence**, send | Learned something like "Keeps replies to Kabir to one sentence" | |
| 10.2 | Draft for Kabir again, open the chat | "the Critic fixed …" if Claude wrote more than one sentence; why? shows "Critic redo → Drafter rewrote → Critic pass" | |
| 10.3 | Console (`localhost:8765/`): draft, then look under the why lanes | The "How the agents made it" row | |

## 11. Errors

| # | Steps | Expected | Result |
|---|---|---|---|
| 11.1 | Stop the bridge, Option+M | The cat shakes: "Purrsona's bridge isn't running" | |
| 11.2 | Restart the bridge without plugging in, Option+M | A generic draft with the "no key plugged in" warning | |
