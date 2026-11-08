#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Arnaud Courtecuisse
#
# Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
# modifier selon les termes de la GNU Affero General Public License publiée par
# la Free Software Foundation, version 3 ou (à votre choix) toute version
# ultérieure. Il est distribué SANS AUCUNE GARANTIE. Voir tools/LICENSE.
"""Extraction déterministe de « L'Avenir en commun » (melenchon2027.fr) en Markdown.

Aucune dépendance hors bibliothèque standard, aucun LLM : le site (WordPress)
sert un HTML sémantique dont les classes portent la structure du programme :

    main.introduction | main.partie | main.chapitre   conteneur du texte
    header > h2 / h1                                  partie / chapitre
    h3.section-title                                  section
    div.mesure-cle > h6 + p                           mesure clé
    li.mesure                                         mesure
    li > ul > li.sous_mesure                          sous-mesure

Le convertisseur est volontairement strict : toute balise ou classe non prévue
dans <main> lève une erreur, plutôt que d'être ignorée ou mal rendue. Si le site
change, l'extraction échoue bruyamment au lieu de produire un texte tronqué.

Usage :
    python3 tools/extract_aec.py OUT_DIR [--cache DIR] [--keep-spaces]
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
import unicodedata
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://melenchon2027.fr"
INDEX = "/programme/livre/"
UA = "aec-extract/1.0 (+stdlib urllib)"
LICENCE = "https://creativecommons.org/licenses/by-nc-sa/4.0/deed.fr"
MOIS = "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split()

# --------------------------------------------------------------------------- DOM minimal

VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "source", "area", "col", "embed"}


class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs or {}), parent, []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def elements(self):
        return [c for c in self.children if isinstance(c, Node)]

    def iter(self):
        yield self
        for c in self.elements():
            yield from c.iter()

    def find(self, tag, cls=None):
        for n in self.iter():
            if n.tag == tag and (cls is None or cls in n.classes):
                return n
        return None

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#root")

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:  # ferme jusqu'à la balise correspondante
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html: str) -> Node:
    b = TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


# --------------------------------------------------------------------------- texte

SPACES = "\u00a0\u202f\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"


def clean(s: str, keep_spaces: bool) -> str:
    s = unicodedata.normalize("NFC", s)  # la source mélange accents composés et décomposés (e + U+0301)
    s = s.replace("\u00ad", "")  # traits d'union conditionnels
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch) != "Cc")  # contrôles (ex. U+0085)
    s = s.replace("\u200b", "")  # espace sans chasse
    if not keep_spaces:
        s = s.translate({ord(c): " " for c in SPACES})
    return s


class Strict(Exception):
    pass


class Renderer:
    def __init__(self, keep_spaces=False):
        self.keep = keep_spaces
        self.stats = {"mesure_cle": 0, "mesure": 0, "sous_mesure": 0}

    # ---- inline
    def inline(self, node: Node) -> str:
        out = []
        for c in node.children:
            if isinstance(c, str):
                out.append(clean(c, self.keep))
            elif c.tag == "em" or c.tag == "i":
                out.append(self.wrap("*", self.inline(c)))
            elif c.tag == "strong" or c.tag == "b":
                out.append(self.wrap("**", self.inline(c)))
            elif c.tag == "sup":
                out.append(f"<sup>{self.inline(c).strip()}</sup>")
            elif c.tag == "br":
                out.append("\n")
            elif c.tag == "span" and any(k.startswith("fa-") for k in c.classes):
                pass  # icône Font Awesome
            elif c.tag in ("span", "a"):
                out.append(self.inline(c))
            else:
                raise Strict(f"balise inline inattendue <{c.tag} class={c.attrs.get('class')!r}>")
        return "".join(out)

    @staticmethod
    def wrap(mark, s):
        core = s.strip()
        if not core:
            return s
        lead, trail = s[: len(s) - len(s.lstrip())], s[len(s.rstrip()):]
        return f"{lead}{mark}{core}{mark}{trail}"

    def line(self, node):
        return re.sub(r"[ \t\r\n]+", " ", self.inline(node)).strip()

    # ---- blocs
    def blocks(self, node: Node, skip_text: set[str] = frozenset()) -> list[str]:
        out = []
        for c in node.children:
            if isinstance(c, str):
                if c.strip():
                    raise Strict(f"texte hors bloc : {c.strip()[:60]!r}")
                continue
            cls = c.classes
            if c.tag in ("nav",) or (c.tag == "a" and "next" in cls):
                continue  # navigation
            if c.tag in ("section", "div") and not ({"mesure-cle"} & cls):
                out += self.blocks(c, skip_text)
            elif c.tag == "header":
                out += self.blocks(c, skip_text)
            elif c.tag == "h1":
                out.append("# " + self.line(c))
            elif c.tag == "h2":
                out.append("## " + self.line(c))
            elif c.tag == "h3":
                out.append("### " + self.line(c))
            elif c.tag == "p":
                t = self.line(c)
                if t and t not in skip_text:
                    out.append(escape_block(t))
            elif c.tag == "div" and "mesure-cle" in cls:
                ps = [e for e in c.elements() if e.tag == "p"]
                if len(ps) != 1 or c.find("h6") is None:
                    raise Strict("div.mesure-cle de forme inattendue")
                self.stats["mesure_cle"] += 1
                out.append("> **" + self.line(c.find("h6")) + " :** " + self.line(ps[0]))
            elif c.tag == "ul":
                out.append("\n".join(self.list_items(c, 0)))
            else:
                raise Strict(f"bloc inattendu <{c.tag} class={c.attrs.get('class')!r}>")
        return out

    def list_items(self, ul: Node, depth: int) -> list[str]:
        lines = []
        for li in ul.elements():
            if li.tag != "li":
                raise Strict(f"<{li.tag}> dans <ul>")
            subs = [e for e in li.elements() if e.tag == "ul"]
            if "mesure" in li.classes:
                self.stats["mesure"] += 1
            elif "sous_mesure" in li.classes:
                self.stats["sous_mesure"] += 1
            # contenu propre du <li> (hors sous-listes)
            own = Node("li")
            own.children = [x for x in li.children if not (isinstance(x, Node) and x.tag == "ul")]
            text = self.line(own)
            if text:
                lines.append("  " * depth + "- " + text)
            for sub in subs:
                # <li> sans classe = simple conteneur WordPress d'une sous-liste
                lines += self.list_items(sub, depth + 1)
        return lines


def escape_block(t: str) -> str:
    """Évite qu'un paragraphe soit interprété comme titre, liste ou citation."""
    if re.match(r"^(#{1,6}\s|[-+*>]\s|\d+[.)]\s)", t):
        return "\\" + t
    return t


