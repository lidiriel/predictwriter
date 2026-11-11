"""Context (n-gram) prediction: a thin wrapper around pressagio (no ``uno``).

``NextWordPredictor.predict(context_words, prefix)`` returns the most likely
words given the previous words: the next word when ``prefix`` is empty,
otherwise completions of ``prefix`` ranked by context.

The model is a SQLite file of n-gram counts in pressagio's format, built by
``tools/train_ngrams.py``. pressagio 0.1.6 is used unmodified; its known
problems (see ``pressagio/VENDORED.md``) are worked around here:

* ``SafeSqliteConnector`` opens the model read-only (pressagio would create an
  empty file), uses parameterised range queries instead of unescaped
  ``LIKE 'prefix%'`` (French apostrophes broke the SQL) and caches the
  unigram total (pressagio scans the whole table on every prediction).
* ``’`` is a separator for pressagio's tokenizer but ``'`` is not, so text is
  normalised to ``'`` (training does the same).

A predictor holds a SQLite connection: create and use it in one thread (the
UNO layer runs it in a worker thread, see ``worker.py``).
"""

import configparser
import os
import re
import sqlite3
import urllib.parse
from typing import Dict, List, Optional, Sequence

import pressagio
import pressagio.callback
import pressagio.dbconnector

DEFAULT_DELTAS = (0.01, 0.1, 0.89)  # presage's defaults for 1-, 2-, 3-grams
_TOP = "\U0010ffff"  # sorts after every other character (UTF-8 bytes too)


_SPACES = re.compile(r"\s")


def normalize(text: str) -> str:
    """Normalise text exactly as ``tools/train_ngrams.py`` does before counting.

    * ``’`` → ``'``: pressagio splits words on ``’`` but not on ``'``.
    * Every Unicode space → ``' '``: pressagio's tokenizer does not know e.g.
      the narrow no-break space (U+202F) that French AutoCorrect puts before
      ``; : ! ?``, which would otherwise stick to the previous word.
    """
    return _SPACES.sub(" ", text.replace("’", "'"))


def open_readonly(path: str) -> sqlite3.Connection:
    """Open a SQLite file read-only; raise if it does not exist."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    uri = "file:%s?mode=ro" % urllib.parse.quote(os.path.abspath(path))
    return sqlite3.connect(uri, uri=True)


def model_info(path: str) -> Dict[str, str]:
    """Metadata stored by ``train_ngrams.py`` (table ``_meta``); {} if none."""
    try:
        con = open_readonly(path)
    except (OSError, sqlite3.Error):
        return {}
    try:
        return dict(con.execute("SELECT key, value FROM _meta").fetchall())
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def model_cardinality(con: sqlite3.Connection) -> int:
    """Highest n for which a ``_n_gram`` table exists (0 if none)."""
    names = {
        row[0]
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    n = 0
    while "_%d_gram" % (n + 1) in names:
        n += 1
    return n


class SafeSqliteConnector(pressagio.dbconnector.SqliteDatabaseConnector):
    """pressagio's SQLite connector, read-only and with safe queries."""

    def __init__(self, dbname: str, cardinality: int = 1) -> None:
        self._unigram_sum: Optional[int] = None
        pressagio.dbconnector.SqliteDatabaseConnector.__init__(
            self, dbname, cardinality
        )

    def open_database(self) -> None:
        self.con = open_readonly(self.dbname)

    def unigram_counts_sum(self) -> int:
        if self._unigram_sum is None:
            row = self.con.execute("SELECT SUM(count) FROM _1_gram").fetchone()
            self._unigram_sum = int(row[0] or 0)
        return self._unigram_sum

    @staticmethod
    def _columns(n: int) -> List[str]:
        """Column names of ``_n_gram``, oldest word first: word_2, word_1, word."""
        return ["word_%d" % i for i in range(n - 1, 0, -1)] + ["word"]

    def ngram_count(self, ngram: Sequence[str]) -> int:
        n = len(ngram)
        where = " AND ".join("%s = ?" % c for c in self._columns(n))
        row = self.con.execute(
            "SELECT count FROM _%d_gram WHERE %s" % (n, where), tuple(ngram)
        ).fetchone()
        return int(row[0]) if row and row[0] and row[0] > 0 else 0

    def ngram_like_table(self, ngram: Sequence[str], limit: int = -1) -> list:
        """Rows ``(context..., word, count)`` whose last word starts with ngram[-1]."""
        n = len(ngram)
        columns = self._columns(n)
        conditions = ["%s = ?" % c for c in columns[:-1]]
        params: list = list(ngram[:-1])
        prefix = ngram[-1]
        if prefix:
            conditions.append("word >= ? AND word < ?")
            params += [prefix, prefix + _TOP]
        query = "SELECT %s, count FROM _%d_gram" % (", ".join(columns), n)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY count DESC"
        if limit >= 0:
            query += " LIMIT ?"
            params.append(limit)
        return self.con.execute(query, params).fetchall()


