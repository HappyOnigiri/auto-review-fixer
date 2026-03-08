"""Verify import path and that src modules can be imported."""


def test_import_auto_fixer():
    import auto_fixer  # noqa: F401
    assert hasattr(auto_fixer, "generate_prompt")


def test_import_review_db():
    import review_db  # noqa: F401
    assert hasattr(review_db, "init_db")


def test_import_summarizer():
    import summarizer  # noqa: F401
    assert hasattr(summarizer, "summarize_reviews")
