"""Transparent theme labels using class-based TF-IDF (c-TF-IDF).

For each theme, all member texts are concatenated into one "class document".
A term's weight is its frequency in the class × log(1 + A / f), where A is the
average number of words per class and f is the term's frequency across all
classes. The top terms become keywords; the label is built from the top 1–3
keywords. Labels are therefore always explainable from the data, and users can
rename any theme.
"""

from __future__ import annotations

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

DOMAIN_STOP_WORDS = frozenset(
    """
    app product please really just like would get got use using used also thing things still even one
    us we team time way lot much make need want could every since keep keeps able think know day days
    week im ive dont doesnt cant isnt thanks hi hello tool pretty bit good great bad love hate new didn
    doesn won wasn aren isn couldn shouldn wouldn haven ll ve re honestly fyi sure known noticed quick
    feedback admin soon fix blocking workflow frustrating happy alternatives otherwise instead
    """.split()
)
STOP_WORDS = sorted(ENGLISH_STOP_WORDS | DOMAIN_STOP_WORDS)


def theme_keywords(docs_by_theme: dict[int, list[str]], top_n: int = 6) -> dict[int, list[str]]:
    themes = [t for t, docs in docs_by_theme.items() if docs]
    if not themes:
        return {}
    class_docs = [" ".join(docs_by_theme[t]) for t in themes]
    vectorizer = CountVectorizer(
        ngram_range=(1, 2),
        stop_words=STOP_WORDS,
        min_df=1,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b",  # noqa: S106
    )
    try:
        counts = vectorizer.fit_transform(class_docs).toarray().astype(float)
    except ValueError:
        return {t: [] for t in themes}
    terms = np.array(vectorizer.get_feature_names_out())
    tf = counts / np.maximum(counts.sum(axis=1, keepdims=True), 1)
    avg_words = counts.sum() / len(themes)
    idf = np.log(1 + avg_words / np.maximum(counts.sum(axis=0), 1))
    ctfidf = tf * idf

    result: dict[int, list[str]] = {}
    for row, theme in enumerate(themes):
        order = np.argsort(-ctfidf[row])
        picked: list[str] = []
        for j in order:
            if ctfidf[row, j] <= 0 or len(picked) >= top_n:
                break
            term = str(terms[j])
            # Skip terms that share a word with an already chosen keyword.
            if set(term.split()) & {w for p in picked for w in p.split()}:
                continue
            picked.append(term)
        result[theme] = picked
    return result


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def label_from_keywords(keywords: list[str], fallback: str, max_words: int = 3) -> str:
    """Join the top keywords (most distinctive first) until ``max_words`` words are used.

    Keywords repeating an already used word (including simple plurals: dashboard/dashboards) are skipped.
    """
    if not keywords:
        return fallback
    parts: list[str] = []
    stems: set[str] = set()
    words = 0
    for k in keywords:
        ks = {_stem(w) for w in k.split()}
        if ks & stems:
            continue
        n = len(k.split())
        if parts and words + n > max_words:
            break
        parts.append(k)
        stems |= ks
        words += n
    return " · ".join(_title(p) for p in parts)[:200]


ACRONYMS = {"api", "csv", "sso", "saml", "ui", "ux", "sdk", "pdf", "url", "ai", "crm", "nps", "rest", "ios"}


def _title(term: str) -> str:
    return " ".join(w.upper() if w in ACRONYMS else w.capitalize() for w in term.split())
