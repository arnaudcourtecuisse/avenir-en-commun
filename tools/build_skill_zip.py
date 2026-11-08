#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Arnaud Courtecuisse
"""Construit dist/avenir-en-commun.zip, à importer dans claude.ai (Paramètres → Capacités → Skills).

Archive reproductible : fichiers triés, dates fixes, mêmes droits. Relancer le
script sans rien changer à la skill redonne une archive identique octet pour
octet, donc le zip versionné ne change que si la skill change.

Usage (depuis la racine du dépôt) : python3 tools/build_skill_zip.py
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "avenir-en-commun"
OUT = ROOT / "dist" / "avenir-en-commun.zip"
SKIP_DIRS = {"__pycache__", "evals"}
STAMP = (2026, 1, 1, 0, 0, 0)


def main():
    files = sorted(p for p in SKILL.rglob("*") if p.is_file()
                   and not SKIP_DIRS & set(p.relative_to(SKILL).parts) and p.suffix != ".pyc")
    if not (SKILL / "SKILL.md").exists():
        sys.exit("SKILL.md introuvable")
    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            info = zipfile.ZipInfo(str(Path(SKILL.name) / p.relative_to(SKILL)), STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if p.suffix == ".py" else 0o644) << 16
            z.writestr(info, p.read_bytes())
    print(f"{OUT.relative_to(ROOT)} : {len(files)} fichiers")
    for p in files:
        print("  " + str(p.relative_to(SKILL.parent)))


if __name__ == "__main__":
    main()
