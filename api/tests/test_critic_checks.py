"""Tests for Maulik's three Critic checks (api/purrsona/critic_checks.py)."""
from purrsona.critic_checks import banned_phrase_used, count_emojis, count_sentences


def test_count_emojis():
    assert count_emojis("Congrats 🎉🔥") == 2
    assert count_emojis("Congrats!") == 0
    assert count_emojis("Yo bro 😂 that over 🏏") == 2
    assert count_emojis("") == 0


def test_count_sentences():
    assert count_sentences("Hi Priya. Great work! Proud of you") == 3
    assert count_sentences("Congrats on the launch") == 1
    assert count_sentences("Yo!\nWhat a finish\n\nProud of you bro") == 3
    assert count_sentences("") == 0


def test_banned_phrase_used():
    rule = "Never says 'I hope this finds you well'"
    assert banned_phrase_used("Hi Neha, I hope this finds you well.", rule) == "I hope this finds you well"
    assert banned_phrase_used("Hi Neha, i HOPE this finds you well", rule) == "I hope this finds you well"
    assert banned_phrase_used("Hi Neha, congrats!", rule) is None
    tricky = "Drops closing well-wishes like 'Enjoy the moment, it's well earned' when congratulating Neha"
    assert banned_phrase_used("Great work. Enjoy the moment, it's well earned!", tricky) == "Enjoy the moment, it's well earned"
    assert banned_phrase_used("No quotes in this rule", "Keeps it short") is None
