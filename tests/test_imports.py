"""Verify import path and that src modules can be imported."""


def test_import_auto_fixer():
    import auto_fixer  # noqa: F401

    assert hasattr(auto_fixer, "generate_prompt")


def test_import_state_manager():
    import state_manager  # noqa: F401

    assert hasattr(state_manager, "load_state_comment")


def test_import_summarizer():
    import summarizer  # noqa: F401

    assert hasattr(summarizer, "summarize_reviews")
