"""The Drafter agent: one Claude call that writes a reply in the user's voice from graph rules."""
import anthropic

MODEL = "claude-opus-5-5"

VOICE_SYSTEM = """You draft social-media replies and posts in the user's own writing voice.
You get the post being replied to and the user's style rules, which come from their voice graph.
Each rule says where it comes from (a person, a circle, a platform, a topic, or the user overall).
Follow every rule. Where rules seem to conflict, the one listed first wins.
Write only the reply text, ready to paste: no quotes around it, no explanations, no options."""

GENERIC_SYSTEM = """You draft a reply to a social-media post.
Write only the reply text, ready to paste: no quotes around it, no explanations, no options."""


def _rules_block(rules: list[dict]) -> str:
    lines = []
    for r in rules:
        source = f"{r['scope']}: {r['scope_name']}" if r["scope"] != "User" else "you, everywhere"
        lines.append(f"- [{r['kind']}] {r['text']} (from {source})")
    return "\n".join(lines)


NOTE_INSTRUCTIONS = """The user already started writing this reply themselves (their note is below).
Keep their meaning, their names and nicknames, and their key words exactly; turn the note into the finished reply."""

POST_INSTRUCTIONS = """This time the user is writing their own new post, not a reply.
Their note says what the post is about: keep its facts, names and key words, and write the finished post."""

# Your-words mode (Slack, Teams, Gmail, DMs): Purrsona never reads the conversation there, only what you typed.
NOTE_MODE_INSTRUCTIONS = """This time the user is writing a message in a private app, and you can't see the conversation:
only their own rough note. Turn the note into the finished message in their voice. Keep its meaning, facts,
names and key words; don't add facts, questions or plans they didn't write."""


def write_draft(client: anthropic.Anthropic, post_text: str, author_name: str, platform: str,
                rules: list[dict] | None, note: str | None = None, mode: str = "reply") -> str:
    """rules=None means the key is unplugged: no voice, a generic draft.
    note = what the user had already typed in the box: polish it instead of starting over.
    mode="post" = the user's own new post (the note says what it's about; there's nobody to reply to)."""
    if mode in ("post", "note"):
        system = (VOICE_SYSTEM if rules else GENERIC_SYSTEM) + "\n" + (POST_INSTRUCTIONS if mode == "post"
                                                                     else NOTE_MODE_INSTRUCTIONS)
        user = (f"Platform: {platform}\nThe user's note for their new post:\n{note}" if mode == "post" else
                f"Platform: {platform}\nTo: {author_name or 'no one in particular'}\nThe user's rough message:\n{note}")
        if rules:
            user += f"\n\nThe user's style rules, most specific first:\n{_rules_block(rules)}"
        note = None  # already in the prompt
    elif rules:
        system = VOICE_SYSTEM
        user = (f"Platform: {platform}\nReplying to: {author_name}\n\nTheir post:\n{post_text}\n\n"
                f"The user's style rules, most specific first:\n{_rules_block(rules)}")
    else:
        system = GENERIC_SYSTEM
        user = f"Platform: {platform}\nReplying to: {author_name}\n\nTheir post:\n{post_text}"
    if note:
        system += "\n" + NOTE_INSTRUCTIONS
        user += f"\n\nThe user's own note to turn into the reply:\n{note}"

    return _ask(client, system, user)


REFINE_SYSTEM = """You change a social-media draft the way the user asks, keeping it in their own writing voice.
You get the post they're answering (or nothing, for their own new post), their style rules from their
voice graph, the current draft, and their request. Do exactly what they ask; where the request clashes
with a style rule, the request wins for this message. Keep everything else the same.
Write only the new text, ready to paste: no quotes around it, no explanations, no options."""


def refine_draft(client: anthropic.Anthropic, current: str, instruction: str, post_text: str,
                 author_name: str, platform: str, rules: list[dict] | None, mode: str = "reply") -> str:
    """The cat's chat: "make it shorter", "add my emojis". rules=None means the key is unplugged."""
    user = f"Platform: {platform}\n"
    user += (f"Replying to: {author_name}\n\nTheir post:\n{post_text}\n\n" if mode == "reply"
             else f"This is a message the user is writing to {author_name or 'someone'} (you can't see the conversation).\n\n"
             if mode == "note" else "This is the user's own new post.\n\n")
    if rules:
        user += f"The user's style rules, most specific first:\n{_rules_block(rules)}\n\n"
    user += f"Current draft:\n{current}\n\nThe user's request: {instruction}"
    return _ask(client, REFINE_SYSTEM, user)


def _ask(client: anthropic.Anthropic, system: str, user: str) -> str:
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to draft this reply.")
    return "".join(b.text for b in response.content if b.type == "text").strip()
