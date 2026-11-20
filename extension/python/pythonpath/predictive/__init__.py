"""Predictive Writer: word completion for LibreOffice Writer.

Modules ``engine``, ``wordlist``, ``nextword``, ``learning`` and ``textutil``
are plain Python. All UNO code lives in ``uno_glue`` (and ``python/main.py``).
"""

__version__ = "1.2"  # fallback only: the packaged version comes from the git tag (tools/build_oxt.py)