class _Callback(pressagio.callback.Callback):
    """Feeds pressagio the text before the cursor (no future text)."""

    def __init__(self) -> None:
        pressagio.callback.Callback.__init__(self)
        self.stream = ""

    def past_stream(self) -> str:
        return self.stream

    def future_stream(self) -> str:
        return ""


class NextWordPredictor:
    """Word prediction from an n-gram model (pressagio)."""

    PREDICTOR = "DefaultSmoothedNgramPredictor"

    def __init__(
        self,
        model_path: str,
        max_results: int = 30,
        deltas: Optional[Sequence[float]] = None,
    ) -> None:
        """Open ``model_path`` (raises ``FileNotFoundError``/``ValueError``)."""
        con = open_readonly(model_path)
        try:
            cardinality = model_cardinality(con)
        finally:
            con.close()
        if cardinality == 0:
            raise ValueError("not an n-gram model: %s" % model_path)
        deltas = list(deltas or DEFAULT_DELTAS)[-cardinality:]
        # Pad lower orders if the model has more n than deltas were given.
        deltas = [0.01] * (cardinality - len(deltas)) + deltas
        self.model_path = model_path
        self.cardinality = cardinality
        self.max_results = max_results
        config = configparser.ConfigParser()
        config.read_dict(
            {
                "Selector": {"suggestions": str(max_results)},
                "ContextTracker": {"lowercase_mode": "True"},
                "PredictorRegistry": {"predictors": self.PREDICTOR},
                self.PREDICTOR: {
                    "predictor_class": "SmoothedNgramPredictor",
                    "deltas": " ".join(str(d) for d in deltas),
                    "learn": "False",
                },
                "Database": {
                    "class": "SqliteDatabaseConnector",
                    "database": model_path,
                },
            }
        )
        self._callback = _Callback()
        self._pressagio = pressagio.Pressagio(self._callback, config)
        # Swap pressagio's connector for ours (see module docstring).
        for predictor in self._pressagio.predictor_registry:
            if predictor.db is not None:
                predictor.db.close_database()
            predictor.db = SafeSqliteConnector(model_path, cardinality)

    def predict(self, context_words: Sequence[str], prefix: str = "") -> List[str]:
        """Most likely words, best first, all lowercase.

        Args:
            context_words: Previous words, oldest first.
            prefix: Beginning of the word being typed ('' for the next word).

        Returns:
            Words starting with ``prefix`` (case-insensitively), the prefix
            itself excluded.
        """
        words = [normalize(w) for w in context_words if w]
        prefix = normalize(prefix)
        # A trailing space makes pressagio's prefix empty: "next word".
        stream = " ".join(words) + " " + prefix if words else prefix or " "
        self._callback.stream = stream
        key = prefix.lower()
        result = []
        for word in self._pressagio.predict():
            if word and word != key and word.startswith(key):
                result.append(word)
        return result[: self.max_results]

    def close(self) -> None:
        self._pressagio.close_database()
