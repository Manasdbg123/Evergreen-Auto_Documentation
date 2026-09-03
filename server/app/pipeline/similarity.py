"""Step-to-step similarity, in three tiers of increasing cost.

    tier 1  lexical      free, instant, offline
    tier 2  embeddings   free, offline, ~40ms per batch on CPU
    tier 3  LLM judge    costs money — ambiguous band only

The tiering is not premature optimisation; it is what keeps a diff under a
cent. Most step pairs are obviously the same or obviously unrelated, and
resolving those with an LLM would cost more than generating the SOP did.

Anthropic has no embeddings endpoint, so tier 2 runs a local
sentence-transformers model. It needs no API key and no network after the
first download. If the package is missing the tier degrades to lexical-only
rather than failing — the diff still works, slightly blunter.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from ..config import Config
from ..models import Step

_WORD = re.compile(r"[a-z0-9]+")

#: Words that carry no signal about which step this is. Kept deliberately
#: short — over-stemming makes "Save" and "Submit" look alike, which is the
#: exact distinction the diff exists to catch.
_STOP = {
    "the", "a", "an", "to", "of", "in", "on", "at", "and", "or", "for",
    "is", "are", "be", "will", "then", "your", "you", "it", "this", "that",
    "with", "from", "by", "as", "click", "select", "choose", "press",
}


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


# --------------------------------------------------------------------------
# Tier 1 — lexical
# --------------------------------------------------------------------------


def lexical_similarity(a: Step, b: Step) -> float:
    """Weighted token overlap across the fields that *identify* a step.

    The UI element label is deliberately weighted low here, which is
    counter-intuitive until you notice the failure it causes.

    The label is the single most common thing to change when a UI is updated —
    "Save" becomes "Submit". Weighting it heavily as evidence of *identity*
    means the harder a step changed, the less likely we are to recognise it as
    the same step. Measured on the fixture with the label at 0.20, the
    Save->Submit step scored 0.51 against a 0.62 match threshold and was
    reported as remove + add: the single most important case in the demo,
    wrong, precisely because the signal was doing double duty.

    So: matching signals and change signals are kept separate. Identity comes
    from what stayed the same (the title, the surrounding instruction, the kind
    of control). The label earns a small positive contribution when it matches
    but costs almost nothing when it does not — and `field_changes` still
    reports the label change in full.

    Weights are renormalised over the fields that can actually be compared.
    A field suppressed by `identity_view` (a human edit with no generated
    baseline) is unknown, not mismatched, and scoring it as zero would charge
    the step full weight for evidence nobody has. Measured on the fixture, an
    edited instruction dragged a correct pair to 0.599 against a 0.62
    threshold purely through that dead weight — the edit was neutralised, then
    punished anyway.
    """
    # Both tiers read the same view, so a hand edit cannot be neutralised in
    # the embedding tier and still count against the step lexically. See
    # `Step.identity_view`.
    va, vb = a.identity_view(), b.identity_view()

    # (weight, score) per component; None means "cannot compare — abstain".
    components: list[tuple[float, float | None]] = [
        (0.38, _compare(va["title"], vb["title"])),
        (0.37, _compare(va["instruction"], vb["instruction"])),
        (0.05, _compare_label(va["label"], vb["label"])),
        (0.05, 1.0 if a.ui_element.type == b.ui_element.type else 0.0),
        (0.15, _compare(a.expected_result, b.expected_result)),
    ]

    total = sum(w for w, s in components if s is not None)
    if not total:
        return 0.0
    return sum(w * s for w, s in components if s is not None) / total


def _compare(a: str | None, b: str | None) -> float | None:
    """Token overlap, or None when either side is unknown."""
    if a is None or b is None:
        return None
    return _jaccard(tokens(a), tokens(b))


def _compare_label(a: str | None, b: str | None) -> float | None:
    if a is None or b is None:
        return None
    la, lb = a.strip().lower(), b.strip().lower()
    if not la or not lb:
        return 0.0
    return 1.0 if la == lb else _jaccard(tokens(la), tokens(lb)) * 0.5


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------------
# Tier 2 — local embeddings
# --------------------------------------------------------------------------


@lru_cache(maxsize=2)
def _load_embedder(model_name: str):
    """Loaded once per process. Returns None if unavailable, never raises."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("[similarity] sentence-transformers not installed — lexical tier only")
        return None
    try:
        print(f"[similarity] loading local embedding model '{model_name}' (no API key needed)")
        return SentenceTransformer(model_name, device="cpu")
    except Exception as exc:
        print(f"[similarity] could not load embedding model ({exc}) — lexical tier only")
        return None


def embed_steps(cfg: Config, steps: list[Step]) -> list[list[float]] | None:
    if not cfg.similarity.use_embeddings or not steps:
        return None
    model = _load_embedder(cfg.similarity.embedding_model)
    if model is None:
        return None
    texts = [s.similarity_text() for s in steps]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [list(map(float, v)) for v in vecs]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------
# Combined
# --------------------------------------------------------------------------


def blended_similarity(
    lex: float, emb: float | None, *, embedding_weight: float = 0.6
) -> float:
    """Fuse the two offline tiers.

    Embeddings lead because they survive rewording ("Click Save" vs "Press the
    save button"); lexical is retained because embeddings are *too* forgiving
    about exactly the substitutions that matter here — MiniLM rates "Save" and
    "Submit" as highly similar, and the diff must not.
    """
    if emb is None:
        return lex
    return embedding_weight * emb + (1 - embedding_weight) * lex


def prose_equivalence(cfg: Config, pairs: list[tuple[str, str]]) -> list[float]:
    """Similarity for a batch of (old, new) prose field values.

    Batched deliberately: encoding 40 short strings in one call costs about
    the same as encoding one, and field comparison needs a lot of them.
    """
    if not pairs:
        return []

    lex = [_jaccard(tokens(a), tokens(b)) for a, b in pairs]

    model = _load_embedder(cfg.similarity.embedding_model) if cfg.similarity.use_embeddings else None
    if model is None:
        return lex

    flat = [t for pair in pairs for t in pair]
    # Empty strings embed to noise; handle them by rule instead.
    encodable = [t if t.strip() else " " for t in flat]
    vecs = model.encode(encodable, normalize_embeddings=True, show_progress_bar=False)

    out: list[float] = []
    for i, (a, b) in enumerate(pairs):
        if not a.strip() and not b.strip():
            out.append(1.0)
        elif not a.strip() or not b.strip():
            out.append(0.0)
        else:
            emb = cosine(list(map(float, vecs[2 * i])), list(map(float, vecs[2 * i + 1])))
            # Max, not blend: lexical overlap and semantic match are each
            # sufficient evidence that a field says the same thing.
            out.append(max(emb, lex[i]))
    return out


def similarity_matrix(
    cfg: Config, old: list[Step], new: list[Step]
) -> tuple[list[list[float]], bool]:
    """Full pairwise similarity. Returns (matrix, whether embeddings were used)."""
    old_vecs = embed_steps(cfg, old)
    new_vecs = embed_steps(cfg, new)
    used = old_vecs is not None and new_vecs is not None

    matrix: list[list[float]] = []
    for i, a in enumerate(old):
        row = []
        for j, b in enumerate(new):
            lex = lexical_similarity(a, b)
            emb = cosine(old_vecs[i], new_vecs[j]) if used else None
            row.append(blended_similarity(lex, emb))
        matrix.append(row)
    return matrix, used
