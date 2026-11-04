from campaignpilot.rag.store import chunk_markdown, search


def test_chunking_splits_by_section():
    md = "# Doc\n\n## A\nalpha text\n\n## B\nbeta text\n"
    chunks = chunk_markdown(md, "doc.md")
    assert [c["heading"] for c in chunks] == ["A", "B"]
    assert chunks[0]["text"].startswith("Doc > A")


def test_search_finds_banned_words_section():
    hits = search("which words are banned and never used in campaign copy", k=3)
    assert hits
    assert any(h["heading"] == "Words we never use" for h in hits)


def test_search_finds_pricing():
    hits = search("Pro plan price per user per month", k=3)
    assert any(h["source"] == "products.md" for h in hits)
