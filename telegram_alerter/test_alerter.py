import pytest
from main import escape_markdown

def test_markdown_escape():
    # Test all special characters
    special_chars = r"_*[]()~`>#+-=|{}.!"
    escaped = escape_markdown(special_chars)
    
    # Each character should be preceded by a backslash
    expected = r"\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!"
    
    assert escaped == expected
    
def test_markdown_escape_with_text():
    text = "Hello! This is a test_string with *bold* and (parens)."
    escaped = escape_markdown(text)
    
    expected = r"Hello\! This is a test\_string with \*bold\* and \(parens\)\."
    assert escaped == expected
