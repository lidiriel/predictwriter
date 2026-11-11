# Vendored: pressagio 0.1.6

- Source: https://pypi.org/project/pressagio/0.1.6/ (sdist `pressagio-0.1.6.tar.gz`,
  sha256 `9a9f3cf0b5284e4f46993eb588e4ac416b0d164c54defbc5f1b90c6202a77c76`)
- Project: https://github.com/Poio-NLP/pressagio — author Peter Bouda
- Licence: Apache License 2.0 (`LICENSE`, taken from the project's GitHub repository,
  branch `master`; the sdist does not ship it)
- **Unmodified.** The Python files are byte-for-byte those of the sdist.

Known problems in this version are worked around in `predictive/nextword.py`
(not by editing these files):

- `DatabaseConnector._build_where_like_clause` does not escape quotes or LIKE
  wildcards (`aujourd'h` breaks the SQL) → our connector uses parameterised
  range queries.
- `unigram_counts_sum()` scans the whole unigram table on every prediction
  → cached in our connector.
- `SqliteDatabaseConnector.open_database()` creates an empty database when the
  file is missing → our connector opens the model read-only.
- `’` (U+2019) is a separator but `'` is not → text is normalised to `'`
  before training and before prediction.
- `tokenizer.forward_tokenize_file()` re-reads the first tokens of each line
  (bogus first n-grams) and `callback.Callback.update()` references an
  undefined name → not used; `tools/train_ngrams.py` counts n-grams itself.
