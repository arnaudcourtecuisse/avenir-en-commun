#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Arnaud Courtecuisse
"""Accès au programme « L'Avenir en commun » (2027) publié sur GitHub.

Lit les fichiers du dossier doc/ du dépôt arnaudcourtecuisse/avenir-en-commun
(SOMMAIRE.md, mesures.jsonl, un fichier Markdown par chapitre), avec un cache
local d'une heure. Bibliothèque standard uniquement.

    aec.py sommaire                      sommaire détaillé (parties, chapitres, sections)
    aec.py chercher TERME [TERME ...]    mesures contenant tous les termes
           [--ou] [--type cle|mesure|sous] [--partie N] [--chapitre N.N] [--max N] [--textes]
    aec.py mesure ID [ID ...]            une mesure, avec ses sous-mesures et sa section
    aec.py lire CHAPITRE [--section N]   texte d'un chapitre (« 3.1 ») ou d'une page
    aec.py stats                         nombre de mesures par partie et chapitre

La recherche ignore la casse et les accents, et compare des débuts de mots :
« retrait » trouve « retraite », « retraités »… mais « velo » ne trouve pas
« développer ». Un terme de plusieurs mots (« zone euro ») est cherché tel quel.
Les termes portent sur le texte de la mesure, le titre de sa section et celui
de son chapitre ; avec --textes, aussi sur les textes de présentation.

Variables d'environnement :
    AEC_BASE   autre source (URL ou dossier local contenant SOMMAIRE.md…)
    AEC_CACHE  dossier de cache (défaut : <tmp>/aec-cache) ; AEC_CACHE=off pour le désactiver
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://raw.githubusercontent.com/arnaudcourtecuisse/avenir-en-commun/main/doc/"
GITHUB_VIEW = "https://github.com/arnaudcourtecuisse/avenir-en-commun/blob/main/doc/"
TTL = 3600
TYPES = {"cle": "mesure_cle", "mesure": "mesure", "sous": "sous_mesure"}
LABEL = {"mesure_cle": "MESURE CLÉ", "mesure": "mesure", "sous_mesure": "sous-mesure"}


def base() -> str:
    b = os.environ.get("AEC_BASE", DEFAULT_BASE)
    return b if b.endswith("/") else b + "/"


def get(name: str) -> str:
    b = base()
    if not b.startswith(("http://", "https://")):
        return (Path(b) / name).read_text(encoding="utf-8")
    cache_dir = os.environ.get("AEC_CACHE", str(Path(tempfile.gettempdir()) / "aec-cache"))
    cached = None if cache_dir == "off" else Path(cache_dir) / re.sub(r"[^\w.-]", "_", b + name)
    if cached and cached.exists() and time.time() - cached.stat().st_mtime < TTL:
        return cached.read_text(encoding="utf-8")
    req = urllib.request.Request(b + name, headers={"User-Agent": "aec-skill/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8")
    except Exception as e:
        if cached and cached.exists():  # hors ligne : version en cache, même périmée
            print(f"(réseau indisponible, cache utilisé : {e})", file=sys.stderr)
            return cached.read_text(encoding="utf-8")
        sys.exit(f"Impossible de lire {b + name} : {e}\n"
                 "Sans accès réseau depuis le code, lisez ces fichiers avec l'outil de lecture web.")
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8")
    return text


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.replace("’", "'").replace("œ", "oe").replace("Œ", "oe"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def mesures() -> list[dict]:
    return [json.loads(l) for l in get("mesures.jsonl").splitlines() if l.strip()]


def chapter_num(m: dict) -> str:
    return m["id"].split("-")[0].rsplit(".", 1)[0]


def show(m: dict, with_ctx: bool = True) -> str:
    out = f"[{m['id']}] ({LABEL[m['type']]}) {m['texte']}"
    if with_ctx:
        out += f"\n    {m['chapitre']} › {m['section']}\n    {m['url']}"
    return out


def cmd_chercher(a):
    # début de mot : « velo » trouve « vélos » mais pas « développer »
    pats = [re.compile(r"(?<!\w)" + re.escape(fold(t))) for t in a.termes]
    found = []
    for m in mesures():
        if a.type and m["type"] != TYPES[a.type]:
            continue
        num = chapter_num(m)
        if a.partie and num.split(".")[0] != str(a.partie):
            continue
        if a.chapitre and num != a.chapitre:
            continue
        hay = fold(" ".join((m["texte"], m["section"], m["chapitre"])))
        hits = [p.search(hay) is not None for p in pats]
        if any(hits) if a.ou else all(hits):
            found.append(m)
    print(f"{len(found)} mesure(s) pour {' OU '.join(a.termes) if a.ou else ' ET '.join(a.termes)}"
          + (f" (affichées : {a.max})" if len(found) > a.max else ""))
    last = None
    for m in found[: a.max]:
        sec = (m["chapitre"], m["section"])
        if sec != last:
            print(f"\n## {m['chapitre']} › {m['section']}\n   {m['url']}")
            last = sec
        print(f"- [{m['id']}] ({LABEL[m['type']]}) {m['texte']}")
    if len(found) > a.max:
        print(f"\n… {len(found) - a.max} autres : affinez la recherche ou augmentez --max.")
    if a.textes:
        chercher_textes(pats, a)


def all_pages() -> list[str]:
    """Toutes les pages du programme, dans l'ordre du sommaire."""
    seen = []
    for name in re.findall(r"\]\(([^)/]+\.md)\)", get("SOMMAIRE.md")):
        if name not in seen:
            seen.append(name)
    return seen


