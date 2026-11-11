# FrequencyWords (French, OpenSubtitles 2018)

- `fr_full.txt` from https://github.com/hermitdave/FrequencyWords
  (`content/2018/fr/fr_full.txt`, downloaded 2026-11-11,
  sha256 `dda23c3025fc195b3a7996af26371dcf3abd1aa2cd7218dbbb7c92a49780ab0c`).
- Licence of the lists: CC BY-SA 4.0 (the repository's MIT licence covers its code).
- Used at build time only: `python3 tools/build_wordlist.py --lang fr_FR --freq
  sources/frequencywords/fr_full.txt --elision-model <ngrams_fr_FR.sqlite>`.
