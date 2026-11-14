import pytest

from app.text import (
    SentenceChunker,
    build_prompt,
    decode_header_text,
    encode_header_text,
    mock_response,
    parse_sse_delta,
)


def chunk_all(tokens):
    chunker = SentenceChunker()
    out = [s for token in tokens for s in chunker.feed(token)]
    tail = chunker.flush()
    return out + ([tail] if tail else [])


def test_chunker_splits_streamed_tokens_into_sentences():
    tokens = ["Hel", "lo there", ". How", " are you", "? I'm ", "fine"]
    assert chunk_all(tokens) == ["Hello there.", "How are you?", "I'm fine"]


def test_chunker_handles_multiple_sentences_in_one_token():
    assert chunk_all(["One. Two! Three?"]) == ["One.", "Two!", "Three?"]


def test_chunker_supports_hindi_danda_and_newlines():
    assert chunk_all(["नमस्ते। आप कैसे हैं\nठीक"]) == ["नमस्ते।", "आप कैसे हैं", "ठीक"]


def test_chunker_flush_does_not_duplicate_text():
    chunker = SentenceChunker()
    assert list(chunker.feed("Done. tail")) == ["Done."]
    assert chunker.flush() == "tail"
    assert chunker.flush() is None


def test_build_prompt_includes_context_query_and_language():
    prompt = build_prompt("Where is my order?", "Track it online.", "hi")
    assert "Where is my order?" in prompt
    assert "Track it online." in prompt
    assert "'hi'" in prompt


@pytest.mark.parametrize(
    ("language", "prefix"), [("en", "Based on our records: "), ("hi", "हमारे रिकॉर्ड")]
)
def test_mock_response_uses_top_context_line(language, prefix):
    answer = mock_response("First fact.\nSecond fact.", language)
    assert answer.startswith(prefix)
    assert "First fact." in answer
    assert "Second fact." not in answer


def test_mock_response_handles_empty_context():
    assert "couldn't find" in mock_response("", "en")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('data: {"choices":[{"delta":{"content":"Hi"}}]}', "Hi"),
        ('data:{"choices":[{"delta":{"content":" there"}}]}', " there"),
        ('data: {"choices":[{"delta":{"role":"assistant"}}]}', None),
        ('data: {"choices":[]}', None),
        ("data: [DONE]", None),
        ("data: {not json", None),
        (": keep-alive", None),
        ("", None),
    ],
)
def test_parse_sse_delta(line, expected):
    assert parse_sse_delta(line) == expected


def test_header_text_round_trip_unicode():
    text = "नमस्ते — ¿qué tal?"
    encoded = encode_header_text(text)
    assert encoded.isascii()
    assert decode_header_text(encoded) == text
