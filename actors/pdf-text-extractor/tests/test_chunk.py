from src.chunk import chunk_pages


def pages(*texts, headings=None):
    return [
        {"page": i + 1, "text": t, "headings": (headings or {}).get(i + 1, [])}
        for i, t in enumerate(texts)
    ]


def test_single_small_chunk():
    out = chunk_pages(pages("hello world"), 1500, 200)
    assert len(out) == 1
    c = out[0]
    assert c["chunkIndex"] == 0 and c["chunkCount"] == 1
    assert (c["pageStart"], c["pageEnd"], c["charStart"], c["charEnd"]) == (1, 1, 0, 11)
    assert c["text"] == "hello world" and c["tokenEstimate"] == 2 and c["headingPath"] == []


def test_paragraph_cut_preferred():
    text = ("A" * 300 + ". ") + "\n\n" + ("B" * 300 + ". ") + "\n\n" + "C" * 100
    out = chunk_pages(pages(text), 400, 0)
    assert out[0]["text"].rstrip() == "A" * 300 + "."
    assert out[1]["text"].startswith("B")


def test_sentence_cut_then_hard_cut():
    text = "A" * 120 + ". Next sentence starts here and continues " + "x" * 400
    out = chunk_pages(pages(text), 200, 0)
    assert out[0]["text"] == "A" * 120 + "."  # sentence end inside the second half of the window
    assert (
        out[1]["text"].startswith("Next sentence") and len(out[1]["text"]) == 200
    )  # no cut in the window
    assert out[2]["text"] == "x" * 200  # hard cut inside the run of x
    assert out[3]["text"] == "x" * 40 and out[3]["chunkCount"] == 4


def test_overlap_clamped_and_applied():
    text = "y" * 1000
    out = chunk_pages(pages(text), 200, 5000)  # overlap clamped to 100
    assert out[0]["charEnd"] == 200 and out[1]["charStart"] == 100
    assert out[1]["text"] == text[100:300]
    assert all(c["charEnd"] - c["charStart"] == len(c["text"]) for c in out)


def test_page_offsets_and_heading_path():
    p1 = "# Intro\n\nIntro text here. More intro."
    p2 = "Details on page two. Even more details here."
    headings = {1: [{"level": 1, "text": "# Intro"}]}
    out = chunk_pages(pages(p1, p2, headings=headings), 40, 0)
    assert out[0]["pageStart"] == 1
    assert out[-1]["pageEnd"] == 2
    assert all(c["headingPath"] == ["# Intro"] for c in out)
    spans = [c for c in out if c["pageStart"] != c["pageEnd"]]
    joined = p1 + "\n\n" + p2
    for c in out:
        assert joined[c["charStart"] : c["charEnd"]] == c["text"]
    assert spans == [] or all(c["pageStart"] == 1 and c["pageEnd"] == 2 for c in spans)


def test_heading_stack_pops_by_level():
    p1 = "Alpha\nBeta\nGamma\nbody body body"
    headings = {
        1: [
            {"level": 1, "text": "Alpha"},
            {"level": 2, "text": "Beta"},
            {"level": 2, "text": "Gamma"},
        ]
    }
    out = chunk_pages(pages(p1, headings=headings), 1500, 0)
    assert out[0]["headingPath"] == ["Alpha"]  # the chunk starts at Alpha itself
    late = chunk_pages(pages("x" * 10 + "\n\n" + p1, headings=headings), 5, 0)
    assert late[-1]["headingPath"] == ["Alpha", "Gamma"]


def test_empty_pages_produce_no_chunks():
    assert chunk_pages(pages("", "   "), 1500, 200) == []
