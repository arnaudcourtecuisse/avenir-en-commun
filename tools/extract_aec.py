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

Usage (depuis la racine du dépôt) :
    python3 tools/extract_aec.py doc/ [--cache DIR] [--keep-spaces] [--date AAAA-MM-JJ]

Produit, outre un fichier Markdown par page : README.md (sommaire), SOMMAIRE.md
(sommaire détaillé par section) et mesures.jsonl (une mesure par ligne, avec
identifiant, type, section et lien vers le site officiel).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
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
        self.sections: list[dict] = []  # une entrée par h3.section-title
        self.mesures: list[dict] = []  # une entrée par mesure (clé, simple ou sous-mesure)
        self.page: dict | None = None

    # ---- index structuré (SOMMAIRE.md, mesures.jsonl)
    def begin_page(self, **page):
        """page : fichier, url, partie, chapitre, num (ex. « 1.2 ») ; None pour une page sans mesures."""
        self.page, self.section = page or None, None

    def _section(self, h3: Node, title: str):
        if not self.page:
            return
        anchor = h3.attrs.get("id")
        n = sum(1 for s in self.sections if s["fichier"] == self.page["fichier"]) + 1
        self.section = {"num": f"{self.page['num']}.{n}", "titre": title, "partie": self.page["partie"],
                        "chapitre": self.page["chapitre"], "fichier": self.page["fichier"],
                        "url": self.page["url"] + (f"#{anchor}" if anchor else ""), "compte": [0, 0, 0], "_n": 0}
        self.sections.append(self.section)

    def _mesure(self, kind: str, text: str, parent: dict | None = None) -> dict | None:
        if not self.page:
            raise Strict(f"{kind} hors d'une page de chapitre")
        s = self.section
        if s is None:
            raise Strict(f"{kind} avant toute section")
        if parent is None:
            s["_n"] += 1
            ident = f"{s['num']}-{s['_n']}"
        else:
            k = 1 + sum(1 for x in self.mesures if x.get("parent") == parent["id"])
            ident = f"{parent['id']}.{k}"
        s["compte"][("mesure_cle", "mesure", "sous_mesure").index(kind)] += 1
        m = {"id": ident, "type": kind, "texte": text, "partie": s["partie"], "chapitre": s["chapitre"],
             "section": s["titre"], "fichier": s["fichier"], "url": s["url"]}
        if parent is not None:
            m["parent"] = parent["id"]
        self.mesures.append(m)
        return m

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
                title = self.line(c)
                self._section(c, title)
                out.append("### " + title)
            elif c.tag == "p":
                t = self.line(c)
                if t and t not in skip_text:
                    out.append(escape_block(t))
            elif c.tag == "div" and "mesure-cle" in cls:
                ps = [e for e in c.elements() if e.tag == "p"]
                if len(ps) != 1 or c.find("h6") is None:
                    raise Strict("div.mesure-cle de forme inattendue")
                self.stats["mesure_cle"] += 1
                text = self.line(ps[0])
                self._mesure("mesure_cle", text)
                out.append("> **" + self.line(c.find("h6")) + " :** " + text)
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
            # contenu propre du <li> (hors sous-listes)
            own = Node("li")
            own.children = [x for x in li.children if not (isinstance(x, Node) and x.tag == "ul")]
            text = self.line(own)
            if "mesure" in li.classes:
                self.stats["mesure"] += 1
                self._last_mesure = self._mesure("mesure", text)
            elif "sous_mesure" in li.classes:
                self.stats["sous_mesure"] += 1
                if getattr(self, "_last_mesure", None) is None:
                    raise Strict("sous-mesure sans mesure parente")
                self._mesure("sous_mesure", text, parent=self._last_mesure)
            elif text:
                raise Strict(f"élément de liste sans classe avec du texte : {text[:60]!r}")
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


def convert(html: str, url: str, r: Renderer, today: str, page: dict | None = None) -> str:
    main = page_main(html)
    if page and "chapitre" not in main.classes:
        raise Strict("page de chapitre attendue")
    r.begin_page(**(page or {}))
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


