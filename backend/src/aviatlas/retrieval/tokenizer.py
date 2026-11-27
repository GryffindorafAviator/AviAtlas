"""Mixed Chinese/English tokenization for keyword retrieval.

jieba's search mode emits both whole words and their sub-words (注意力 →
注意, 注意力), which raises recall for Chinese queries; Latin text is lower-cased
and split into word tokens. Changing this behavior changes every stored keyword
index, so bump TOKENIZER_VERSION whenever it does.
"""
from functools import lru_cache
import logging
import re

TOKENIZER_VERSION = "jieba-search-v1"
_WORD = re.compile(r"\w+")
_STOPWORDS = frozenset("""
a an and are as at be but by for from has have in into is it its of on or that the their them then there these
this to was were which while will with
的 了 是 在 和 与 及 或 也 就 都 而 之 其 这 那 个 着 把 被 从 对 等 中 为 以 及其
什么 怎么 怎样 如何 为什么 哪些 哪个 吗 呢 吧 啊 请 一下 我们 你 我
""".split())


@lru_cache(maxsize=1)
def _jieba():
    import jieba
    jieba.setLogLevel(logging.WARNING)
    return jieba


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _jieba().cut_for_search(text.lower()):
        token = raw.strip()
        if token and _WORD.fullmatch(token) and token not in _STOPWORDS:
            tokens.append(token)
    return tokens
