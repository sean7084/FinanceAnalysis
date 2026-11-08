"""The lexicon scorer must fire on Chinese text with or without jieba.

The defect these pin: ``_tokenize``'s fallback character class was double-escaped
(``r'[\\\\w\\\\u4e00-\\\\u9fff]+'``), which matches a literal backslash, ``w``, ``u`` and
an ASCII ``0``-``\\`` range -- and no Chinese at all. jieba is not declared in
requirements, so that fallback was the only path in every environment including CI.
Every article tokenized to nothing, ``_score_text`` returned its empty-text
signature, and all 6,522,270 stored SentimentScore rows were exactly 0.000000.
``FactorScore.sentiment_score`` was then a constant 0.5 -- arithmetically correct
as ``(0 + 1) / 2`` and carrying no information -- while three LightGBM features
derived from it.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.sentiment import tasks


class TokenizeTests(SimpleTestCase):
    def test_the_fallback_character_class_matches_chinese(self):
        """The exact regression: a CJK run must survive tokenization."""
        with patch.object(tasks, 'jieba', None):
            tokens = tasks._tokenize('业绩增长利好')
        self.assertEqual(tokens, ['业绩增长利好'])

    def test_the_fallback_splits_on_punctuation_and_keeps_ascii(self):
        with patch.object(tasks, 'jieba', None):
            tokens = tasks._tokenize('公司业绩大幅增长，净利润超预期利好 buy')
        self.assertEqual(tokens, ['公司业绩大幅增长', '净利润超预期利好', 'buy'])

    def test_empty_text_yields_no_tokens(self):
        with patch.object(tasks, 'jieba', None):
            self.assertEqual(tasks._tokenize(''), [])
            self.assertEqual(tasks._tokenize(None), [])

    def test_a_segmented_path_is_used_when_jieba_is_present(self):
        class FakeJieba:
            @staticmethod
            def lcut(text):
                return ['公司', '业绩', '增长', '利好']

        with patch.object(tasks, 'jieba', FakeJieba):
            self.assertEqual(tasks._tokenize('公司业绩增长利好'),
                             ['公司', '业绩', '增长', '利好'])


class ScoreTextTests(SimpleTestCase):
    def test_positive_chinese_news_scores_positive_without_jieba(self):
        with patch.object(tasks, 'jieba', None):
            positive, neutral, negative, sentiment = tasks._score_text(
                '公司业绩大幅增长，净利润超预期利好')
        self.assertGreater(sentiment, Decimal('0'))
        self.assertEqual(positive, Decimal('1.000000'))
        self.assertEqual(negative, Decimal('0.000000'))
        self.assertGreater(positive, neutral)

    def test_negative_chinese_news_scores_negative_without_jieba(self):
        with patch.object(tasks, 'jieba', None):
            positive, neutral, negative, sentiment = tasks._score_text(
                '重大亏损利空暴跌风险警示')
        self.assertLess(sentiment, Decimal('0'))
        self.assertEqual(negative, Decimal('1.000000'))
        self.assertEqual(positive, Decimal('0.000000'))

    def test_the_empty_text_signature_is_not_what_real_news_produces(self):
        """74,139 stored ARTICLE rows carried this signature; real text must not."""
        empty = tasks._score_text('')
        self.assertEqual(empty, (Decimal('0.333333'), Decimal('0.333333'),
                                 Decimal('0.333333'), Decimal('0')))
        with patch.object(tasks, 'jieba', None):
            self.assertNotEqual(tasks._score_text('业绩增长利好'), empty)

    def test_ascii_terms_match_whole_tokens_not_substrings(self):
        with patch.object(tasks, 'jieba', None):
            _, _, _, buyer = tasks._score_text('buyer')
            _, _, _, buy = tasks._score_text('buy')
        self.assertEqual(buyer, Decimal('0'))
        self.assertGreater(buy, Decimal('0'))

    def test_text_with_no_lexicon_evidence_stays_neutral(self):
        with patch.object(tasks, 'jieba', None):
            _, neutral, _, sentiment = tasks._score_text('国际原子能机构发布年度通报')
        self.assertEqual(sentiment, Decimal('0'))
        self.assertEqual(neutral, Decimal('1.000000'))

    def test_segmented_and_unsegmented_paths_agree_on_the_sign(self):
        class FakeJieba:
            @staticmethod
            def lcut(text):
                return ['公司', '业绩', '大幅', '增长', '净利润', '超预期', '利好']

        text = '公司业绩大幅增长净利润超预期利好'
        with patch.object(tasks, 'jieba', None):
            unsegmented = tasks._score_text(text)[3]
        with patch.object(tasks, 'jieba', FakeJieba):
            segmented = tasks._score_text(text)[3]
        self.assertGreater(unsegmented, Decimal('0'))
        self.assertGreater(segmented, Decimal('0'))
        # Segmentation gives a larger denominator, so the same evidence saturates less.
        self.assertLessEqual(segmented, unsegmented)


class LexiconHitsTests(SimpleTestCase):
    def test_a_cjk_term_is_counted_per_occurrence(self):
        with patch.object(tasks, 'jieba', None):
            tokens = tasks._tokenize('增长增长利好')
        self.assertEqual(tasks._lexicon_hits('增长增长利好', tokens, {'增长'}), 2)
        self.assertEqual(tasks._lexicon_hits('增长增长利好', tokens, {'利好'}), 1)

    def test_an_ascii_term_is_counted_per_token(self):
        tokens = ['buy', 'buy', 'buyer']
        self.assertEqual(tasks._lexicon_hits('buy buy buyer', tokens, {'buy'}), 2)

    def test_case_is_folded_for_both_scripts(self):
        tokens = tasks._tokenize('BUY 利好')
        self.assertEqual(tasks._lexicon_hits('BUY 利好', tokens, {'buy'}), 1)
        self.assertEqual(tasks._lexicon_hits('BUY 利好', tokens, {'利好'}), 1)