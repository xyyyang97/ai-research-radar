"""Unit tests: fingerprint / URL canonicalization / simhash."""

from __future__ import annotations

import pytest

from ai_research_radar.fingerprint import (
    canonicalize_url,
    content_fingerprint,
    hamming_distance,
    normalized_words,
    simhash64,
    similarity_within,
    title_key,
    word_jaccard,
)


class TestCanonicalizeUrl:
    def test_strips_tracking_params(self):
        url = "https://Example.com/Path/?utm_source=rss&id=7"
        assert canonicalize_url(url) == "https://example.com/Path?id=7"

    def test_case_host_kept_path(self):
        assert canonicalize_url("https://EXAMPLE.com/A") == "https://example.com/A"

    def test_drops_fragment_and_trailing_slash(self):
        assert canonicalize_url("https://a.com/x/#frag") == "https://a.com/x"

    def test_default_port_removed(self):
        assert canonicalize_url("https://a.com:443/x") == "https://a.com/x"
        assert "8080" in canonicalize_url("https://a.com:8080/x")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            canonicalize_url("not a url")
        with pytest.raises(ValueError):
            canonicalize_url("mailto:someone@example.com")


class TestTitleKeyAndWords:
    def test_stopwords_and_case(self):
        assert title_key("The Model Launches Today") == "model launches today"

    def test_normalized_words_alnum_only(self):
        assert normalized_words("GPT-5.6, now live!") == ["gpt", "5", "6", "now", "live"]


class TestSimhash:
    def test_identical_text_zero_distance(self):
        a = "Anthropic launches Claude Enterprise with SSO and audit logs today"
        assert hamming_distance(simhash64(a), simhash64(a)) == 0

    def test_paraphrase_close(self):
        # lightly-edited copy stays within a modest distance
        a = ("Anthropic today announced the launch of its new enterprise plan "
             "with single sign-on support and audit logging for teams")
        b = ("Anthropic today announced the launch of its new enterprise plan "
             "featuring single sign-on support plus audit logging for teams")
        assert similarity_within(a, b, max_distance=12)

    def test_deep_rewrites_exceed_fuzzy_threshold(self):
        # genuinely different wording must NOT be flagged near-duplicate
        a = ("Anthropic announced the launch of its new enterprise plan with "
             "single sign-on support and audit logging for large customers")
        b = ("The company said teams at big organizations can now buy SSO and "
             "log retention in a package for enterprises it introduced")
        assert not similarity_within(a, b, max_distance=12)

    def test_unrelated_far(self):
        a = ("The chef prepared a delicate risotto with porcini mushrooms and "
             "aged parmesan cheese, stirring slowly for twenty minutes")
        b = ("Kubernetes clusters scale horizontally by adding nodes to the "
             "worker pool while the control plane schedules pods automatically")
        assert hamming_distance(simhash64(a), simhash64(b)) > 20

    def test_empty_text_is_zero(self):
        assert simhash64("") == 0


class TestFingerprints:
    def test_content_fingerprint_stable_ignoring_tracking(self):
        f1 = content_fingerprint("Same Title", "https://a.com/x?utm_source=rss")
        f2 = content_fingerprint("Same Title", "https://a.com/x")
        assert f1 == f2

    def test_content_fingerprint_differs_for_different_titles(self):
        f1 = content_fingerprint("Title One", "https://a.com/x")
        f2 = content_fingerprint("Title Two", "https://a.com/x")
        assert f1 != f2

    def test_word_jaccard_bounds(self):
        assert word_jaccard("alpha beta gamma", "alpha beta gamma") == 1.0
        assert word_jaccard("", "") == 0.0
