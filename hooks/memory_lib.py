#!/usr/bin/env python3
"""
memory_lib — the retrieval engine. Everything else in hooks/ is an envelope
around this file.

THE STORE: one local SQLite file (kept out of the repo — it is a churning
binary) holding, for every markdown file in the corpus:

    docs      metadata: path, kind, title, summary, sha256, [[wikilinks]]
    docs_fts  FTS5/BM25 lexical index over title + body
    vec       sqlite-vec KNN index over a 384-dimension local embedding
    entities  name -> document registry, for exact-name pinning

RETRIEVAL: lexical (FTS5) and semantic (vector) search run independently, and
their two ranked lists are fused with Reciprocal Rank Fusion — the fusion, not
either arm alone, is what makes this work. Lexical alone misses paraphrase;
vector alone drifts to whatever is topically adjacent and is actively harmful on
short prompts. Then one hop is taken over the [[wikilink]] graph you wrote by
hand, which is the cheapest source of relevance in the system: a human already
said these two documents are related.

Before either arm runs, entities the prompt NAMES are pinned to the top. A query
that says someone's name should return that person's file first, not eighth;
soft ranking is bad at this and exact matching is trivially good at it.

Embeddings are computed locally (fastembed, all-MiniLM-L6-v2). Nothing in the
retrieval path makes a network call, which is not a performance decision — a
memory system that phones home about what you asked is a different product.

Design rule: fail open, everywhere. Callers are hooks in the prompt path. A
retrieval bug must degrade to no-context, never to a broken session.

Dependencies: the standard library, sqlite-vec, fastembed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as cfg  # noqa: E402 — the path insert must precede this import

# ----- locations (config-driven) ---------------------------------------------
# Every path comes from config.py, so a fork edits one file and leaves the
# organs alone. MEMORY_OS selects which OS this process serves when one copy of
# these hooks is wired into several repos; each gets its own index.
OS_NAME = cfg.os_name()
OS_ROOT = cfg.os_root()
_CACHE = cfg.cache_dir()
DB_PATH = cfg.db_path()
EPISODIC_DIR = cfg.episodic_dir()
MEMORY_DIR = cfg.memory_dir()

EMBED_MODEL = cfg.EMBED_MODEL
EMBED_DIM = cfg.EMBED_DIM

# corpus: (kind, root, recursive, skip-basenames) — see config.corpus_sources().
SOURCES = cfg.corpus_sources(OS_NAME)

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
_STOP = {
    "the","and","for","with","this","that","what","when","where","which","from",
    "into","about","your","you","are","was","were","have","has","had","will",
    "would","should","can","could","just","like","some","any","all","out","now",
    "get","got","going","want","need","tell","give","make","let","how","why","who",
    "but","not","yes","see","did","does","done","memory","memories","update",
    "remind","think","know","thing","today","tomorrow","really","also","more",
    "than","then","been","over","under","after","before","much","many","one",
    # conversational acknowledgments — zero retrieval signal; without these a bare
    # "ok thanks" porter-stems onto any "Thank you" in the corpus (caught 2026-07-01)
    "thanks","thank","okay","hey","hello","yeah","yep","nope","nah","cool",
    "sure","fine","great","nice","awesome","perfect","please","sounds","welcome",
    "bye","morning","evening","tonight",
}

# ----- embedding (lazy module singleton) -------------------------------------
_EMBEDDER = None


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding
        _EMBEDDER = TextEmbedding(EMBED_MODEL)
    return _EMBEDDER


def embed(text: str):
    import numpy as np
    v = list(_embedder().embed([text or " "]))[0]
    return np.asarray(v, dtype=np.float32)


def _serialize(v):
    return v.tobytes()


# ----- db --------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    # 10+ parallel sessions all touch this DB (SessionStart sync, SessionEnd episodic).
    # Without a busy timeout, a held write lock makes concurrent hooks fail silently
    # (they're fail-open). Wait instead of failing.
    db.execute("PRAGMA busy_timeout=15000")
    db.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


def init_schema(db: sqlite3.Connection):
    db.execute("""CREATE TABLE IF NOT EXISTS docs(
        id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT, title TEXT,
        summary TEXT, stem TEXT, sha256 TEXT, wikilinks TEXT, indexed_at REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS docs_stem ON docs(stem)")
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(title, body, tokenize='porter unicode61')")
    db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(embedding float[{EMBED_DIM}])")
    db.execute("""CREATE TABLE IF NOT EXISTS entities(
        id INTEGER PRIMARY KEY, type TEXT, canonical_name TEXT, doc_id INTEGER,
        aliases TEXT)""")
    db.commit()


# ----- parsing helpers -------------------------------------------------------
def _frontmatter(text):
    m = _FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _title(fp, text, fm):
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return fm.get("title") or " ".join(p.capitalize() for p in fp.stem.split("-"))


def _summary(text, fm):
    if fm.get("description"):
        return fm["description"][:200]
    body = _FM_RE.sub("", text)
    for line in body.split("\n"):
        s = line.strip()
        if not s or s[0] in "#*->|`=_":
            continue
        if set(s) <= set("-=*_ "):
            continue
        return s[:200]
    return ""


def _wikilinks(text):
    out = set()
    for m in _WIKILINK_RE.finditer(text):
        target = m.group(1).split("/")[-1].strip().lower()  # [[folder/file|disp]] -> file
        if target:
            out.add(target)
    return sorted(out)


def _embed_text(title, summary, body):
    # MiniLM truncates ~256 tokens; feed the highest-signal head: title + summary + lead
    return f"{title}\n{summary}\n{body[:1500]}"


GLOBAL_SKIP = cfg.GLOBAL_SKIP  # navigation/index files, not content


def iter_corpus():
    for kind, root, recursive, skip in SOURCES:
        if not root.is_dir():
            continue
        files = root.rglob("*.md") if recursive else root.glob("*.md")
        for fp in files:
            if fp.name in skip or fp.name in GLOBAL_SKIP:
                continue
            yield kind, fp


# ----- sync ------------------------------------------------------------------
def sync(db, verbose=False):
    """Hash-based incremental: (re)index changed/new files, drop removed ones."""
    init_schema(db)
    existing = {row[0]: row[1] for row in db.execute("SELECT path, sha256 FROM docs")}
    seen = set()
    added = updated = removed = 0
    for kind, fp in iter_corpus():
        path = str(fp)
        seen.add(path)
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            continue
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if existing.get(path) == sha:
            continue  # unchanged
        fm = _frontmatter(text)
        title = _title(fp, text, fm)
        summary = _summary(text, fm)
        wl = _wikilinks(text)
        is_new = path not in existing
        row = db.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
        if row:
            doc_id = row[0]
            db.execute("UPDATE docs SET kind=?,title=?,summary=?,stem=?,sha256=?,wikilinks=? WHERE id=?",
                       (kind, title, summary, fp.stem.lower(), sha, json.dumps(wl), doc_id))
            db.execute("DELETE FROM docs_fts WHERE rowid=?", (doc_id,))
            db.execute("DELETE FROM vec WHERE rowid=?", (doc_id,))
            updated += 1
        else:
            cur = db.execute("INSERT INTO docs(path,kind,title,summary,stem,sha256,wikilinks) VALUES (?,?,?,?,?,?,?)",
                             (path, kind, title, summary, fp.stem.lower(), sha, json.dumps(wl)))
            doc_id = cur.lastrowid
            added += 1
        db.execute("INSERT INTO docs_fts(rowid,title,body) VALUES (?,?,?)", (doc_id, title, text))
        db.execute("INSERT INTO vec(rowid,embedding) VALUES (?,?)",
                   (doc_id, _serialize(embed(_embed_text(title, summary, text)))))
        if verbose:
            print(("NEW " if is_new else "UPD "), path)
    for path, _ in list(existing.items()):
        if path not in seen:
            row = db.execute("SELECT id FROM docs WHERE path=?", (path,)).fetchone()
            if row:
                db.execute("DELETE FROM docs WHERE id=?", (row[0],))
                db.execute("DELETE FROM docs_fts WHERE rowid=?", (row[0],))
                db.execute("DELETE FROM vec WHERE rowid=?", (row[0],))
                removed += 1
    build_entities(db)
    db.commit()
    return {"added": added, "updated": updated, "removed": removed,
            "total": db.execute("SELECT COUNT(*) FROM docs").fetchone()[0]}


# ----- entity registry + resolution (Seed 3) ---------------------------------
# Generic tokens that must never, alone, identify an entity (collide too easily).
_ALIAS_STOP = _STOP | {
    "systems","system","inc","llc","labs","lab","corp","company","technologies",
    "technology","group","home","homes","global","solutions","ventures","partners",
    "capital","studio","studios","new","old","house","street","road",
    "de","van","von","la","le","el","al","da","di","san",  # name connectors
}
_ALIAS_STOP |= cfg.EXTRA_ALIAS_STOPWORDS  # the generic words in YOUR corpus


def _aliases_for(kind, title, stem):
    """Name variants an entity can be referred to by. Multi-word (full name) +
    distinctive single tokens (usually surnames). Generic tokens dropped."""
    aliases = set()
    name = title.split("—")[0].split("(")[0].strip().lower()   # leading name in the H1
    if name and len(name) >= 3:
        aliases.add(name)
    for m in re.findall(r'"([^"]+)"', title):                  # quoted nickname: Jordan "JJ" Alvarez
        if m.strip():
            aliases.add(m.strip().lower())
    for t in re.split(r"[-_\s]+", stem):                       # filename tokens (first/last/street #)
        t = t.lower()
        if t.isdigit() and len(t) >= 3:
            aliases.add(t)
        elif len(t) >= 3 and t not in _ALIAS_STOP:
            aliases.add(t)
    return sorted(aliases)


def build_entities(db):
    # person + company only: one file = one entity, clean aliases. Properties are
    # folder-level entities (many sub-files per property) — deferred to avoid false
    # self-collisions; property retrieval already works via Seed 1 + the kind tag.
    db.execute("DELETE FROM entities")
    placeholders = ",".join("?" * len(cfg.ENTITY_KINDS))
    for doc_id, kind, title, stem in db.execute(
            f"SELECT id, kind, title, stem FROM docs WHERE kind IN ({placeholders})",
            tuple(cfg.ENTITY_KINDS)):
        aliases = _aliases_for(kind, title, stem)
        if aliases:
            db.execute("INSERT INTO entities(type,canonical_name,doc_id,aliases) VALUES (?,?,?,?)",
                       (kind, title, doc_id, json.dumps(aliases)))


def resolve(db, prompt):
    """Which known entities the prompt explicitly names. Returns (confident, ambiguous).
    confident = entity matched by an alias unique among the matches (pin it).
    ambiguous = an alias matching >1 entity (two people who share a name)."""
    low = " " + prompt.lower() + " "
    matched = {}             # eid -> {type,name,doc_id, hit_aliases:set}
    alias_owners = {}        # matched alias -> set(eid)
    for eid, etype, cname, doc_id, aj in db.execute(
            "SELECT id,type,canonical_name,doc_id,aliases FROM entities"):
        for a in json.loads(aj):
            multi = (" " in a) or ("." in a)
            hit = (a in low) if multi else (re.search(r"\b" + re.escape(a) + r"\b", low) is not None)
            if hit:
                matched.setdefault(eid, {"type": etype, "name": cname, "doc_id": doc_id, "hits": set()})
                matched[eid]["hits"].add(a)
                alias_owners.setdefault(a, set()).add(eid)
    confident, ambiguous = [], []
    for eid, m in matched.items():
        unique = [a for a in m["hits"] if len(alias_owners[a]) == 1]
        if unique:
            confident.append({"doc_id": m["doc_id"], "name": m["name"],
                              "type": m["type"], "alias": sorted(unique, key=len, reverse=True)[0]})
        else:
            shared = sorted(m["hits"], key=lambda a: -len(alias_owners[a]))[0]
            ambiguous.append({"doc_id": m["doc_id"], "name": m["name"],
                              "type": m["type"], "alias": shared})
    return confident, ambiguous


# ----- retrieval -------------------------------------------------------------
def _fts_query(text):
    toks = [w.lower() for w in _WORD_RE.findall(text)]
    toks = [w for w in toks if len(w) >= 3 and w.lower() not in _STOP]
    if not toks:
        return None
    return " OR ".join('"' + w.replace('"', "") + '"' for w in toks)


def _fts_search(db, query, n):
    q = _fts_query(query)
    if not q:
        return []
    try:
        return [r[0] for r in db.execute(
            "SELECT rowid FROM docs_fts WHERE docs_fts MATCH ? ORDER BY bm25(docs_fts) LIMIT ?",
            (q, n))]
    except sqlite3.OperationalError:
        return []


def _vec_search(db, query, n):
    try:
        q = _serialize(embed(query))
    except Exception:
        return []
    return [r[0] for r in db.execute(
        "SELECT rowid FROM vec WHERE embedding MATCH ? AND k=? ORDER BY distance", (q, n))]


def _rrf(*ranked_lists, k=cfg.RRF_K):
    scores = {}
    for lst in ranked_lists:
        for rank, rid in enumerate(lst, 1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + rank)
    return scores


def _doc(db, doc_id):
    r = db.execute("SELECT id,path,kind,title,summary,wikilinks FROM docs WHERE id=?", (doc_id,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "path": r[1], "kind": r[2], "title": r[3],
            "summary": r[4], "wikilinks": json.loads(r[5] or "[]")}


def retrieve(db, query, k=cfg.MAX_RECALL_RESULTS, n=cfg.CANDIDATES_PER_ARM,
             expand=cfg.LINK_EXPANSION):
    """Hybrid retrieve: entity resolution (pin named entities) + FTS5 + vector
    -> RRF -> 1-hop wikilink expansion + ambiguity flags."""
    results, seen = [], set()
    # Pin the entities the prompt NAMES: an exact name should outrank soft
    # similarity, and a name matching two entities should be flagged, not merged.
    confident, ambiguous = resolve(db, query)
    for e in confident:
        d = _doc(db, e["doc_id"])
        if d and e["doc_id"] not in seen:
            d["score"] = None
            d["via"] = f"named:{e['alias']}"
            results.append(d)
            seen.add(e["doc_id"])
    # collisions surface first, carrying a flag, so the model disambiguates
    # instead of silently treating two same-named entities as one hit
    for e in ambiguous[:4]:
        d = _doc(db, e["doc_id"])
        if d and e["doc_id"] not in seen:
            d["score"] = None
            d["via"] = f"ambiguous:{e['alias']}"
            results.append(d)
            seen.add(e["doc_id"])

    # No-content gate: a prompt with zero FTS-worthy tokens ("hi", "ok", "thanks")
    # has nothing to recall against. Without this, FTS returns nothing and retrieval
    # degrades to vector-only — whose nearest neighbors to "hi" are the other junk
    # episodes (observed 2026-07-01). Entity pinning above still applies (exact match).
    if _fts_query(query) is None:
        return results
    lex = _fts_search(db, query, n)
    vec = _vec_search(db, query, n)
    scores = _rrf(lex, vec)
    if not scores and not results:
        return []
    ranked = sorted(scores, key=scores.get, reverse=True)[:k]
    for rid in ranked:
        if rid in seen:
            continue
        d = _doc(db, rid)
        if not d:
            continue
        d["score"] = round(scores[rid], 5)
        d["via"] = "+".join(x for x, lst in (("lex", lex), ("vec", vec)) if rid in lst)
        results.append(d)
        seen.add(rid)
    # one-hop expansion over the curated wikilink graph
    if expand:
        stem_to_id = {row[0]: row[1] for row in db.execute("SELECT stem,id FROM docs")}
        wanted = []
        for d in results:
            for stem in d["wikilinks"]:
                tid = stem_to_id.get(stem)
                if tid and tid not in seen:
                    wanted.append((tid, d["title"]))
        for tid, src in wanted[:expand]:
            if tid in seen:
                continue
            d = _doc(db, tid)
            if not d:
                continue
            d["score"] = None
            d["via"] = f"link←{src}"
            results.append(d)
            seen.add(tid)
    return results
