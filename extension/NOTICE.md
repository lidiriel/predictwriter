# Third-party data and code in Predictive Writer

## Code
- **pressagio 0.1.6** (`python/pythonpath/pressagio/`), Peter Bouda —
  Apache License 2.0, see `python/pythonpath/pressagio/LICENSE` and `VENDORED.md`.

## Data (`data/`)
- **French word forms and grammar tags** (`fr_FR.words.tsv`): generated from the
  Hunspell dictionary "Dictionnaires Français Dicollecte" (Grammalecte,
  https://grammalecte.net) — Mozilla Public License 2.0.
- **English word forms** (`en_US.words.tsv`): generated from the Hunspell en_US
  dictionary (SCOWL, http://wordlist.aspell.net) — SCOWL licence (permissive).
- **French word frequencies** (column 2 of `fr_FR.words.tsv`): FrequencyWords by
  Hermit Dave, built from OpenSubtitles 2018
  (https://github.com/hermitdave/FrequencyWords, `content/2018/fr/fr_full.txt`) —
  CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/).
  Changes: counts matched to the dictionary's word forms.
- **Elided pair scores** (`fr_FR.elisions.tsv`): counts of pairs such as `l'année`
  in French Wikipedia (CirrusSearch dump 20261108; authors: see the history of each
  article), combined with the frequencies above — CC BY-SA 4.0.
  Changes: tokenised, counted, scored and filtered.

The two CC BY-SA 4.0 files above are shared under the same licence
(CC BY-SA 4.0). The n-gram model used for context prediction is not shipped: it
is built locally by `tools/train_ngrams.py` and records its own source and
licence in its `_meta` table.
