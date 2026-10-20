"""noslop — a deterministic detector for the written tics of AI prose, tuned for spoken narration.

The rule set is ported from Peter Yang's "no-ai-slop" editing skill (MIT, github.com/petergyang/no-ai-slop)
and adapted for text that is read aloud by a TTS voice: the rules about formatting, bullets and
leading with the point do not apply to a slow narration, while the structural ones (binary
contrasts, colon reveals, fake-profound kickers, recap endings, puffery, metadiscourse, synonym
cycling, robotic rhythm) are exactly what makes a long script sound machine-written.

Three things live here, shared by every writer in the repo (this file is vendored verbatim into
``apps/worker/storyfactory/services/noslop.py``; a test keeps the two copies identical):

* ``lint(text, mode=...)`` — finds the patterns and returns a ``Report`` (per-paragraph findings, a
  per-1,000-words score, the paragraphs worth rewriting). Pure regex, no model, no network.
* ``style_block(mode)`` — the same rules phrased as instructions for the model that writes, so the
  patterns are mostly avoided before the lint ever runs. ``VOICE_PROFILE`` is the Sleep On Facts
  narrator (approved by Gian, 2026-10-21).
* ``polish(text, rewrite=..., mode=...)`` — the targeted-rewrite driver: only the flagged paragraphs
  go back to a (cheap) model, each replacement is accepted only if it scores better and keeps its
  length, and it stops after ``rounds``. It never raises on the model's behalf.

Nothing in here blocks a run: the score is advisory and goes to the ledger.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

MODES = ("sleep", "fiction", "recap")

# ---------------------------------------------------------------------------- word lists (skill)

BANNED_WORDS = (
    "delve", "delves", "delving", "foster", "fosters", "fostering", "leverage", "leverages", "leveraging",
    "utilize", "utilizes", "utilizing", "facilitate", "facilitates", "empower", "empowers", "empowering",
    "streamline", "streamlines", "robust", "cutting-edge", "paradigm shift", "game changer", "game-changer",
    "this is huge", "this changes everything", "tapestry", "realm", "beacon", "multifaceted", "meticulous",
    "meticulously", "intricate", "intricately", "paramount", "transformative", "elevate", "elevates", "embark",
    "embarks", "supercharge", "harness", "harnessing", "ever-evolving",
)

EMPTY_PHRASES = (
    "it's worth noting", "it is worth noting", "it's important to note", "it is important to note",
    "at the end of the day", "when it comes to", "at its core", "in today's world", "in the age of",
    "in the world of", "the reality is", "the truth is", "in terms of", "with regard to", "in order to",
    "going forward", "in this article", "in this video", "let's dive in",
)

THROAT_CLEARING = (
    "here's the thing", "here is the thing", "here's what i mean", "let me be clear", "i'll be honest",
    "to be honest", "the uncomfortable truth", "truth be told", "make no mistake", "let's be honest",
)

FAUX_INSIGHT = (
    "what most people get wrong", "what many people get wrong", "what nobody tells you", "what no one tells you",
    "the part everyone misses", "the part most people skip", "what few people know", "what few people realize",
    "what few people realise", "few people know", "few people realize", "few people realise",
    "most people don't know", "most people never notice", "most people don't realize", "most people don't realise",
    "what most people miss", "what people get wrong",
)

RHETORICAL = (
    "what if i told you", "think about it", "plot twist", "here's the catch", "here is the catch", "here's the kicker",
    "here's why", "here is why", "imagine", "picture this", "let's dive", "let's take a look", "let's look",
    "let's explore", "let's begin", "let's start", "let us begin", "let us look", "did you know", "fun fact",
    "believe it or not", "spoiler alert", "wait for it", "and that's when",
)

SUMMARY_OPENERS = (
    "in conclusion", "ultimately", "overall", "to sum up", "in summary", "all in all", "taken together",
    "in the end", "at the end of the day", "all of this", "altogether", "in sum", "so, in the end",
)

# Words the Sleep On Facts narration uses to describe its own mood instead of letting the facts carry it.
MOOD_WORDS = (
    "quiet", "quietly", "quieter", "quietness", "gentle", "gently", "gentleness", "patient", "patiently",
    "patience", "soft", "softly", "softness", "calm", "calmly", "calmness", "slow", "slowly", "slowness",
    "serene", "serenely", "serenity", "tender", "tenderly", "hushed", "unhurried", "unhurriedly", "peaceful",
    "peacefully", "soothing", "soothingly", "tranquil", "tranquility", "tranquillity", "restful", "stillness",
)

# How the skill says to fix each pattern, in a few words (goes into the rewrite instructions).
FIXES = {
    "banned_word": "replace with a plain word",
    "empty_phrase": "cut the phrase; start at the fact",
    "throat_clearing": "cut the opener; state the point",
    "faux_insight": "cut the setup; let the claim stand alone",
    "rhetorical_setup": "drop the setup; make the point",
    "question": "turn the question into a statement",
    "binary_contrast": "state what it IS, in one plain sentence; drop the 'not X' half",
    "colon_reveal": "rewrite as a plain sentence; keep colons for lists only",
    "ing_analysis": "cut the trailing '-ing' clause, or say the concrete consequence",
    "puffery": "state the fact; delete the words that say it matters",
    "metadiscourse": "delete the aside; the facts carry the point",
    "weasel": "name the source from the notes, or drop the claim",
    "kicker": "delete the final line; end on the last concrete fact already there",
    "recap_paragraph": "this paragraph sums up what was already said: keep only sentences that add a new fact, or return it empty",
    "summary_ending": "cut the recap; end on the last concrete fact",
    "negative_listing": "say what it is; drop the 'not a' list",
    "fragmentation": "join into complete sentences",
    "robotic_rhythm": "vary the sentence openings and lengths",
    "mood_words": "cut the words that describe the mood; keep the facts",
    "em_dashes": "replace the dashes with commas or periods",
}

# ---------------------------------------------------------------------------- regexes

_WORD = re.compile(r"[A-Za-z0-9'’-]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"“(]?[A-Z0-9])")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _phrase_re(phrases: tuple[str, ...]) -> re.Pattern:
    alts = "|".join(re.escape(p).replace(r"\ ", r"\s+").replace("'", "['’]") for p in sorted(phrases, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z-])(?:" + alts + r")(?![A-Za-z-])", re.I)


_BANNED = _phrase_re(BANNED_WORDS)
_EMPTY = _phrase_re(EMPTY_PHRASES)
_THROAT = _phrase_re(THROAT_CLEARING)
_FAUX = _phrase_re(FAUX_INSIGHT)
_RHET = _phrase_re(RHETORICAL)
_SUMMARY = re.compile(r"^[\"“(]?(?:" + "|".join(re.escape(p) for p in SUMMARY_OPENERS) + r")\b", re.I)
_MOOD = _phrase_re(MOOD_WORDS)

_BINARY = [
    # "It is not X, but Y" / "not as X, but as Y" / "not because X but because Y" / "not only X but Y"
    re.compile(r"\bnot\s+(?:just|only|merely|simply|as|a|an|the|from|because|in|of|for|to|by|with|about|so much)\s+"
               r"[^.;?!]{1,90}?,?\s+but\s+(?:also\s+|rather\s+)?(?:as|a|an|the|because|in|of|for|to|by|with|one|something|"
               r"what|how|where|more|less|it|they|this|that|rather|from|a|through|something)\b", re.I),
    # "X is not Y. It is Z." / "It's not X. It's Y." / "I'm not X. I'm Y." (two sentences)
    re.compile(r"(?:\b(?:is|are|was|were|am)\s+not|\b(?:isn|aren|wasn|weren)['’]t|['’](?:s|m|re)\s+not)\s+[^.;?!]{2,80}[.]\s+"
               r"(?:It|They|This|That|These|Those|He|She|We|I|You)(?:['’](?:s|m|re)|\s+(?:is|are|was|were|am))\b"),
    # "... does not X. Instead, it Y" / "Not X. Instead Y"
    re.compile(r"\b(?:not|never|no longer|nor)\b[^.;?!]{1,90}[.]\s+Instead,?\b"),
    # "the question isn't X" / "the point is not X"
    re.compile(r"\b(?:the\s+)?(?:question|point|issue|problem|goal|answer|difference|secret|trick|key)\s+(?:isn['’]t|is\s+not|wasn['’]t|was\s+not)\b", re.I),
    # "less a X than a Y" / "more a X than a Y"
    re.compile(r"\b(?:less|more)\s+(?:a|an|about|of)\s+[^.;?!]{1,50}?\s+than\s+(?:a|an|about|of)\b", re.I),
    # "X, not Y" tail contrast: "..., not as a Z but as a W" is caught above; this is the short "rather than" kicker
    re.compile(r"\bnot\s+(?:a|an|the)\s+[^.;?!,]{1,40},\s+(?:but|only)\s+(?:a|an|the)\b", re.I),
]

_ING_ANALYSIS = re.compile(
    r",\s+(?:highlighting|underscoring|reflecting|showcasing|signal(?:l)?ing|illustrating|demonstrating|emphasi[sz]ing|"
    r"revealing|suggesting|reminding|marking|symboli[sz]ing|representing|embodying|echoing|hinting|pointing to|"
    r"capturing|speaking to|testifying|attesting|affirming|cementing|reinforcing|evoking|conjuring)\b", re.I)

_PUFFERY = re.compile(
    r"\b(?:stands?\s+as\s+a\s+testament|a\s+testament\s+to|testament|pivotal|vital\s+role|"
    r"plays?\s+(?:a|an)\s+(?:crucial|key|vital|central|important|essential|integral)\s+(?:role|part)|"
    r"solidif(?:y|ies|ied|ying)\s+(?:its|their|his|her)|underscores?\s+(?:its|the|their)\s+(?:significance|importance)|"
    r"marks?\s+a\s+(?:pivotal|turning|significant|defining|new\s+chapter)|serves?\s+as\s+a\s+(?:reminder|symbol|bridge|window|"
    r"testament|powerful)|speaks?\s+to\s+the|a\s+(?:quiet|gentle|small|powerful|humble|constant|living|subtle)\s+reminder\s+(?:of|that)|"
    r"reminder\s+of\s+(?:how|the|what)|timeless|remarkabl[ey]|profound(?:ly)?|significance|it\s+is\s+no\s+accident|"
    r"no\s+(?:small|minor)\s+(?:thing|feat|matter|achievement)|cannot\s+be\s+overstated|of\s+immense\s+importance|"
    r"truly\s+(?:special|unique|remarkable)|rich\s+(?:history|heritage|tradition)|(?:a|the)\s+(?:true|living|lasting)\s+legacy)\b", re.I)

_META = re.compile(
    r"\b(?:It\s+tells\s+us|This\s+(?:tells\s+us|means\s+that|shows\s+that|reminds\s+us|matters|is\s+important|is\s+significant)|"
    r"That\s+(?:matters|is\s+why|is\s+the\s+point|is\s+the\s+heart)|In\s+other\s+words|The\s+key\s+(?:point|thing|idea|takeaway)|"
    r"As\s+you\s+can\s+see|This\s+distinction\s+matters|What\s+matters\s+(?:here|most)|It\s+is\s+worth\s+(?:noting|remembering|pausing)|"
    r"worth\s+(?:noting|remembering)|Notice\s+(?:that|how)|Consider\s+(?:that|how|the)|Think\s+of\s+it|It\s+helps\s+to\s+(?:remember|know|notice)|"
    r"To\s+put\s+it\s+(?:simply|another\s+way|differently)|Put\s+simply|Simply\s+put|In\s+that\s+sense|In\s+this\s+sense|"
    r"Seen\s+(?:this|that)\s+way|Understood\s+(?:this|that)\s+way|The\s+(?:lesson|moral|takeaway)\s+(?:is|here))\b")

_WEASEL = re.compile(
    r"\b(?:experts?\s+(?:agree|say|believe|suggest|argue)|studies\s+(?:show|suggest|have\s+shown|indicate)|"
    r"research\s+(?:shows|suggests|indicates)|widely\s+(?:regarded|considered|believed|seen|accepted)|"
    r"many\s+(?:people\s+)?(?:believe|argue|say|think|consider)|some\s+(?:say|argue|believe|claim)|"
    r"it\s+is\s+(?:often|widely|commonly|generally|sometimes)\s+(?:said|believed|thought|held|regarded|claimed|argued)|"
    r"scientists\s+(?:say|believe|agree)|historians\s+(?:agree|say|believe)|it\s+has\s+been\s+(?:said|argued|suggested))\b", re.I)

_NEGATIVE_LIST = re.compile(r"(?:\bNot\s+(?:a|an|the|just|only|merely)\s+[^.?!]{1,50}[.]\s*){2,}")
_FRAGMENTS = [
    re.compile(r"[.]\s+And\s+[^.?!]{1,30}[.]\s+And\b"),
    re.compile(r"\bThat['’]s\s+it[.]\s+That['’]s\b"),
]
_EM_DASH = re.compile(r"[—–]|\s--\s")
_QUESTION = re.compile(r"\?")
_COLON = re.compile(r"(?<![0-9]):\s+(?![0-9])")
_LIST_INTRO = re.compile(r"(?:for\s+example|such\s+as|including|as\s+follows|the\s+following|following|namely|e\.g\.|i\.e\.|"
                         r"these|those|things|parts|ways|kinds|sorts|types|reasons|steps|ingredients|words|names|places)\s*$", re.I)

# A kicker is the LAST sentence of a paragraph that works as a mic-drop rather than a fact.
_KICKER_OPENER = re.compile(
    r"^[\"“(]?(?:From\s+(?:that|this|these|those|outside|China|here)|Beneath|Behind|Within|Together|Through\s+(?:it\s+)?all|"
    r"Through\s+(?:these|this|that|such)|Across\b|And\b|So\b|Here,?\s+(?:then|too|at\s+last)|Still\b|Yet\b|Ahead\b|Soon\b|"
    r"All\s+of\s+(?:this|it|that|them)|All\s+(?:these|those|the\s+while)|In\s+that\b|In\s+this\b|In\s+the\s+(?:end|steady|slow|long)|"
    r"At\s+the\s+end|Before\s+any|By\s+the\s+time|Whether\b|What\s+remains|What\s+is\s+left|Out\s+of\s+(?:that|this|all)|Such\s+is|"
    r"That\s+is\s+(?:the|what|how)|It\s+is\s+(?:a|an|the)\s+(?:small|quiet|gentle|strange|simple|old|long)|There\s+is\s+something|"
    r"Perhaps\b|Somewhere\b|Somehow\b|Every\b|Each\b)")
_KICKER_TAIL = re.compile(
    r"\b(?:onward|onwards|again|season\s+after\s+season|season\s+by\s+season|cell\s+by\s+cell|one\s+by\s+one|"
    r"day\s+by\s+day|year\s+by\s+year|night\s+after\s+night|at\s+a\s+time|hand\s+to\s+hand|to\s+the\s+other|all\s+along|"
    r"all\s+the\s+same|still|ever|forever|the\s+world|through\s+time|story|memory|trace|home|life|rest|sleep|"
    r"the\s+night|silence|the\s+dark|the\s+light|the\s+same|as\s+one|as\s+before|as\s+always|as\s+ever|at\s+last|to\s+come|"
    r"and\s+on|and\s+over|begins?\s+again|goes\s+on|lives\s+on|carries\s+on|carried\s+on|continues?|endures?|remains?|waits?|"
    r"waiting|holding|carrying|becoming|bloom|seasons?|together)[.\"”)]*$", re.I)
_KICKER_SHAPE = [
    re.compile(r"\bmore\s+than\s+(?:a|an|the|just|only|simply|merely)\s+[^.;?!]{1,50}?[:,;]\s", re.I),   # "more than a X: a Y"
    re.compile(r"\bas\s+if\b", re.I),
    re.compile(r"\b(?:a|the)\s+(?:small|quiet|gentle|thin|faint|single|simple|patient|old)\s+(?:trace|thread|fiber|fibre|echo|memory|"
               r"reminder|link|line|mark|pulse|breath|light|rhythm|music|poem|miracle|wonder|craft|vocabulary|foundation|world)\b", re.I),
    re.compile(r"\b(?:world|life|time|history|memory|story)\s+(?:itself|goes\s+on|moves\s+on|turns)\b", re.I),
    re.compile(r"\bone\s+\w+(?:\s+\w+)?\s+at\s+a\s+time\b", re.I),
    re.compile(r"\bfrom\s+\w+\s+to\s+\w+,\s+(?:and\s+)?from\s+\w+\s+to\b", re.I),      # "from gland to scale, from scale to wall"
    re.compile(r"\bso\s+much\s+as\b", re.I),
    re.compile(r"\bits\s+(?:own|next|small|unmistakable|ordinary|long|old)\s+\w+[.\"”)]*$", re.I),
]
_APHORISM = re.compile(r"^[\"“(]?(?:That|It|This|Such|So|And|Nothing|Everything|All|Only|Just|Simply|Always|Never|Here|There)\b")
# The last paragraph of a sleep movement is a recap when it opens by summing up what came before.
_RECAP_OPENER = re.compile(
    r"^[\"“(]?(?:So\b|And\s+so\b|And\s+still\b|And\s+yet\b|Together\b|Across\s+(?:all|these|those|the\s+year|daily|many)|"
    r"Through\s+(?:these|this|all)|In\s+(?:this|that)\s+way|By\s+the\s+time\b|Whether\s+it\b|Taken\s+together|Therefore\b|"
    r"All\s+of\s+(?:this|these|it)|All\s+these\b|Every\s+\w+\s+is\s+still\b|Such\s+is\b|This\s+is\s+(?:the|what|how)\b|"
    r"Here,?\s+then\b|In\s+the\s+end\b|What\s+(?:remains|emerges|began)\b|Seen\s+(?:together|whole|this\s+way)|Step\s+by\s+step)")
_RECAP_INSIDE = re.compile(r"\b(?:therefore|in\s+this\s+way|in\s+that\s+way|also\s+stands\s+as|taken\s+together|all\s+of\s+this)\b", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    para: int
    quote: str
    hard: bool = True

    @property
    def fix(self) -> str:
        return FIXES.get(self.rule, "rewrite plainly")


@dataclass
class Report:
    mode: str
    words: int
    paragraphs: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def hard(self) -> list[Finding]:
        return [f for f in self.findings if f.hard]

    def flagged(self) -> list[int]:
        return sorted({f.para for f in self.hard})

    def per_1k(self) -> float:
        return round(len(self.hard) * 1000.0 / self.words, 2) if self.words else 0.0

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.rule for f in self.hard).most_common())

    def by_para(self) -> dict[int, list[Finding]]:
        out: dict[int, list[Finding]] = {}
        for f in self.hard:
            out.setdefault(f.para, []).append(f)
        return out

    def summary(self) -> str:
        top = ", ".join(f"{k} {v}" for k, v in list(self.counts().items())[:6])
        return (f"slop {self.per_1k()}/1k · {len(self.hard)} findings in {len(self.flagged())}/{self.paragraphs} paragraphs"
                + (f" · {top}" if top else ""))

    def to_dict(self) -> dict:
        return {"per_1k": self.per_1k(), "findings": len(self.hard), "paragraphs": self.paragraphs,
                "flagged": len(self.flagged()), "counts": self.counts()}


# ---------------------------------------------------------------------------- helpers

def paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs (a single-paragraph text is one item)."""
    return [p.strip() for p in _PARA_SPLIT.split(text or "") if p.strip()]


