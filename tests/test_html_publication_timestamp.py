import unittest

from layer3_tool_bus_external_capabilities.tools.extractors import (
    extract_html_publication_timestamp,
)


class HtmlPublicationTimestampTests(unittest.TestCase):
    # OpenAI-style article metadata outranks lower-priority visible dates.
    def test_article_publication_meta_is_selected(self) -> None:
        result = extract_html_publication_timestamp(
            """
            <html><head>
              <meta property="article:published_time" content="2026-08-06T14:30:00Z">
            </head><body><p>Published August 7, 2026</p></body></html>
            """
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.value.isoformat(), "2026-08-06T14:30:00+00:00")
        self.assertEqual(result.basis, "html_meta:article:published_time")

    # JSON-LD datePublished supports date-only values without scanning arbitrary page dates.
    def test_json_ld_date_published_is_selected(self) -> None:
        result = extract_html_publication_timestamp(
            """
            <script type="application/ld+json">
              {"@type":"Article","headline":"Release","datePublished":"2026-08-19"}
            </script>
            <p>The rollout starts on 2026-09-01.</p>
            """
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.value.isoformat(), "2026-08-19T00:00:00+00:00")
        self.assertEqual(result.basis, "html_json_ld:datePublished")

    # Semantic date classes cover Blogger-like markup.
    def test_blog_post_date_class_is_selected(self) -> None:
        result = extract_html_publication_timestamp(
            "<p class='blog-post-full__date'>August 19, 2026</p>"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.value.isoformat(), "2026-08-19T00:00:00+00:00")
        self.assertEqual(result.basis, "html_element:semantic_date_class")

    # The live OpenAI-style fallback is scoped to one exact metadata element.
    def test_exact_published_metadata_element_is_selected(self) -> None:
        result = extract_html_publication_timestamp(
            "<p class='text-meta text-sm'>Published August 6, 2026</p>"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.value.isoformat(), "2026-08-06T00:00:00+00:00")
        self.assertEqual(result.basis, "html_element:published_label")

    # A body sentence can cite another publication and is not source metadata.
    def test_published_sentence_in_metadata_element_is_rejected(self) -> None:
        result = extract_html_publication_timestamp(
            "<p class='text-meta'>A report was published August 6, 2026.</p>"
        )

        self.assertIsNone(result)

    # A semantic HTML time element is an explicit machine-readable publication marker.
    def test_time_datetime_is_selected(self) -> None:
        result = extract_html_publication_timestamp(
            "<time itemprop='datePublished' datetime='2026-08-14T08:00:00+02:00'>"
            "14 August 2026</time>"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.value.isoformat(), "2026-08-14T06:00:00+00:00")
        self.assertEqual(result.basis, "html_time:itemprop=datePublished")

    # A generic machine-readable time can describe an event and is not publication proof.
    def test_unmarked_time_datetime_is_rejected(self) -> None:
        result = extract_html_publication_timestamp(
            "<time datetime='2026-08-14T08:00:00+02:00'>Event starts soon</time>"
        )

        self.assertIsNone(result)

    # Update and revision labels cannot satisfy a publication freshness burden.
    def test_updated_date_class_is_rejected(self) -> None:
        result = extract_html_publication_timestamp(
            "<p class='article-updated-date'>August 19, 2026</p>"
        )

        self.assertIsNone(result)

    # Arbitrary dates and malformed publication metadata never become freshness facts.
    def test_invalid_metadata_does_not_fall_back_to_arbitrary_visible_date(self) -> None:
        result = extract_html_publication_timestamp(
            """
            <meta property="article:published_time" content="not-a-date">
            <p>The product rollout begins on August 20, 2026.</p>
            """
        )

        self.assertIsNone(result)

    # Conflicting equally authoritative publication metadata fails closed.
    def test_conflicting_publication_metadata_is_rejected(self) -> None:
        result = extract_html_publication_timestamp(
            """
            <meta property="article:published_time" content="2026-08-06">
            <meta name="datePublished" content="2026-08-07">
            """
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
