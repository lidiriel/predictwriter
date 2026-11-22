"""Translations of the user interface (plain Python, no ``uno``).

The English text is the key (like gettext's msgid): ``_("Close")`` returns
the text in the current language, or the English text itself if it has no
translation. The language is LibreOffice's user-interface language, set at
startup by ``uno_glue`` with :func:`set_language`.

Dialog labels come from the ``.xdl`` files in English and are translated
when a dialog opens (``uno_glue.translate_controls``); the tests check that
every English text of the code and of the dialogs has a French translation.

French typography: a no-break space (``\\u00a0``) before ":", ";", "?", "!".
"""

from typing import Dict

NBSP = " "

FR: Dict[str, str] = {
    # Options page (dialogs/OptionsPage.xdl) ----------------------------------
    "Word completion": "Complétion des mots",
    "Suggest word completions while typing": "Proposer la fin des mots pendant la frappe",
    "Dictionary language:": "Langue du dictionnaire" + NBSP + ":",
    "Letters typed before suggesting:": "Lettres tapées avant de proposer" + NBSP + ":",
    "Minimum letters a suggestion adds:": "Une proposition ajoute au moins (lettres)"
    + NBSP
    + ":",
    "Suggestion list": "Liste des propositions",
    "Show the list:": "Afficher la liste" + NBSP + ":",
    "Words shown at once (Alt+1 ... Alt+9):": "Mots affichés à la fois (Alt+1 … Alt+9)"
    + NBSP
    + ":",
    "Words reachable by scrolling:": "Mots accessibles en faisant défiler" + NBSP + ":",
    "Numbers in the list under the cursor:": "Numéros dans la liste sous le curseur"
    + NBSP
    + ":",
    "Also show the highlighted word in the text (selected letters)": (
        "Afficher aussi le mot en surbrillance dans le texte (lettres sélectionnées)"
    ),
    "Off by default: about 40 ms per change to the text, against 5 ms per key for "
    "the list alone.": (
        "Désactivé par défaut" + NBSP + ": env. 40 ms par modification du texte, "
        "contre 5 ms par touche."
    ),
    "Context prediction (n-grams)": "Prédiction selon le contexte (n-grammes)",
    "Rank completions by the previous words": "Classer les propositions selon les mots précédents",
    "Predict the next word after a space": "Prédire le mot suivant après une espace",
    "Accept a completion with:": "Accepter une proposition avec" + NBSP + ":",
    "Accept a next-word prediction with:": "Accepter un mot suivant avec" + NBSP + ":",
    "Build model...": "Créer le modèle…",
    "Tip: turn off Tools ▸ AutoCorrect Options ▸ Word Completion to avoid double "
    "completions.": (
        "Astuce" + NBSP + ": désactivez Outils ▸ Options d'AutoCorrection ▸ "
        "Complétion de mots."
    ),
    "© 2026 Ludovic Mercier · Mozilla Public License 2.0 · data: MPL 2.0, CC BY-SA 4.0 "
    "(see NOTICE.md)": (
        "© 2026 Ludovic Mercier · Mozilla Public License 2.0 · données" + NBSP + ": "
        "MPL 2.0, CC BY-SA 4.0 (voir NOTICE.md)"
    ),
    # Options page lists (uno_glue, keymap)
    "Under the cursor": "Sous le curseur",
    "Bar at the bottom": "Barre en bas",
    "Bar at the top": "Barre en haut",
    "Don't show": "Ne pas afficher",
    "Hidden": "Masqués",
    "Left of the words": "À gauche des mots",
    "Tab or Enter": "Tab ou Entrée",
    "Tab only": "Tab seulement",
    "Enter only": "Entrée seulement",
    # Model line of the Options page (uno_glue.model_status)
    "Building a new model for %s...": "Création d'un nouveau modèle pour %s…",
    "No model for %s yet: use Build model...": "Pas encore de modèle pour %s"
    + NBSP
    + ": utilisez Créer le modèle…",
    "Model %s:": "Modèle %s" + NBSP + ":",
    "%s M words,": "%s M mots,",
    "%s words,": "%s mots,",
    "built %s": "créé le %s",
    # Version line (buildinfo.describe)
    " (+1 commit)": " (+1 commit)",
    " (+%d commits)": " (+%d commits)",
    " — commit %s of %s": " — commit %s du %s",
    " (with uncommitted changes)": " (avec des modifications non validées)",
    " — development version (not packaged)": " — version de développement (non empaquetée)",
    " — built %s": " — construite le %s",
    # Build model dialog (dialogs/BuildModel.xdl) -----------------------------
    "Build the prediction model": "Créer le modèle de prédiction",
    "Sources": "Sources",
    "My documents (.odt, .txt and .pdf files in a folder and its subfolders):": (
        "Mes documents (.odt, .txt et .pdf d'un dossier et de ses sous-dossiers)"
        + NBSP
        + ":"
    ),
    "Browse...": "Parcourir…",
    "Wikimedia project:": "Projet Wikimédia" + NBSP + ":",
    "Limits": "Limites",
    "Maximum download (MB):": "Téléchargement maximal (Mo)" + NBSP + ":",
    "Maximum words read (millions):": "Nombre maximal de mots lus (millions)"
    + NBSP
    + ":",
    "Keep word groups seen at least (times):": "Garder les groupes vus au moins (fois)"
    + NBSP
    + ":",
    "Progress": "Progression",
    "Build": "Créer",
    "Cancel build": "Arrêter",
    "Close": "Fermer",
    # Build model dialog texts (build_ui, uno_glue)
    "Wikimedia text is under CC BY-SA 4.0. It is read in 25 MB slices and never "
    "stored; the model is for your own use and records its source and licence.": (
        "Le texte de Wikimédia est sous licence CC BY-SA 4.0. Il est lu par "
        "tranches de 25 Mo sans être conservé" + NBSP + "; le modèle est réservé "
        "à votre usage et indique sa source et sa licence."
    ),
    "Choose your documents and/or a Wikimedia project.": (
        "Choisissez vos documents et/ou un projet Wikimédia."
    ),
    "Folder not found: %s": "Dossier introuvable" + NBSP + ": %s",
    "About %s words": "Environ %s mots",
    "%d MB to download": "%d Mo à télécharger",
    "about %d min": "environ %d min",
    "model about %d MB": "modèle d'environ %d Mo",
    ". Needs about %d MB free while building.": (
        ". Il faut environ %d Mo libres pendant la création."
    ),
    " %d PDF file(s) will be skipped: install poppler-utils to read PDF files.": (
        " %d fichier(s) PDF seront ignorés" + NBSP + ": installez poppler-utils "
        "pour lire les fichiers PDF."
    ),
    " The new model replaces the current one.": " Le nouveau modèle remplacera l'actuel.",
    "Starting...": "Démarrage…",
    "Reading %s...": "Lecture de %s…",
    "folder %s": "dossier %s",
    ", dump %s": ", copie du %s",
    ", latest dump": ", copie la plus récente",
    "Downloading %s...": "Téléchargement de %s…",
    "%s: %.0f of %.0f MB downloaded": "%s" + NBSP + ": %.0f Mo téléchargés sur %.0f",
    "%s words read (%s words/s)": "%s mots lus (%s mots/s)",
    "Skipped %s: %s": "Ignoré" + NBSP + ": %s (%s)",
    "%d PDF file(s) skipped: install poppler-utils to read PDF files": (
        "%d fichier(s) PDF ignorés" + NBSP + ": installez poppler-utils pour lire "
        "les fichiers PDF"
    ),
    "Counting and indexing %s words...": "Comptage et indexation de %s mots…",
    "Installing the model...": "Installation du modèle…",
    "Done: model built from %s words in %.0f s, now in use.": (
        "Terminé" + NBSP + ": modèle créé à partir de %s mots en %.0f s, "
        "déjà utilisé."
    ),
    " %d file(s) could not be read (see log.txt).": (
        " %d fichier(s) n'ont pas pu être lus (voir log.txt)."
    ),
    "Cancelled; the previous model is kept.": "Annulé"
    + NBSP
    + "; l'ancien modèle est conservé.",
    "Error: %s": "Erreur" + NBSP + ": %s",
    "unknown": "inconnue",
    "Last build: ": "Dernière création" + NBSP + ": ",
    "A build is already running.": "Une création est déjà en cours.",
    "Choose at least one source.": "Choisissez au moins une source.",
    "No Python interpreter found to run the build.": (
        "Aucun interpréteur Python trouvé pour lancer la création."
    ),
    "Cannot start the build: %s": "Impossible de lancer la création" + NBSP + ": %s",
    "Cannot install the model: %s": "Impossible d'installer le modèle" + NBSP + ": %s",
    "build failed (exit %s)": "échec de la création (code %s)",
}

CATALOGS: Dict[str, Dict[str, str]] = {"fr": FR}

_catalog: Dict[str, str] = {}
_language = "en"


def normalize(code: str) -> str:
    """``"fr-FR"``, ``"fr_FR.UTF-8"``, ``"FR"`` -> ``"fr"``; ``""`` -> ``"en"``."""
    code = (code or "").strip().replace("_", "-").split(".")[0]
    return code.split("-")[0].lower() or "en"


def set_language(code: str) -> str:
    """Use the catalog of ``code`` (English if there is none); returns it."""
    global _catalog, _language
    _language = normalize(code)
    _catalog = CATALOGS.get(_language, {})
    return _language


def language() -> str:
    """Current interface language, e.g. ``"fr"`` or ``"en"``."""
    return _language


def _(text: str) -> str:
    """``text`` in the current language (English if not translated)."""
    return _catalog.get(text, text)


def number(text: str) -> str:
    """Decimal separator of the language: ``"12.3"`` -> ``"12,3"`` in French."""
    return text.replace(".", ",") if _language == "fr" else text
