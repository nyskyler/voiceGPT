import markdown
import textwrap

def format_datetime(value, fmt='%Y년 %m월 %d일 %H:%M'):
    return value.strftime(fmt)

def markdown_to_html(text):
    return markdown.markdown(text)

def shorten_text(text):
    return textwrap.shorten(text, width=40)
