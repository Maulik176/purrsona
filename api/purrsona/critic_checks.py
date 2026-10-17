"""The Critic's three basic checks. Written by Maulik.

Plain Python, no Claude call, so the Critic runs in milliseconds. critic.py reads each rule's
wording and calls these to measure the draft. Run the tests with:
    cd api && uv run pytest tests/test_critic_checks.py -q
"""
import re


def count_emojis(text: str) -> int:
    """How many emojis are in the text?

    'Congrats 🎉🔥' -> 2 · 'Congrats!' -> 0 · 'Yo bro 😂 that over 🏏' -> 2

    Hint: most emojis live in these Unicode ranges: U+1F300 to U+1FAFF and U+2600 to U+27BF.
    A regex character class like r"[\\U0001F300-\\U0001FAFF\\u2600-\\u27BF]" matches one of them.
    """
    raise NotImplementedError("Maulik: write count_emojis")


def count_sentences(text: str) -> int:
    """How many sentences does the text have?

    'Hi Priya. Great work! Proud of you' -> 3 · 'Congrats on the launch' -> 1 · '' -> 0

    Hint: split on . ! ? and line breaks, then count the pieces that aren't empty.
    """
    raise NotImplementedError("Maulik: write count_sentences")


def banned_phrase_used(draft: str, rule_text: str) -> str | None:
    """The rule quotes a phrase to avoid. Return that phrase if the draft uses it, else None.

    rule "Never says 'I hope this finds you well'"
      draft "Hi Neha, I hope this finds you well."  -> "I hope this finds you well"
      draft "Hi Neha, congrats!"                    -> None
    Ignore upper/lower case. Careful: a phrase can contain an apostrophe, as in
    "Drops closing well-wishes like 'Enjoy the moment, it's well earned'".
    Hint: take the text between the FIRST and the LAST ' in the rule.
    """
    raise NotImplementedError("Maulik: write banned_phrase_used")
