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


def write_draft(client: anthropic.Anthropic, post_text: str, author_name: str, platform: str,
                rules: list[dict] | None, note: str | None = None) -> str:
    """rules=None means the key is unplugged: no voice, a generic draft.
    note = what the user had already typed in the box: polish it instead of starting over."""
    if rules:
        system = VOICE_SYSTEM
        user = (f"Platform: {platform}\nReplying to: {author_name}\n\nTheir post:\n{post_text}\n\n"
                f"The user's style rules, most specific first:\n{_rules_block(rules)}")
    else:
        system = GENERIC_SYSTEM
        user = f"Platform: {platform}\nReplying to: {author_name}\n\nTheir post:\n{post_text}"
    if note:
        system += "\n" + NOTE_INSTRUCTIONS
        user += f"\n\nThe user's own note to turn into the reply:\n{note}"

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
