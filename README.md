# L’Avenir en commun (2027) : texte intégral et skill Claude

> Projet indépendant et citoyen, sans lien avec La France insoumise et non validé par elle. Le texte reproduit est celui publié par LFI sur [melenchon2027.fr](https://melenchon2027.fr/programme/livre/).

Ce dépôt contient deux choses :

1. **Le programme « L’Avenir en commun » (édition 2027)** de Jean-Luc Mélenchon et de La France insoumise, en Markdown, dans [`doc/`](doc/README.md) : 1 069 mesures, un fichier par chapitre, avec un [sommaire détaillé](doc/SOMMAIRE.md) et un index de toutes les mesures ([`mesures.jsonl`](doc/mesures.jsonl)). Le texte est extrait de [melenchon2027.fr](https://melenchon2027.fr/programme/livre/) par un script déterministe, sans LLM ([`tools/extract_aec.py`](tools/extract_aec.py)).
2. **Une skill Claude** pour explorer ce programme ([`skills/avenir-en-commun/`](skills/avenir-en-commun/SKILL.md)). Elle sert à poser des questions, retrouver toutes les mesures sur un thème, résumer une partie ou trouver les mesures qui concernent sa situation. Elle cite toujours le texte exact, avec un lien vers la section du site officiel. Elle lit le programme en direct dans ce dépôt GitHub : quand le dépôt est mis à jour, la skill l’est aussi.

## Installer la skill

### Claude Code

```
/plugin marketplace add arnaudcourtecuisse/avenir-en-commun
/plugin install avenir-en-commun@avenir-en-commun
```

### Application Claude et claude.ai

1. Téléchargez [`dist/avenir-en-commun.zip`](https://github.com/arnaudcourtecuisse/avenir-en-commun/raw/main/dist/avenir-en-commun.zip).
2. Dans Claude : **Paramètres → Capacités → Skills → Importer une skill**, puis choisissez le zip.
3. Pour que la skill lise le programme, Claude doit pouvoir accéder à `raw.githubusercontent.com` : soit par l’exécution de code avec accès réseau (réglage dans les mêmes paramètres), soit par la recherche ou la lecture web.

Dans une organisation Claude, un administrateur peut aussi, si l’offre le permet, ajouter ce dépôt comme catalogue de plugins. Le fichier [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) est au format attendu.

### Exemples de questions

- « Que propose la France insoumise pour les retraites ? »
- « Liste toutes les mesures du programme sur le logement. »
- « Résume-moi la partie sur la 6e République. »
- « Je suis infirmière à l’hôpital public, qu’est-ce qui changerait pour moi ? »

## Mettre à jour

Depuis la racine du dépôt :

```bash
python3 tools/extract_aec.py doc/            # relit le site, régénère doc/ (échoue si la structure du site change)
python3 tools/build_skill_zip.py             # reconstruit dist/avenir-en-commun.zip si la skill a changé
git diff --stat                              # voir ce qui a changé dans le programme
```

Les deux scripts n’utilisent que la bibliothèque standard de Python.

## Organisation du dépôt

| Chemin | Contenu |
|---|---|
| `doc/` | Le programme : un fichier Markdown par page, `README.md` (sommaire), `SOMMAIRE.md` (sections et liens), `mesures.jsonl` (une mesure par ligne) |
| `skills/avenir-en-commun/` | La skill : `SKILL.md` (instructions) et `scripts/aec.py` (recherche et lecture) |
| `evals/` | Cas de test de la skill, pour `claude plugin eval` |
| `.claude-plugin/` | Manifeste du plugin et catalogue, pour l’installation depuis GitHub |
| `dist/` | La skill en zip, pour l’import dans l’application Claude |
| `tools/` | Extraction du site et construction du zip |
| `assets/` | Logo de La France insoumise (domaine public, marque déposée) et sa provenance |

## Licences

- **Textes du programme** (`doc/`) : © La France insoumise, sous [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.fr), voir [`LICENSE`](LICENSE). Vous pouvez les reprendre en citant la source, sans usage commercial et sous la même licence.
- **Code et skill** (`tools/`, `skills/`, `evals/`, `.claude-plugin/`, `dist/`) : [AGPL-3.0-or-later](tools/LICENSE). Toute version modifiée, y compris proposée comme service en ligne, doit rester libre sous la même licence.
- **Logo** (`assets/`, `.claude-plugin/icon.png`) : domaine public selon Wikimedia Commons, mais marque déposée de La France insoumise ; voir [`assets/README.md`](assets/README.md).
