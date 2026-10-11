"""Small compatibility repair around the unchanged upstream tool modules."""


def install():
    from bs4 import UnicodeDammit
    from readability import Document
    from tools.web_extractor import web_extractor
    from tools.web_search import web_search
    from ddgs import DDGS

    class DecodedDocument(Document):
        def __init__(self, html_content, **options):
            # The upstream tool passes requests.Response.content (bytes), while
            # readability's charset regex expects decoded text. Preserve its
            # original extraction logic and correctly decode HTML encodings.
            if isinstance(html_content, bytes):
                html_content = UnicodeDammit(html_content, is_html=True).unicode_markup or html_content.decode("utf-8", errors="replace")
            super().__init__(html_content, **options)

    web_extractor.Document = DecodedDocument

    class CurrentSearch:
        """Keep the old tool's context-manager API using maintained DDGS."""
        def __init__(self, proxy=None):
            self.client = DDGS(proxy=proxy, timeout=15, verify=True)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def text(self, query, **options):
            options.setdefault("backend", "duckduckgo")
            return self.client.text(query, **options)

    # duckduckgo_search was renamed to ddgs. Its 2024 client fingerprint set
    # no longer matches current primp/server TLS. Query planning, formatting
    # and all original UI controls remain upstream code.
    web_search.DDGS = CurrentSearch