def write_index(out: Path, r: Renderer, parts_meta: list, today: str, keep_spaces: bool):
    """Écrit mesures.jsonl (une mesure par ligne) et SOMMAIRE.md (sections et comptes)."""
    with (out / "mesures.jsonl").open("w", encoding="utf-8") as f:
        for m in r.mesures:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    def compte(c):
        bits = [f"{c[0]} clé" + ("s" if c[0] > 1 else ""), f"{c[1]} mesure" + ("s" if c[1] > 1 else "")]
        if c[2]:
            bits.append(f"{c[2]} sous-mesure" + ("s" if c[2] > 1 else ""))
        return ", ".join(b for b in bits if not b.startswith("0 "))

    L = ["# Sommaire détaillé – L’Avenir en commun (édition 2027)", "",
         f"Généré automatiquement par `tools/extract_aec.py` (texte relevé le {today}). "
         "Chaque section porte un numéro partie.chapitre.section, son nombre de mesures "
         "et un lien vers la section sur le site officiel. Les identifiants de mesures de "
         "`mesures.jsonl` (ex. `1.1.1-3`, sous-mesure `2.2.3-3.1`) prolongent cette numérotation ; "
         "ils sont positionnels et peuvent changer si le site modifie le programme."]
    for ptitle, pfile, chaps in parts_meta:
        L += ["", f"## {ptitle} — [{pfile}]({pfile})"]
        for num, ctitle, cfile in chaps:
            secs = [x for x in r.sections if x["fichier"] == cfile]
            tot = [sum(x["compte"][j] for x in secs) for j in range(3)]
            L += ["", f"### {num} {ctitle} — [{cfile}]({cfile}) ({compte(tot)})", ""]
            L += [f"- {x['num']} [{x['titre']}]({x['url']}) — {compte(x['compte'])}" for x in secs]
    (out / "SOMMAIRE.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="dossier de sortie (dans ce dépôt : doc/)")
    ap.add_argument("--cache", type=Path, help="dossier de cache HTML (réutilisé s'il existe)")
    ap.add_argument("--keep-spaces", action="store_true", help="conserver les espaces insécables / fines")
    ap.add_argument("--date", type=dt.date.fromisoformat, default=dt.date.today(),
                    help="date de relevé à inscrire (AAAA-MM-JJ, défaut : aujourd'hui)")
    a = ap.parse_args()

    today = date_fr(a.date)
    a.out.mkdir(parents=True, exist_ok=True)
    r = Renderer(a.keep_spaces)
    parts = toc(fetch(INDEX, a.cache), r)
    readme, parts_meta = [], []
    for i, (ptitle, phref, chaps) in enumerate(parts):
        s = slug(phref)
        if s == "introduction":
            items = [(ptitle, phref, "00-introduction.md", None)]
            readme.append(("", items))
            parts_meta.append((ptitle, "00-introduction.md", []))
        else:
            name = f"{i}-0-{s}.md"
            items = [("Présentation de la partie", phref, name, None)]
            for k, (ct, ch) in enumerate(chaps, 1):
                fname = f"{i}-{k}-{s}-{slug(ch).split('/')[-1]}.md"
                items.append((ct, ch, fname, {"fichier": fname, "url": BASE + ch.rstrip("/"),
                                              "partie": ptitle, "chapitre": ct, "num": f"{i}.{k}"}))
            readme.append((ptitle, items))
            parts_meta.append((ptitle, name, [(x[3]["num"], x[0], x[2]) for x in items[1:]]))
        for _, href, fname, page in items:
            url = BASE + href.rstrip("/")
            try:
                md = convert(fetch(href.rstrip("/"), a.cache), url, r, today, page)
            except Strict as e:
                sys.exit(f"ERREUR {url} : {e}")
            (a.out / fname).write_text(md, encoding="utf-8")
            print(f"{fname}", file=sys.stderr)

    st = r.stats
    total = sum(st.values())
    if total != len(r.mesures):
        sys.exit(f"ERREUR : {total} mesures comptées mais {len(r.mesures)} indexées")
    lines = ["# L’Avenir en commun – Le programme de la Nouvelle France (édition 2027)", "",
             f"Programme de Jean-Luc Mélenchon et de La France insoumise pour l’élection présidentielle de 2027 : "
             f"{total:,} mesures".replace(",", " " if a.keep_spaces else " ")
             + f" ({st['mesure_cle']} mesures clés, {st['mesure']} mesures, {st['sous_mesure']} sous-mesures).",
             "", f"- Source : [melenchon2027.fr/programme/livre]({BASE}{INDEX}), texte relevé le {today}",
             f"- Licence : textes du site sous [CC BY-NC-SA 4.0]({LICENCE}) ([mentions légales]({BASE}/mentions-legales/), [texte complet](../LICENSE))",
             "- Conversion : automatique et déterministe ([tools/extract_aec.py](../tools/extract_aec.py), sous AGPL-3.0-or-later), sans LLM",
             "- Index : [sommaire détaillé par section](SOMMAIRE.md) et [toutes les mesures, une par ligne](mesures.jsonl) (JSON Lines)",
             "", "## Sommaire", ""]
    for ptitle, items in readme:
        if ptitle:
            lines += ["", "### " + ptitle]
        lines += [f"- [{t}]({f})" for t, _, f, _ in items]
    (a.out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_index(a.out, r, parts_meta, today, a.keep_spaces)
    print(f"OK : {st}", file=sys.stderr)


if __name__ == "__main__":
    main()