# --------------------------------------------------------------------------- site

def fetch(path: str, cache: Path | None) -> str:
    if cache:
        f = cache / (path.strip("/").replace("/", "__") + ".html")
        if f.exists():
            return f.read_text(encoding="utf-8")
    for attempt in range(4):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8")
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    time.sleep(0.5)  # politesse
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(html, encoding="utf-8")
    return html


def toc(index_html: str, r: "Renderer"):
    """[(titre_partie, href_partie, [(titre_chapitre, href)])] depuis la table des matières."""
    main = parse(index_html).find("main", "livre")
    if main is None:
        raise Strict("table des matières introuvable (main.livre)")
    parts = []
    for div in main.find("nav", "tdm").elements():
        if "partie" not in div.classes:
            continue
        a = div.find("h2").find("a")
        chaps = [(r.line(x), x.attrs["href"]) for x in div.elements() if x.tag == "a"]
        parts.append((r.line(a), a.attrs["href"], chaps))
    return parts


def page_main(html: str) -> Node:
    for n in parse(html).iter():
        if n.tag == "main" and n.classes & {"introduction", "partie", "chapitre"}:
            return n
    raise Strict("<main> de contenu introuvable")


def date_fr(d: dt.date) -> str:
    return f"{d.day} {MOIS[d.month - 1]} {d.year}"


def convert(html: str, url: str, r: Renderer, today: str) -> str:
    main = page_main(html)
    skip = set()
    h1 = main.find("h1")
    if "introduction" in main.classes and h1 is not None:
        skip.add(r.line(h1))  # le corps répète « Introduction » sous le titre
    body = "\n\n".join(b for b in r.blocks(main, skip) if b.strip())
    head = (f"<!-- Source : {url} — L'Avenir en commun (édition 2027), La France insoumise. "
            f"Licence CC BY-NC-SA 4.0 ({LICENCE}). Converti en markdown le {today}. -->")
    return head + "\n\n" + body + "\n"


def slug(href: str) -> str:
    return href.rstrip("/").split("/programme/livre/")[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path)
    ap.add_argument("--cache", type=Path, help="dossier de cache HTML (réutilisé s'il existe)")
    ap.add_argument("--keep-spaces", action="store_true", help="conserver les espaces insécables / fines")
    a = ap.parse_args()

    today = date_fr(dt.date.today())
    a.out.mkdir(parents=True, exist_ok=True)
    r = Renderer(a.keep_spaces)
    parts = toc(fetch(INDEX, a.cache), r)
    readme = []
    for i, (ptitle, phref, chaps) in enumerate(parts):
        s = slug(phref)
        if s == "introduction":
            name, items = "00-introduction.md", [(f"Introduction : {ptitle.split(':', 1)[-1].strip()}", phref, "00-introduction.md")]
            readme.append(("", items))
        else:
            name = f"{i}-0-{s}.md"
            items = [("Présentation de la partie", phref, name)]
            for k, (ct, ch) in enumerate(chaps, 1):
                items.append((ct, ch, f"{i}-{k}-{s}-{slug(ch).split('/')[-1]}.md"))
            readme.append((ptitle, items))
        for _, href, fname in items:
            url = BASE + href.rstrip("/")
            try:
                md = convert(fetch(href.rstrip("/"), a.cache), url, r, today)
            except Strict as e:
                sys.exit(f"ERREUR {url} : {e}")
            (a.out / fname).write_text(md, encoding="utf-8")
            print(f"{fname}", file=sys.stderr)

    st = r.stats
    total = sum(st.values())
    lines = ["# L’Avenir en commun – Le programme de la Nouvelle France (édition 2027)", "",
             f"Programme de Jean-Luc Mélenchon et de La France insoumise pour l’élection présidentielle de 2027 : "
             f"{total:,} mesures".replace(",", "\u202f" if a.keep_spaces else " ")
             + f" ({st['mesure_cle']} mesures clés, {st['mesure']} mesures, {st['sous_mesure']} sous-mesures).",
             "", f"- Source : [melenchon2027.fr/programme/livre]({BASE}{INDEX}), texte relevé le {today}",
             f"- Licence : textes du site sous [CC BY-NC-SA 4.0]({LICENCE}) ([mentions légales]({BASE}/mentions-legales/), [texte complet](LICENSE))",
             "- Code : tools/extract_aec.py sous [AGPL-3.0-or-later](tools/LICENSE)",
             "- Conversion : automatique et déterministe (tools/extract_aec.py), sans LLM", "", "## Sommaire", ""]
    for ptitle, items in readme:
        if ptitle:
            lines += ["", "### " + ptitle]
        lines += [f"- [{t}]({f})" for t, _, f in items]
    (a.out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK : {st}", file=sys.stderr)


if __name__ == "__main__":
    main()
