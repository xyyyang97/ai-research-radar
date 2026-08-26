"""Rule classifier tests."""

from __future__ import annotations

from ai_research_radar.classify import RuleClassifier


class TestRuleClassifier:
    def test_matches_topic_name_and_keywords(self, config):
        c = RuleClassifier(config)
        assert "openai" in c.classify("OpenAI ships new feature", "")
        assert "openai" in c.classify("GPT-5 benchmark results", "")  # keyword
        assert c.classify("Nothing relevant here at all", "") == []

    def test_word_boundary_no_substring_hits(self, config):
        c = RuleClassifier(config)
        # 'openai' inside 'reopenedai' must NOT match
        assert c.classify("The reopenedai project restarted", "") == []
        # multiword keyword needs the phrase
        assert "agents" not in c.classify("a hostile agent ran away", "")

    def test_multiword_keyword_matches(self, config):
        c = RuleClassifier(config)
        assert "agents" in c.classify("New agent runtime released today", "")

    def test_source_name_hints(self, config):
        c = RuleClassifier(config)
        assert "openai" in c.classify("Weekly digest", "", source_name="OpenAI News")

    def test_multiple_topics_sorted(self, config):
        c = RuleClassifier(config)
        got = c.classify("OpenAI acquires an agent startup", "",
                         source_name="")
        assert got == sorted(got)
        assert set(got) == {"openai", "acquisitions"}