def chercher_textes(pats, a):
    """Paragraphes de présentation (hors mesures) contenant les termes."""
    hits = []
    for name in all_pages():
        page_title, section = "", ""
        for line in get(name).splitlines():
            if line.startswith("# "):
                page_title = line[2:]
            elif line.startswith("## ") and not page_title:
                page_title = line[3:]
            elif line.startswith("### "):
                section = line[4:]
            elif line.strip() and not line.startswith(("-", " ", ">", "#", "<!--", "*de ")):
                folded = fold(line)
                ok = [p.search(folded) for p in pats]
                if any(ok) if a.ou else all(ok):
                    first = next(m for m in ok if m)
                    hits.append((name, page_title, section, line, first.start()))
    print(f"\n=== Textes de présentation : {len(hits)} paragraphe(s)")
    for name, title, section, line, pos in hits[: a.max]:
        start = max(0, pos - 250)
        excerpt = ("…" if start else "") + line[start:pos + 350] + ("…" if pos + 350 < len(line) else "")
        print(f"\n## {title}" + (f" › {section}" if section else "") + f"   ({name})\n{excerpt}")


def cmd_mesure(a):
    ms = mesures()
    by_id = {m["id"]: m for m in ms}
    for ident in a.ids:
        m = by_id.get(ident)
        if not m:
            print(f"[{ident}] introuvable (les identifiants ont la forme 1.1.1-3 ou 5.2.1-4.1)")
            continue
        print(show(m))
        if m.get("parent"):
            print(f"    sous-mesure de : {show(by_id[m['parent']], False)}")
        for s in (x for x in ms if x.get("parent") == ident):
            print(f"    - {show(s, False)}")
        print()


def chapter_files() -> dict[str, str]:
    files = {}
    for m in re.finditer(r"^### (\d+\.\d+) .*?\[([^\]]+\.md)\]", get("SOMMAIRE.md"), re.M):
        files[m.group(1)] = m.group(2)
    return files


def cmd_lire(a):
    files = chapter_files()
    target = a.chapitre
    name = files.get(target) or (target if target.endswith(".md") else None)
    if name is None:
        sys.exit(f"Chapitre « {target} » inconnu. Chapitres : {', '.join(files)} "
                 "(ou un nom de fichier, ex. 00-introduction.md, 1-0-….md)")
    text = get(name)
    if a.section:
        parts = re.split(r"(?m)^(?=### )", text)
        if not 1 <= a.section < len(parts):
            sys.exit(f"Section {a.section} inexistante (1 à {len(parts) - 1}).")
        header = re.search(r"(?m)^# .*$", text)
        text = (header.group(0) + "\n\n" if header else "") + parts[a.section]
    print(text)
    print(f"\n(Source Markdown : {GITHUB_VIEW}{name})")


def cmd_stats(a):
    ms = mesures()
    rows: dict[str, list] = {}
    for m in ms:
        key = (chapter_num(m), m["partie"], m["chapitre"])
        c = rows.setdefault(key, [0, 0, 0])
        c[list(TYPES.values()).index(m["type"])] += 1
    tot = [sum(c[i] for c in rows.values()) for i in range(3)]
    print(f"Total : {sum(tot)} mesures ({tot[0]} clés, {tot[1]} mesures, {tot[2]} sous-mesures)")
    part = None
    for (num, p, ch), c in sorted(rows.items(), key=lambda kv: [int(x) for x in kv[0][0].split(".")]):
        if p != part:
            print(f"\n{p}")
            part = p
        print(f"  {num} {ch} : {c[0]} clés, {c[1]} mesures, {c[2]} sous-mesures")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ps = {name: argparse.ArgumentParser(prog=f"aec.py {name}") for name in
          ("sommaire", "chercher", "mesure", "lire", "stats")}
    c = ps["chercher"]
    c.add_argument("termes", nargs="+")
    c.add_argument("--ou", action="store_true", help="au moins un des termes (défaut : tous)")
    c.add_argument("--type", choices=TYPES)
    c.add_argument("--partie", type=int)
    c.add_argument("--chapitre", help="ex. 3.1")
    c.add_argument("--max", type=int, default=60)
    c.add_argument("--textes", action="store_true",
                   help="chercher aussi dans les textes de présentation (introduction, parties, sections)")
    ps["mesure"].add_argument("ids", nargs="+")
    ps["lire"].add_argument("chapitre", help="numéro (ex. 3.1) ou nom de fichier")
    ps["lire"].add_argument("--section", type=int, help="numéro de la section dans le chapitre (1, 2…)")
    if not argv or argv[0] not in ps:
        print(__doc__)
        sys.exit(0 if argv and argv[0] in ("-h", "--help") else 2)
    # intermixé : autorise « chercher smic --ou "salaire minimum" »
    a = ps[argv[0]].parse_intermixed_args(argv[1:])
    if argv[0] == "sommaire":
        print(get("SOMMAIRE.md"))
    else:
        {"chercher": cmd_chercher, "mesure": cmd_mesure, "lire": cmd_lire, "stats": cmd_stats}[argv[0]](a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # sortie coupée (ex. « | head ») : rien à signaler
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
