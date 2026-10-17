"""The Critic agent: checks a draft against the rules it was written with, in plain Python (no Claude call).

It reads each rule's own wording with simple patterns ("No emojis", "one to three sentences",
"Never says '…'") and measures the draft with Maulik's checks in critic_checks.py. Rules it can't
measure (tone, nicknames, line layout) are left alone. Each problem names the rule and its owner,
so the Drafter's one redo knows exactly what to fix: "broke Work's 'No emojis'".
"""
import re

from . import critic_checks as measure

NUMBERS = {"no": 0, "zero": 0, "one": 1, "single": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5}
# A last line that is only a sign-off ("Regards, Maulik", "Cheers!", "- Maulik"), not "Thanks for sharing this!".
SIGNOFF_LINE = re.compile(r"^\W*(?:(?:best|warm|kind)\s+)?(regards|best wishes|best|cheers|thanks|thank you|sincerely)?"
                          r"[\s,.!-]*(maulik)?[\s.!]*$", re.I)


def owner(rule: dict) -> str:
    """Whose rule is it, in words: "Work's", "Priya's", "LinkedIn's", "your"."""
    scope, name = rule["scope"], rule.get("scope_name") or ""
    if scope == "User":
        return "your"
    if scope == "Person":
        name = name.split(" ")[0]
    if scope == "Platform":
        name = {"linkedin": "LinkedIn", "x": "X"}.get(name, name)
    return f"{name}'s"


GREETING_WORDS = re.compile(r"^\W*(hi|hello|hey|yo|dear|bhaila)\b", re.I)


def is_signoff_line(line: str) -> bool:
    m = SIGNOFF_LINE.match(line)
    return bool(m and (m.group(1) or m.group(2)))


def message_body(draft: str) -> str:
    """The draft without a greeting line on top ("Hi Rohan,") or sign-off lines at the bottom
    ("Regards," / "Maulik"): those aren't sentences, and Clients rules even require them.
    Found live on Oct 17: Rohan's 2-sentence reply was counted as 5, so no redo could pass."""
    lines = [l for l in draft.strip().splitlines()]
    if len(lines) > 1 and GREETING_WORDS.match(lines[0]) and len(lines[0].split()) <= 4:
        lines = lines[1:]
    while lines and (not lines[-1].strip() or is_signoff_line(lines[-1])):
        lines = lines[:-1]
    return "\n".join(lines)


def quoted(text: str) -> list[str]:
    """Phrases in quotes: Opens with 'Hi Nair' instead of 'Hi Priya' -> ['Hi Nair', 'Hi Priya']."""
    return re.findall(r"'([^']+)'", text)


def words(text: str) -> list[str]:
    return re.findall(r"[\w<>]+", text.lower())


def emoji_limit(text: str) -> int | None:
    """'No emojis' -> 0 · 'One or two emojis' -> 2 · 'a single 🎉 …, otherwise no emoji' -> 1 (the most allowed)."""
    t = text.lower()
    if "emoji" not in t and "🎉" not in t:
        return None
    found = [NUMBERS[w] for w in re.findall(r"\b(no|zero|one|single|two|three|four|five)\b", t)]
    return max(found) if found else None


def sentence_limit(text: str) -> int | None:
    """'one to three sentences' -> 3 · 'to a single line' -> 1 · 'in one short line' -> 1."""
    m = re.search(r"\b(one|two|three|four|five|single|\d+)\s+(?:short\s+)?(?:sentences?|lines?)\b", text.lower())
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]


def problem_with(draft: str, rule: dict, first_name: str) -> str | None:
    """What's wrong with the draft under this one rule, or None (also None when the rule can't be measured).
    Raises NotImplementedError while one of Maulik's checks isn't written yet."""
    text, kind = rule["text"], rule["kind"]
    low = text.lower()
    # A rule of any kind that names a length ("keeps it to a single line") is measured as a length.
    limit = sentence_limit(text)
    if limit is not None and kind != "emoji" and (n := measure.count_sentences(message_body(draft))) > limit:
        return f"it has {n} sentences, the rule allows {limit}"
    if kind == "emoji":
        limit = emoji_limit(text)
        n = measure.count_emojis(draft)
        if limit is not None and n > limit:
            return f"it has {n} {'emoji' if n == 1 else 'emojis'}, the rule allows {limit}"
    elif kind == "length":
        chars = re.search(r"(\d+)\s*char", low)
        if chars and len(draft) > int(chars.group(1)):
            return f"it's {len(draft)} characters, the limit is {chars.group(1)}"
    elif kind == "phrase_avoid":
        phrase = measure.banned_phrase_used(draft, text)
        if phrase:
            return f"it says '{phrase}'"
    elif kind == "greeting":
        # Only a definite opening ("Opens with 'Hi Nair'"), not "'Yo' or jumps straight in".
        q = quoted(text)
        if q and low.startswith("opens with") and " or " not in low:
            want = q[0].replace("<first name>", first_name)
            if words(draft)[: len(words(want))] != words(want):
                return f"it doesn't open with '{want}'"
    elif kind == "signoff":
        last = draft.strip().splitlines()[-1] if draft.strip() else ""
        if re.search(r"\bno sign-?off\b|drops the sign-?off|without a sign-?off", low):
            if is_signoff_line(last):
                return f"it signs off with '{last.strip()}'"
        elif quoted(text):
            want = quoted(text)[0]
            if not words(draft)[-len(words(want)):] == words(want):
                return f"it doesn't sign off with '{want}'"
    elif kind == "format" and "hashtag" in low and low.startswith("no"):
        tags = re.findall(r"#\w+", draft)
        if tags:
            return f"it has hashtags: {' '.join(tags)}"
    return None


def review(draft: str, rules: list[dict], person_name: str = "", note: str | None = None) -> dict:
    """Check the draft against every rule it was written with.

    Your own words win: if your note breaks a rule too (your own emoji, your own long note), the
    Critic doesn't flag it. Returns {checked, problems: [{id, kind, rule, owner, problem}]}.
    """
    first = (person_name or "").split(" ")[0]
    problems, checked = [], 0
    for rule in rules:
        if rule["kind"] in ("tone", "phrase_use") and sentence_limit(rule["text"]) is None:
            continue  # not measurable in code (version 1), unless it names a length
        try:
            issue = problem_with(draft, rule, first)
            mine = bool(note) and bool(problem_with(note, rule, first))
        except NotImplementedError:  # one of Maulik's checks isn't written yet: skip this rule
            continue
        checked += 1
        if issue and not mine:
            problems.append({"id": rule["id"], "kind": rule["kind"], "rule": rule["text"],
                             "owner": owner(rule), "problem": issue})
    return {"checked": checked, "problems": problems}


def describe(problems: list[dict]) -> str:
    """For the trail and the redo: "broke Work's 'No emojis' (it has 2 emojis, the rule allows 0)"."""
    return "; ".join(f"broke {p['owner']} '{p['rule']}' ({p['problem']})" for p in problems)


def fix_request(problems: list[dict]) -> str:
    """The Critic's note back to the Drafter for its one redo."""
    return ("Fix only these problems and change nothing else: " + describe(problems)
            + ". Keep the same meaning, voice and length.")