def sentences(para: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(re.sub(r"\s+", " ", para.strip())) if s.strip()]


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def _quote(s: str, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _is_colon_reveal(sent: str) -> bool:
    m = _COLON.search(sent)
    if not m:
        return False
    head, tail = sent[:m.start()].rstrip(), sent[m.end():].strip()
    if _LIST_INTRO.search(head):
        return False
    tail_words = word_count(tail)
    if not 1 <= tail_words <= 18:
        return False
    commas = tail.count(",")
    if commas >= 3 or (commas >= 2 and re.search(r",\s+(?:and|or)\s", tail)):
        return False                                            # a spoken list, not a reveal
    return True


# ---------------------------------------------------------------------------- lint

def lint(text: str, *, mode: str = "sleep", exempt: tuple[str, ...] = ()) -> Report:
    """Find the patterns in ``text``. ``exempt`` lines (e.g. a channel's fixed signature) are skipped."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    paras = paragraphs(text)
    rep = Report(mode=mode, words=word_count(text), paragraphs=len(paras))
    ex = [e.strip().lower() for e in exempt if e and e.strip()]

    def add(rule: str, i: int, quote: str, hard: bool = True) -> None:
        rep.findings.append(Finding(rule, i, _quote(quote), hard))

    for i, para in enumerate(paras):
        if any(e in para.lower() for e in ex):
            continue
        sents = sentences(para)
        low = para.lower()

        for rx, rule in ((_BANNED, "banned_word"), (_EMPTY, "empty_phrase"), (_THROAT, "throat_clearing"),
                         (_FAUX, "faux_insight"), (_RHET, "rhetorical_setup"), (_PUFFERY, "puffery"),
                         (_WEASEL, "weasel"), (_ING_ANALYSIS, "ing_analysis")):
            for m in rx.finditer(para):
                add(rule, i, _context(para, m))
        for m in _META.finditer(para):
            add("metadiscourse", i, _context(para, m))
        for rx in _BINARY:
            for m in rx.finditer(para):
                add("binary_contrast", i, _context(para, m))
        for m in _NEGATIVE_LIST.finditer(para):
            add("negative_listing", i, m.group(0))
        for rx in _FRAGMENTS:
            for m in rx.finditer(para):
                add("fragmentation", i, _context(para, m))

        # sentence-level
        for k, s in enumerate(sents):
            if _is_colon_reveal(s):
                add("colon_reveal", i, s)
            if mode == "sleep" and _QUESTION.search(s):
                add("question", i, s)
            if _SUMMARY.match(s) and (k == len(sents) - 1 or k == 0 and i == len(paras) - 1):
                add("summary_ending", i, s)
            if len(_EM_DASH.findall(s)) >= 2:
                add("em_dashes", i, s, hard=(mode != "sleep"))
        # stacked fragments: two or more consecutive sentences of ≤ 3 words
        short = [word_count(s) <= 3 for s in sents]
        if any(short[k] and short[k + 1] for k in range(len(short) - 1)):
            add("fragmentation", i, " ".join(s for s, sh in zip(sents, short) if sh)[:160])
        # robotic rhythm: three consecutive sentences opening on the same word
        firsts = [(_WORD.findall(s) or [""])[0].lower() for s in sents]
        for k in range(len(firsts) - 2):
            if firsts[k] and firsts[k] == firsts[k + 1] == firsts[k + 2]:
                add("robotic_rhythm", i, " ".join(sents[k:k + 3]))
                break
        # mood words: the narration describing its own mood (sleep only)
        if mode == "sleep":
            hits = _MOOD.findall(para)
            if len(hits) >= 3:
                add("mood_words", i, ", ".join(hits))
            elif hits:
                add("mood_words", i, ", ".join(hits), hard=False)
        # fake-profound kicker: the paragraph's last sentence
        is_last = i == len(paras) - 1
        if len(sents) >= 2:
            last = sents[-1]
            if _kicker(last, len(sents), is_last_para=is_last, mode=mode):
                add("kicker", i, last)
        # recap paragraph: a sleep movement's final paragraph that sums up instead of adding a fact
        if mode == "sleep" and is_last and len(paras) >= 3 and sents:
            if _RECAP_OPENER.match(sents[0]) or _RECAP_INSIDE.search(sents[0]):
                add("recap_paragraph", i, sents[0])
    return rep


def _context(para: str, m: re.Match, pad: int = 60) -> str:
    a, b = max(0, m.start() - pad), min(len(para), m.end() + pad)
    return para[a:b]


def _kicker(last: str, n_sents: int, *, is_last_para: bool, mode: str = "sleep") -> bool:
    words = word_count(last)
    if words == 0:
        return False
    if words <= 7 and n_sents >= 3 and not re.search(r"\d", last) and (_APHORISM.match(last) or _KICKER_TAIL.search(last)):
        return True                                             # aphorism ("It is a body." / "Nothing more.")
    if any(rx.search(last) for rx in _KICKER_SHAPE):
        return True
    opener, tail = bool(_KICKER_OPENER.match(last)), bool(_KICKER_TAIL.search(last))
    if opener and tail:
        return True
    factual = bool(re.search(r"\d", last))                     # a number marks a fact, not a flourish
    if opener and words >= 18 and not factual:
        return True
    if tail and (words >= 22 or is_last_para) and not factual:
        return True
    if mode == "sleep" and is_last_para and _MOOD.search(last):
        return True                                             # the movement signs off by naming its own mood
    return False


# ---------------------------------------------------------------------------- prompt text

VOICE_PROFILE = """THE NARRATOR (this is one voice, every night):
An unhurried person who knows the subject well and is reading aloud at night to someone half asleep.
Not a presenter, not a guide; nobody is being addressed.
Sentences: long, winding sentences carried by concrete nouns and real names, broken now and then by a
short plain one. Present tense for how things are, past tense for history. One particular thing (a
place, a creature, a craftsman, a year, a mechanism) explained fully before the next.
Words: plain physical words (wax, pith, strips, cells, cloth). Real names, dates and numbers from the
notes, with sizes and distances put in everyday terms. No word that describes the mood of the
narration itself; the facts carry the mood.
Moves to keep: lists of concrete things ("stone, clay, wood, papyrus, skin"). Returning to the same
object several times in a movement.
Moves the narrator never makes: ending a paragraph on a kicker, a metaphor or a recap (it ends on the
last fact). "Not X but Y." Colon reveals. Questions. "You." Saying what matters or what to notice.
Calling anything quiet, gentle, patient, soft, calm, slow or still."""

_RULES_COMMON = """- Every paragraph ends on its last concrete fact. No closing line that sums up, draws a moral,
  turns the facts into a metaphor, or tells the listener how to feel about them.
- No "not X but Y", "not X. It is Y", "not because X but because Y", "less a X than a Y", "more
  than a X: a Y". Say what it is, once, plainly.
- No colon used for drama ("The detail that matters: ..."). Colons only introduce a spoken list.
- No trailing "-ing" commentary ("..., reflecting the ...", "..., reminding us ...").
- No importance words: testament, pivotal, vital role, remarkable, profound, timeless, significance,
  reminder of, legacy, rich history. State the fact and let it be.
- No telling the listener what to notice or what it means: "It tells us", "This means", "In other
  words", "What matters here", "worth noting".
- No "experts agree", "studies show", "widely regarded", "many believe". Say who, or leave it out.
- No recap endings: "In the end", "Taken together", "All of this", "Ultimately", "Overall".
- No "not a X. Not a Y. A Z." lists and no stacked fragments ("X. And Y. And Z.").
- Vary sentence openings and lengths; never three sentences in a row opening on the same word.
- Never use: delve, foster, leverage, utilize, facilitate, empower, streamline, robust, cutting-edge,
  tapestry, realm, beacon, multifaceted, meticulous, intricate, paramount, transformative, elevate,
  embark, harness, ever-evolving; nor "it is worth noting", "at the end of the day", "when it comes
  to", "at its core", "in order to", "let's dive in"."""

_RULES_SLEEP = _RULES_COMMON + """
- Do not describe the narration's own mood: no quiet, gentle, patient, soft, calm, slow, serene, still,
  hushed, unhurried, soothing. If the facts are told slowly and plainly, the listener hears the calm.
- No questions of any kind, no "imagine", no "picture this", no "you"."""

_RULES_FICTION = _RULES_COMMON + """
- Keep the narrator's own spoken rhythm: contractions, asides, blunt words are fine; polish is not the goal.
- The hook is a concrete event or line of dialogue, not "What if I told you", "Plot twist", "Here's the
  thing", "What nobody tells you".
- No em dashes; use commas and periods."""

_RULES_RECAP = _RULES_COMMON + """
- Hooks are concrete: a scene, a line, a number. Not "What if I told you", "Plot twist", "Here's why".
- Tell what happens; do not label it (no "pivotal moment", "powerful scene", "masterclass").
- No em dashes; use commas and periods."""

STYLE_RULES = {"sleep": _RULES_SLEEP, "fiction": _RULES_FICTION, "recap": _RULES_RECAP}


def style_block(mode: str = "sleep", *, voice: bool = True) -> str:
    """The rules as prompt text. Sleep mode prepends the narrator profile."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    rules = "STYLE RULES (how this channel is written; they override any habit):\n" + STYLE_RULES[mode]
    return (VOICE_PROFILE + "\n\n" + rules) if (voice and mode == "sleep") else rules


def inject(prompt: str, block: str, *, before: str = "Output as JSON") -> str:
    """Insert ``block`` before the output-format section of a prompt (or append)."""
    i = prompt.find(before)
    if i < 0:
        return prompt.rstrip() + "\n\n" + block + "\n"
    return prompt[:i].rstrip() + "\n\n" + block + "\n\n" + prompt[i:]


REWRITE_PROMPT = """You are editing paragraphs of a script that will be read aloud. Each paragraph below has
patterns that make it sound machine-written. Rewrite ONLY to remove the listed patterns.

Rules:
- Keep every fact, name, number and date exactly; add nothing, invent nothing.
- Keep the same length (within about 15 percent) and the same order of ideas.
- Keep the voice: plain words, concrete nouns, the same tense.
- A paragraph ends on its last concrete fact. Delete a closing line that sums up, moralises or turns
  into a metaphor; do not replace it with a better metaphor.
- "Not X but Y" becomes "Y". A dramatic colon becomes a plain sentence. Drop words that say the fact
  matters; drop asides that tell the listener what to notice.
- A paragraph marked recap_paragraph only repeats what came before: keep the sentences that state a
  new fact, and if none do, return its text as "" (empty) so it is removed.
{{extra}}
{{paragraphs}}

Return JSON: {"paragraphs": [{"id": <id>, "text": "<rewritten paragraph>"}, ...]} with every id present."""


def rewrite_request(items: list[dict], *, extra: str = "") -> str:
    """Prompt text for ``items`` = [{"id", "text", "findings": [Finding, ...]}]."""
    blocks = []
    for it in items:
        notes = "\n".join(f"  - {f.rule}: \"{f.quote}\" → {f.fix}" for f in it["findings"])
        blocks.append(f"PARAGRAPH id={it['id']}\n{it['text']}\nPATTERNS FOUND:\n{notes}")
    return REWRITE_PROMPT.replace("{{extra}}", extra.strip()).replace("{{paragraphs}}", "\n\n".join(blocks))


def parse_rewrite(data: object, ids: list[int]) -> dict[int, str]:
    """``{id: text}`` from the model's answer; tolerant of a bare list or string keys. An empty text is a
    deliberate deletion (allowed for recap paragraphs) and comes back as ``""``."""
    rows = data.get("paragraphs") if isinstance(data, dict) else data
    out: dict[int, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if pid in ids:
            out[pid] = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
    return out


# ---------------------------------------------------------------------------- polish (targeted rewrite)

@dataclass
class PolishResult:
    text: str
    before: Report
    after: Report
    rounds: int = 0
    rewritten: int = 0          # paragraphs whose replacement was accepted

    def to_dict(self) -> dict:
        return {"before": self.before.to_dict(), "after": self.after.to_dict(),
                "rounds": self.rounds, "rewritten": self.rewritten}


def polish(text: str, *, rewrite: Callable[[str], object], mode: str = "sleep", rounds: int = 2,
           exempt: tuple[str, ...] = (), length_band: tuple[float, float] = (0.7, 1.3),
           max_paragraphs: int = 40, extra: str = "") -> PolishResult:
    """Lint, send the flagged paragraphs to ``rewrite(prompt) -> json``, keep the replacements that score
    better, repeat up to ``rounds``. Any exception from ``rewrite`` ends the polishing with the text as it
    stands (a script must never be lost to its editor)."""
    paras = paragraphs(text)
    before = lint(text, mode=mode, exempt=exempt)
    cur = list(paras)
    rep = before
    used, accepted = 0, 0
    while used < rounds and rep.flagged():
        flagged = rep.flagged()[:max_paragraphs]
        items = [{"id": i, "text": cur[i], "findings": rep.by_para()[i]} for i in flagged]
        try:
            answer = rewrite(rewrite_request(items, extra=extra))
        except Exception:  # noqa: BLE001 — the editor failing is not the writer failing
            break
        used += 1
        new = parse_rewrite(answer, flagged)
        changed = False
        for i in flagged:
            cand = new.get(i)
            if cand is None:
                continue
            rules = {f.rule for f in rep.by_para()[i]}
            recap = "recap_paragraph" in rules
            old_w, new_w = word_count(cur[i]), word_count(cand)
            lo = 0.0 if recap else length_band[0]            # a recap paragraph may shrink to nothing
            if not (lo * old_w <= new_w <= length_band[1] * old_w):
                continue
            if not cand and sum(1 for p in cur if p) <= 1:
                continue                                         # never delete the only paragraph
            old_n = _count_at(cur, i, mode, exempt)              # in context: kickers and recaps depend on position
            trial = list(cur)
            trial[i] = cand
            new_n = _count_at(trial, i, mode, exempt) if cand else 0
            if new_n < old_n or (cand and new_n == old_n and recap and new_w < old_w):
                cur[i] = cand
                changed, accepted = True, accepted + 1
        if not changed:
            break
        cur = [p for p in cur if p]                          # drop deleted recap paragraphs
        rep = lint("\n\n".join(cur), mode=mode, exempt=exempt)
    out = "\n\n".join(p for p in cur if p)
    return PolishResult(text=out, before=before, after=lint(out, mode=mode, exempt=exempt), rounds=used, rewritten=accepted)


def _count_at(paras_list: list[str], i: int, mode: str, exempt: tuple[str, ...]) -> int:
    """Hard findings for paragraph ``i`` of ``paras_list`` linted as one text (empty entries are skipped)."""
    kept = [(k, p) for k, p in enumerate(paras_list) if p]
    pos = next((n for n, (k, _) in enumerate(kept) if k == i), None)
    if pos is None:
        return 0
    rep = lint("\n\n".join(p for _, p in kept), mode=mode, exempt=exempt)
    return sum(1 for f in rep.hard if f.para == pos)


def merge_stats(parts: list[dict]) -> dict:
    """Combine ``PolishResult.to_dict()`` dicts from several segments into one ledger-friendly record."""
    if not parts:
        return {}
    def total(key: str, sub: str) -> int:
        return sum(int(p.get(key, {}).get(sub, 0)) for p in parts)
    words = sum(int(p.get("before", {}).get("paragraphs", 0)) for p in parts)   # paragraphs, for the record
    b, a = total("before", "findings"), total("after", "findings")
    counts: Counter = Counter()
    for p in parts:
        counts.update(p.get("after", {}).get("counts", {}))
    return {"before": b, "after": a, "paragraphs": words,
            "rewritten": sum(int(p.get("rewritten", 0)) for p in parts),
            "rounds": max(int(p.get("rounds", 0)) for p in parts), "remaining": dict(counts.most_common(8))}
