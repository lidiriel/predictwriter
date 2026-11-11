"""UNO glue: document tracking, key interception and suggestion display.

Display
-------
* A numbered list of the N best words (``popup.py``), under the cursor or as
  a bar at the top/bottom of the text area (setting ``list_position``).
* Optionally (setting ``inline``), the missing letters of the highlighted word
  are inserted after the cursor and selected, as Writer's own Word Completion
  does: ``prédi[ction]``.

Keys while suggestions are shown (decided in ``keymap.py``):

* Tab / Enter accept the highlighted word (configurable separately for
  completions and next-word predictions), Alt+1...Alt+9 pick a visible word,
  Up/Down move the highlight (the list scrolls through up to
  ``max_candidates`` words), Page Up/Down move by a page, Esc removes the
  suggestions. These keys are only
  consumed while suggestions are shown, so Tab keeps working in tables and
  lists otherwise. A click on a list entry picks it too.
* Any other key first removes the suggestions, then is handled by Writer
  normally (typing a letter, Space + AutoCorrect, arrows, Ctrl+Z...).
* Moving the cursor elsewhere (mouse click...) removes the suggestions, as
  does saving the document.

Undo history
------------
The suggestion is inserted inside an undo context titled ``UNDO_TITLE``. To
remove it we call ``undo()`` when that action is on top of the stack, so a
rejected suggestion leaves no trace in the Undo list. An accepted suggestion
stays as its own undo step (Ctrl+Z removes the completion).

Keystroke timing (decision)
---------------------------
``XKeyHandler.keyPressed`` fires *before* Writer inserts the typed character.
Rather than guessing the new text (``current_word + KeyChar``, which is wrong
for dead keys, IME, Backspace, AutoCorrect, selections...), ``keyPressed``
posts a callback with ``com.sun.star.awt.AsyncCallback``. The callback runs on
the main thread from the event loop, *after* Writer has processed the key, and
reads the real document state. Accept/reject decisions are taken synchronously
in ``keyPressed``, from the pending-suggestion state.
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import uno
import unohelper
from com.sun.star.awt import XCallback, XKeyHandler
from com.sun.star.document import XDocumentEventListener
from com.sun.star.view import XSelectionChangeListener

from predictive import buildinfo, keymap
from predictive.engine import SENTENCE_END, Engine
from predictive.nextword import NextWordPredictor, model_info
from predictive.logsetup import LOGGER_NAME, setup_logging
from predictive.popup import SuggestionPopup
from predictive.popup_layout import POSITION_NONE, POSITIONS, page_move, page_top
from predictive.settings import Settings
from predictive.textutil import MAX_LOOKBEHIND, extract_context
from predictive.worker import PredictionWorker

log = logging.getLogger(LOGGER_NAME + ".uno")

TEXT_DOCUMENT_SERVICE = "com.sun.star.text.TextDocument"
UNDO_TITLE = "Predictive Writer suggestion"
FALLBACK_LANGUAGE = "en_US"

# Key codes and modifiers: see predictive/keymap.py.
MOD_CTRL = keymap.MOD_CTRL

# Event names of theGlobalEventBroadcaster, checked against LibreOffice 25.2
# (every received event name is logged at DEBUG level).
ATTACH_EVENTS = ("OnNew", "OnLoad", "OnViewCreated")
VIEW_CLOSED_EVENTS = ("OnViewClosed",)
UNLOAD_EVENTS = ("OnUnload",)
BEFORE_SAVE_EVENTS = ("OnSave", "OnSaveAs", "OnSaveTo")


def user_data_dir(ctx: Any) -> str:
    """Return ``<user profile>/predictive-writer`` as a system path."""
    smgr = ctx.getServiceManager()
    subst = smgr.createInstanceWithContext("com.sun.star.util.PathSubstitution", ctx)
    user_url = subst.substituteVariables("$(user)", True)
    return uno.fileUrlToSystemPath(user_url.rstrip("/") + "/predictive-writer")


def read_text_before_cursor(view_cursor: Any) -> Optional[str]:
    """Return the paragraph text left of the view cursor, or None.

    Uses the start of the view cursor's range, so that a selection (e.g. a
    pending suggestion) is ignored. Returns None when the cursor is not in
    text (e.g. a shape or an image is selected).
    """
    text = view_cursor.getText()
    if text is None:
        return None
    cursor = text.createTextCursorByRange(view_cursor.getStart())
    cursor.gotoStartOfParagraph(True)
    return cursor.getString()[-MAX_LOOKBEHIND:]


def char_after_cursor(view_cursor: Any) -> str:
    """The character right of the cursor ('' at the end of the text)."""
    text = view_cursor.getText()
    cursor = text.createTextCursorByRange(view_cursor.getEnd())
    cursor.goRight(1, True)
    return cursor.getString()


def is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch in "'’-")


def key_char(event: Any) -> str:
    """KeyEvent.KeyChar as a str ('' for none)."""
    char = event.KeyChar
    char = getattr(char, "value", char) or ""
    return "" if char == "\x00" else char


def inserts_text(event: Any) -> bool:
    """True if the key types a character (letters, digits, punctuation)."""
    char = key_char(event)
    return bool(char) and char.isprintable() and not event.Modifiers & MOD_CTRL


def describe_key(event: Any) -> str:
    """Short human-readable description of a KeyEvent, for logging."""
    char = key_char(event)
    shown = repr(char) if char and char.isprintable() else "-"
    return "code=%d char=%s mods=%d" % (event.KeyCode, shown, event.Modifiers)


class Pending:
    """Suggestions currently offered at the cursor."""

    COMPLETE = "complete"  # completing the word being typed
    NEXT = "next"  # next-word prediction (after a space)

    def __init__(
        self, text: Any, span: Any, prefix: str, words: List[str], kind: str = COMPLETE
    ) -> None:
        self.kind = kind
        self.text = text  # XText holding the cursor
        # Text cursor spanning the inline letters (collapsed at the insertion
        # point when nothing is shown inline).
        self.span = span
        self.rest = ""  # inline letters currently in the document
        self.prefix = prefix
        self.words = words
        self.index = 0  # highlighted suggestion

    def rest_of(self, index: int) -> str:
        return self.words[index][len(self.prefix) :]


class _Deferred(unohelper.Base, XCallback):
    """AsyncCallback target: runs on the main thread after the key is handled."""

    def __init__(self, session: "ViewSession") -> None:
        self._session = session

    def notify(self, data: Any) -> None:
        self._session.after_key(data)


class _ModelDeferred(unohelper.Base, XCallback):
    """AsyncCallback target for context-model results (main thread)."""

    def __init__(self, session: "ViewSession") -> None:
        self._session = session

    def notify(self, data: Any) -> None:
        self._session.apply_model_result(data)


class ViewSession(unohelper.Base, XKeyHandler, XSelectionChangeListener):
    """Suggestion state for one Writer view (controller)."""

    def __init__(self, app: "App", model: Any, controller: Any) -> None:
        self.app = app
        self.model = model
        self.controller = controller
        self._pending: Optional[Pending] = None
        self._seq = 0  # incremented per key; only the latest callback acts
        self._busy = False  # True while we modify the document ourselves
        self._async = app.ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.awt.AsyncCallback", app.ctx
        )
        self._deferred = _Deferred(self)
        self._popup: Optional[SuggestionPopup] = None
        # Context model: request sent for key number ``seq`` and its results.
        self._model_deferred = _ModelDeferred(self)
        self._model_lock = threading.Lock()
        self._model_results: Dict[int, List[str]] = {}
        self._model_request: Optional[Tuple[int, str, str, List[str]]] = None

    # Lifecycle -------------------------------------------------------------
    def attach(self) -> None:
        self.controller.addKeyHandler(self)
        self.controller.addSelectionChangeListener(self)

    def detach(self) -> None:
        self._pending = None
        for remove in (
            self.controller.removeKeyHandler,
            self.controller.removeSelectionChangeListener,
        ):
            try:
                remove(self)
            except Exception:
                log.debug("listener removal failed (view already disposed?)")
        if self._popup is not None:
            self._popup.dispose()
            self._popup = None

    # XKeyHandler -----------------------------------------------------------
    def keyPressed(self, event: Any) -> bool:
        """Handle keys while suggestions are shown; schedule new ones after typing."""
        try:
            self._seq += 1
            if self._pending is not None and not self._is_displayed():
                # Cursor moved without a selection event, or the letters were
                # overwritten (discard() then leaves the document alone).
                self.discard(restore_selection=True)
            pending = self._pending
            if pending is not None:
                popup = self._popup
                list_shown = popup is not None and popup.visible
                decision = keymap.action_for_key(
                    event.KeyCode,
                    event.Modifiers,
                    key_char(event),
                    len(pending.words),
                    bool(pending.rest),
                    list_shown,
                    popup.visible_count if list_shown else None,
                    self.app.accept_codes(pending.kind),
                )
                if decision.action == keymap.KEEP:
                    return False
                if decision.action == keymap.ACCEPT:
                    self.accept()
                elif decision.action == keymap.PICK:
                    top = popup.top if list_shown else 0
                    self.accept(top + decision.index)
                elif decision.action == keymap.MOVE:
                    # Up/Down wrap around (the list scrolls to follow).
                    count = len(pending.words)
                    self.move_to((pending.index + decision.delta) % count)
                elif decision.action == keymap.PAGE:
                    rows, count = self.app.list_rows, len(pending.words)
                    self.move_to(
                        page_move(pending.index, decision.delta, rows, count),
                        page_top(popup.top, decision.delta, rows, count),
                    )
                else:
                    self.discard()
                if decision.consume:
                    return True
            if self.app.enabled and inserts_text(event):
                self._async.addCallback(self._deferred, self._seq)
                if self.app.debug:
                    log.debug("key %s", describe_key(event))
        except Exception:
            log.exception("keyPressed failed")
        return False

    def keyReleased(self, event: Any) -> bool:
        return False

    # XSelectionChangeListener ----------------------------------------------
    def selectionChanged(self, event: Any) -> None:
        """Cursor moved (mouse, API...): drop a suggestion it left behind."""
        if self._busy or self._pending is None:
            return
        try:
            if not self._is_displayed():
                self.discard(restore_selection=True)
        except Exception:
            log.exception("selectionChanged failed")

    # XEventListener --------------------------------------------------------
    def disposing(self, source: Any) -> None:
        self._pending = None
        self.app.tracker.forget(self)

    # Suggestions -----------------------------------------------------------
    def after_key(self, seq: Any) -> None:
        """Runs after Writer processed a typed key: show new suggestions."""
        if seq != self._seq or self._pending is not None or not self.app.enabled:
            return
        try:
            self._suggest()
        except Exception:
            log.exception("suggestion failed")

    def _suggest(self) -> None:
        if self.model.isReadonly():
            return
        view_cursor = self.controller.getViewCursor()
        if view_cursor is None or not view_cursor.isCollapsed():
            return
        before = read_text_before_cursor(view_cursor)
        if not before or is_word_char(char_after_cursor(view_cursor)):
            return  # nothing typed, or in the middle of a word
        prefix, context = extract_context(before)
        if not prefix:
            # Just typed a space after a word: next-word prediction.
            if before[-1:].isspace() and context and self.app.next_word_enabled:
                self._request_model(before, "", context)
            return
        apostrophe = "\u2019" if "\u2019" in before else "'"
        words = self.app.engine.suggest(prefix, context, self.app.language, apostrophe)
        if self.app.debug:
            log.debug("prefix=%r suggestions=%r", prefix, words)
        if words:
            self._offer(view_cursor, prefix, words, Pending.COMPLETE)
        if self.app.context_enabled and self.app.engine.completes(
            prefix, self.app.language
        ):
            self._request_model(before, prefix, context)

    def _offer(
        self, view_cursor: Any, prefix: str, words: List[str], kind: str
    ) -> None:
        """Show ``words`` at the cursor (list and/or inline letters)."""
        show_list = self.app.list_position != POSITION_NONE
        inline = self.app.inline and kind == Pending.COMPLETE
        if not inline and not show_list:
            return
        text = view_cursor.getText()
        span = text.createTextCursorByRange(view_cursor.getStart())
        pending = Pending(text, span, prefix, words, kind)
        self._pending = pending
        if inline and not self._set_inline(pending.rest_of(0)):
            self._pending = None
            return
        if show_list:
            self._show_list()

    # Context model ---------------------------------------------------------
    def _request_model(self, before: str, prefix: str, context: List[str]) -> None:
        worker = self.app.worker
        if worker is None:
            return
        self._model_request = (self._seq, before, prefix, list(context))
        worker.submit((self, self._seq), context, prefix)

    def post_model_result(self, seq: int, words: List[str]) -> None:
        """Worker thread: hand the result over to the main thread."""
        with self._model_lock:
            self._model_results[seq] = words
        self._async.addCallback(self._model_deferred, seq)

    def apply_model_result(self, seq: Any) -> None:
        """Main thread: show context predictions if nothing changed since."""
        with self._model_lock:
            words = self._model_results.pop(seq, None)
        request = self._model_request
        if words is None or request is None or request[0] != seq or seq != self._seq:
            return  # the user typed something in the meantime
        _, before, prefix, context = request
        self._model_request = None
        try:
            self._apply_model(before, prefix, context, words)
        except Exception:
            log.exception("applying context predictions failed")

    def _apply_model(
        self, before: str, prefix: str, context: List[str], words: List[str]
    ) -> None:
        pending = self._pending
        view_cursor = self.controller.getViewCursor()
        if pending is not None:
            if not self._is_displayed() or pending.prefix != prefix:
                return
        elif not view_cursor.isCollapsed() or (
            read_text_before_cursor(view_cursor) != before
        ):
            return
        apostrophe = "\u2019" if "\u2019" in before else "'"
        head = before[: len(before) - len(prefix)].rstrip()
        sentence_start = not head or head[-1] in SENTENCE_END
        merged = self.app.engine.merge_context(
            prefix,
            words,
            pending.words if pending is not None else [],
            self.app.language,
            apostrophe,
            sentence_start,
            context,
        )
        if self.app.debug:
            log.debug("context %r prefix=%r model=%r", context, prefix, merged)
        if not merged:
            return
        if pending is None:
            kind = Pending.COMPLETE if prefix else Pending.NEXT
            self._offer(view_cursor, prefix, merged, kind)
            return
        if merged == pending.words:
            return
        pending.words, pending.index = merged, 0
        if pending.rest and pending.rest != pending.rest_of(0):
            if not self._set_inline(pending.rest_of(0)):
                self.discard()
                return
        if self.app.list_position != POSITION_NONE:
            self._show_list()

    def _show_list(self) -> None:
        pending = self._pending
        if pending is None:
            return
        if self._popup is None:
            window = self.controller.getFrame().getComponentWindow()
            self._popup = SuggestionPopup(self.app.ctx, window, self.pick_by_mouse)
        start = time.perf_counter()
        shown = self._popup.show(
            pending.words, pending.index, self.app.list_position, self.app.list_rows
        )
        if self.app.debug:
            log.debug(
                "list %s in %.1f ms",
                "shown" if shown else "not shown (cursor not found)",
                (time.perf_counter() - start) * 1000,
            )

    def _hide_list(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def _set_inline(self, rest: str) -> bool:
        """Replace the inline letters by ``rest`` (selected). False on failure."""
        pending = self._pending
        if pending is None:
            return False
        view_cursor = self.controller.getViewCursor()
        self._busy = True
        try:
            if pending.rest:
                self._remove_inline(pending)
            text = pending.text
            insert_at = text.createTextCursorByRange(pending.span.getStart())
            undo = self.model.getUndoManager()
            # Insert through our own text cursor and select the span
            # explicitly: after an undo(), the view cursor does not reliably
            # move past text inserted at its position.
            undo.enterUndoContext(UNDO_TITLE)
            try:
                text.insertString(insert_at, rest, False)
            finally:
                undo.leaveUndoContext()
            span = _inserted_span(text, insert_at, rest)
            if span is None:
                log.warning("could not locate inserted suggestion; undoing it")
                if undo.getCurrentUndoActionTitle() == UNDO_TITLE:
                    undo.undo()
                return False
            pending.span, pending.rest = span, rest
            # Backward selection: anchor after the word, cursor after the
            # typed letters (what Writer's own Word Completion shows).
            view_cursor.gotoRange(span.getEnd(), False)
            view_cursor.gotoRange(span.getStart(), True)
            return True
        finally:
            self._busy = False

    def _remove_inline(self, pending: Pending) -> None:
        """Remove the inline letters, leaving no Undo entry when possible."""
        if not pending.rest or pending.span.getString() != pending.rest:
            pending.rest = ""
            return  # nothing shown, or already overwritten/edited
        undo = self.model.getUndoManager()
        top = undo.getCurrentUndoActionTitle() if undo.isUndoPossible() else None
        if top == UNDO_TITLE:
            undo.undo()
        else:
            log.debug("undo top is %r, deleting the letters instead", top)
            pending.span.setString("")
        # Collapse the span at the insertion point for the next insertion.
        pending.span = pending.text.createTextCursorByRange(pending.span.getStart())
        pending.rest = ""

    def move_to(self, index: int, top: Optional[int] = None) -> None:
        """Highlight suggestion ``index`` (and show it inline if enabled).

        Args:
            top: First visible row of the list after the move (page turns).
        """
        pending = self._pending
        if pending is None or index == pending.index:
            return
        pending.index = index
        if pending.rest and not self._set_inline(pending.rest_of(pending.index)):
            self.discard()
            return
        if self._popup is not None:
            self._popup.select(pending.index, top)

    def accept(self, index: Optional[int] = None) -> None:
        """Complete the word with suggestion ``index`` (default: highlighted)."""
        pending = self._pending
        if pending is None:
            return
        if index is not None and index != pending.index:
            pending.index = index
            if pending.rest and not self._set_inline(pending.rest_of(index)):
                self.discard()
                return
        self._pending = None
        self._hide_list()
        self._busy = True
        try:
            view_cursor = self.controller.getViewCursor()
            if pending.rest:
                view_cursor.gotoRange(pending.span.getEnd(), False)
            else:
                # Nothing inline: insert the letters now (a normal Undo step).
                end = pending.text.createTextCursorByRange(pending.span.getStart())
                pending.text.insertString(end, pending.rest_of(pending.index), False)
                view_cursor.gotoRange(end.getEnd(), False)
        finally:
            self._busy = False
        if self.app.debug:
            log.debug("accepted %r", pending.words[pending.index])

    def pick_by_mouse(self, index: int) -> None:
        """Click on a list entry."""
        try:
            if self._pending is not None and index < len(self._pending.words):
                self.accept(index)
        except Exception:
            log.exception("pick failed")

    def discard(self, restore_selection: bool = False) -> None:
        """Remove the suggestions (inline letters and list).

        Args:
            restore_selection: Put the view cursor back where it is now (the
                user clicked elsewhere); ``undo()`` would otherwise move it
                back to the suggestion.
        """
        pending, self._pending = self._pending, None
        self._hide_list()
        if pending is None or not pending.rest:
            return
        self._busy = True
        try:
            view_cursor = self.controller.getViewCursor()
            saved = None
            if restore_selection:
                saved = view_cursor.getText().createTextCursorByRange(view_cursor)
            self._remove_inline(pending)
            if saved is not None:
                view_cursor.gotoRange(saved.getStart(), False)
                view_cursor.gotoRange(saved.getEnd(), True)
        except Exception:
            log.exception("discard failed")
        finally:
            self._busy = False

    def _is_displayed(self) -> bool:
        """True if the cursor is still exactly where the suggestions were made."""
        pending = self._pending
        if pending is None:
            return False
        try:
            view_cursor = self.controller.getViewCursor()
            return (
                pending.text.compareRegionStarts(view_cursor, pending.span) == 0
                and pending.text.compareRegionEnds(view_cursor, pending.span) == 0
                and pending.span.getString() == pending.rest
            )
        except Exception:  # e.g. cursor now in another XText
            return False


class DocumentTracker(unohelper.Base, XDocumentEventListener):
    """Listens to global document events and attaches sessions to Writer views."""

    def __init__(self, app: "App") -> None:
        self.app = app
        # A list rather than a dict: comparing UNO proxies with ``==`` is
        # reliable, hashing them is not documented.
        self.sessions: List[ViewSession] = []

    def start(self) -> None:
        """Register on the global event broadcaster and attach to open documents."""
        ctx = self.app.ctx
        broadcaster = ctx.getValueByName(
            "/singletons/com.sun.star.frame.theGlobalEventBroadcaster"
        )
        broadcaster.addDocumentEventListener(self)
        desktop = ctx.getServiceManager().createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx
        )
        components = desktop.getComponents().createEnumeration()
        while components.hasMoreElements():
            self._attach(components.nextElement(), None)
        log.info("document tracker started (%d view(s))", len(self.sessions))

    # XDocumentEventListener ------------------------------------------------
    def documentEventOccured(self, event: Any) -> None:  # sic: UNO spelling
        """Dispatch document events (main thread)."""
        try:
            name = event.EventName
            if self.app.debug:
                log.debug("document event %s", name)
            controller = getattr(event, "ViewController", None)
            if name in ATTACH_EVENTS:
                self._attach(event.Source, controller)
            elif name in BEFORE_SAVE_EVENTS:
                for session in self.sessions:
                    if session.model == event.Source:
                        session.discard()
            elif name in VIEW_CLOSED_EVENTS:
                self._detach(
                    lambda s: controller is not None and s.controller == controller
                )
            elif name in UNLOAD_EVENTS:
                self._detach(lambda s: s.model == event.Source)
        except Exception:
            log.exception("documentEventOccured failed")

    def disposing(self, source: Any) -> None:
        self.sessions = []

    # Internals -------------------------------------------------------------
    def _attach(self, model: Any, controller: Optional[Any]) -> None:
        if model is None or not _is_text_document(model):
            return
        if controller is None:
            controller = model.getCurrentController()
        if controller is None or not hasattr(controller, "addKeyHandler"):
            return
        if any(s.controller == controller for s in self.sessions):
            return
        session = ViewSession(self.app, model, controller)
        session.attach()
        self.sessions.append(session)
        log.info("attached to a Writer view (%d view(s))", len(self.sessions))

    def _detach(self, predicate: Callable[[ViewSession], bool]) -> None:
        keep = []
        for session in self.sessions:
            if predicate(session):
                session.detach()
                log.info("detached from a Writer view")
            else:
                keep.append(session)
        self.sessions = keep

    def forget(self, session: ViewSession) -> None:
        self.sessions = [s for s in self.sessions if s is not session]


def _inserted_span(text: Any, insert_at: Any, rest: str) -> Optional[Any]:
    """Text cursor spanning ``rest`` just inserted at ``insert_at``, or None.

    Whether a collapsed cursor ends up before or after text inserted at its
    position is not specified by the API, so try both and check the string.
    """
    n = len(rest)
    span = text.createTextCursorByRange(insert_at.getEnd())
    span.goLeft(n, True)
    if span.getString() == rest:
        return span
    span = text.createTextCursorByRange(insert_at.getStart())
    span.goRight(n, True)
    if span.getString() == rest:
        return span
    return None


def _is_text_document(model: Any) -> bool:
    try:
        return bool(model.supportsService(TEXT_DOCUMENT_SERVICE))
    except Exception:
        return False


class App:
    """Process-wide state: settings, engine and document tracker."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.data_dir = user_data_dir(ctx)
        self.settings = Settings(os.path.join(self.data_dir, "settings.json"))
        self.debug = False
        self.engine = Engine()
        self.language = FALLBACK_LANGUAGE
        self.worker: Optional[PredictionWorker] = None  # context model thread
        self._worker_model = ""
        self._configure()
        self.tracker = DocumentTracker(self)

    @property
    def enabled(self) -> bool:
        return bool(self.settings["enabled"])

    @property
    def inline(self) -> bool:
        return bool(self.settings["inline"])

    @property
    def context_enabled(self) -> bool:
        """Rank completions with the n-gram model (if a model exists)."""
        return self.enabled and bool(self.settings["context_prediction"])

    @property
    def next_word_enabled(self) -> bool:
        """Predict the next word after a space (needs the n-gram model)."""
        return self.enabled and bool(self.settings["next_word"])

    def accept_codes(self, kind: str):
        """Keys accepting a completion or a next-word prediction."""
        key = "accept_next_word" if kind == Pending.NEXT else "accept_completion"
        return keymap.accept_codes(str(self.settings[key]))

    def model_path(self, language: Optional[str] = None) -> str:
        """Location of the n-gram model built by tools/train_ngrams.py."""
        return os.path.join(
            self.data_dir, "ngrams_%s.sqlite" % (language or self.language)
        )

    def _update_worker(self) -> None:
        """Start, keep or stop the context-model thread to match the settings."""
        wanted = self.context_enabled or self.next_word_enabled
        path = self.model_path()
        if wanted and not os.path.isfile(path):
            log.info("context prediction is on but there is no model at %s", path)
            wanted = False
        if self.worker is not None and (not wanted or path != self._worker_model):
            self.worker.close()
            self.worker = None
        if wanted and self.worker is None:
            rows = int(self.engine.max_suggestions)

            def factory() -> NextWordPredictor:
                return NextWordPredictor(path, max_results=rows)

            def on_result(token: Any, words: List[str]) -> None:
                session, seq = token
                session.post_model_result(seq, words)

            self.worker = PredictionWorker(factory, on_result)
            self._worker_model = path
            log.info("context prediction: using %s", path)

    @property
    def list_rows(self) -> int:
        """Words visible at once in the list (Alt+1...Alt+9)."""
        return min(9, max(1, int(self.settings["max_suggestions"])))

    @property
    def list_position(self) -> str:
        position = str(self.settings["list_position"])
        return position if position in POSITIONS else POSITIONS[0]

    def available_languages(self) -> List[str]:
        """Language tags that have a word list, e.g. ``["en_US", "fr_FR"]``."""
        try:
            names = os.listdir(self.engine.data_dir)
        except OSError:
            return []
        suffix = ".words.tsv"
        return sorted(n[: -len(suffix)] for n in names if n.endswith(suffix))

    def _pick_language(self, wanted: str) -> str:
        if os.path.exists(self.engine.wordlist_path(wanted)):
            return wanted
        log.warning("no word list for %s, using %s", wanted, FALLBACK_LANGUAGE)
        return FALLBACK_LANGUAGE

    def _configure(self) -> None:
        """Push the current settings into logging, the engine and the language."""
        self.debug = bool(self.settings["debug"])
        setup_logging(self.data_dir, logging.DEBUG if self.debug else logging.INFO)
        self.engine.min_prefix = max(1, int(self.settings["min_prefix"]))
        self.engine.min_completion = max(1, int(self.settings["min_completion"]))
        # The engine returns every word reachable by scrolling.
        self.engine.max_suggestions = max(
            self.list_rows, min(100, max(1, int(self.settings["max_candidates"])))
        )
        self.language = self._pick_language(str(self.settings["language"]))
        self._update_worker()

    def start(self) -> None:
        log.info(
            "%s starting (enabled=%s, language=%s)",
            buildinfo.describe(),
            self.enabled,
            self.language,
        )
        if self.enabled:
            self.engine.preload(self.language)
        self.tracker.start()

    def update_settings(self, **changes: Any) -> None:
        """Change settings (Options page), save them and apply them now."""
        was_enabled = self.enabled
        for key, value in changes.items():
            self.settings[key] = value
        try:
            self.settings.save()
        except OSError:
            log.exception("cannot save settings")
        self._configure()
        if self.enabled:
            self.engine.preload(self.language)
        else:
            for session in self.tracker.sessions:
                session.discard()
        if self.enabled != was_enabled:
            log.info("suggestions %s", "enabled" if self.enabled else "disabled")
        log.info("settings updated: %s", ", ".join(sorted(changes)))


_app: Optional[App] = None


def start(ctx: Any) -> App:
    """Entry point called by the startup Job and the Options page. Idempotent."""
    global _app
    if _app is None:
        _app = App(ctx)
        _app.start()
    return _app


# Options page (Tools → Options → Languages and Locales → Predictive Text) ----
# Control names in dialogs/OptionsPage.xdl.
OPT_ENABLED = "Enabled"
OPT_LANGUAGE = "Language"
OPT_MIN_PREFIX = "MinPrefix"
OPT_MIN_COMPLETION = "MinCompletion"
OPT_DEBUG = "Debug"
OPT_LIST_POSITION = "ListPosition"
OPT_MAX_SUGGESTIONS = "MaxSuggestions"
OPT_MAX_CANDIDATES = "MaxCandidates"
OPT_CONTEXT = "Context"
OPT_NEXT_WORD = "NextWord"
OPT_ACCEPT_COMPLETION = "AcceptCompletion"
OPT_ACCEPT_NEXT_WORD = "AcceptNextWord"
OPT_MODEL_STATUS = "ModelStatus"
OPT_VERSION_INFO = "VersionInfo"
OPT_INLINE = "Inline"
# Entries of the ListPosition list, in the order of popup_layout.POSITIONS.
LIST_POSITION_LABELS = (
    "Under the cursor",
    "Bar at the bottom",
    "Bar at the top",
    "Don't show",
)


def options_page_event(ctx: Any, window: Any, event: str) -> bool:
    """Handle the Options page events sent by LibreOffice.

    Args:
        window: The page (an XControlContainer built from OptionsPage.xdl).
        event: ``"initialize"`` (page shown), ``"ok"`` (OK/Apply) or
            ``"back"`` (Reset, or Cancel before the page is shown again).

    Returns:
        True if the event was handled.
    """
    app = start(ctx)
    if event in ("initialize", "back"):
        _load_options_page(app, window)
        return True
    if event == "ok":
        _save_options_page(app, window)
        return True
    return False


def model_status(app: App) -> str:
    """One line describing the n-gram model, for the Options page."""
    path = app.model_path()
    info = model_info(path)
    if not info:
        return (
            "No model for %s. Build one with: python3 tools/train_ngrams.py "
            "--wikipedia" % app.language
        )
    parts = ["Model %s:" % info.get("language", app.language)]
    if "words" in info:
        parts.append("%s words," % "{:,}".format(int(info["words"])).replace(",", " "))
    if "ngrams_3" in info:
        parts.append(
            "%s 3-grams," % "{:,}".format(int(info["ngrams_3"])).replace(",", " ")
        )
    if "wikipedia_dump" in info:
        parts.append("Wikipedia %s (CC BY-SA 4.0)," % info["wikipedia_dump"])
    parts.append("built %s." % info.get("created", "?"))
    return " ".join(parts)


def _model(window: Any, name: str) -> Any:
    return window.getControl(name).getModel()


def _load_options_page(app: App, window: Any) -> None:
    settings = app.settings
    _model(window, OPT_ENABLED).State = 1 if settings["enabled"] else 0
    _model(window, OPT_DEBUG).State = 1 if settings["debug"] else 0
    _model(window, OPT_MIN_PREFIX).Value = float(settings["min_prefix"])
    _model(window, OPT_MIN_COMPLETION).Value = float(settings["min_completion"])
    _model(window, OPT_MAX_SUGGESTIONS).Value = float(settings["max_suggestions"])
    _model(window, OPT_MAX_CANDIDATES).Value = float(settings["max_candidates"])
    _model(window, OPT_INLINE).State = 1 if settings["inline"] else 0
    position = _model(window, OPT_LIST_POSITION)
    position.StringItemList = LIST_POSITION_LABELS
    position.SelectedItems = (POSITIONS.index(app.list_position),)
    _model(window, OPT_CONTEXT).State = 1 if settings["context_prediction"] else 0
    _model(window, OPT_NEXT_WORD).State = 1 if settings["next_word"] else 0
    for name, key in (
        (OPT_ACCEPT_COMPLETION, "accept_completion"),
        (OPT_ACCEPT_NEXT_WORD, "accept_next_word"),
    ):
        choice = str(settings[key])
        if choice not in keymap.ACCEPT_KEY_CHOICES:
            choice = keymap.ACCEPT_KEY_CHOICES[0]
        model = _model(window, name)
        model.StringItemList = keymap.ACCEPT_KEY_LABELS
        model.SelectedItems = (keymap.ACCEPT_KEY_CHOICES.index(choice),)
    _model(window, OPT_MODEL_STATUS).Label = model_status(app)
    version = _model(window, OPT_VERSION_INFO)
    version.Label = buildinfo.describe()
    version.TextColor = 0x808080
    languages = app.available_languages()
    current = str(settings["language"])
    if current not in languages:
        languages.append(current)
    language = _model(window, OPT_LANGUAGE)
    language.StringItemList = tuple(languages)
    language.SelectedItems = (languages.index(current),)


def _save_options_page(app: App, window: Any) -> None:
    language = _model(window, OPT_LANGUAGE)
    items = list(language.StringItemList)
    selected = list(language.SelectedItems)
    changes = {
        "enabled": _model(window, OPT_ENABLED).State == 1,
        "debug": _model(window, OPT_DEBUG).State == 1,
        "min_prefix": int(round(_model(window, OPT_MIN_PREFIX).Value)),
        "min_completion": int(round(_model(window, OPT_MIN_COMPLETION).Value)),
        "max_suggestions": int(round(_model(window, OPT_MAX_SUGGESTIONS).Value)),
        "max_candidates": int(round(_model(window, OPT_MAX_CANDIDATES).Value)),
        "inline": _model(window, OPT_INLINE).State == 1,
    }
    position = list(_model(window, OPT_LIST_POSITION).SelectedItems)
    if position and 0 <= position[0] < len(POSITIONS):
        changes["list_position"] = POSITIONS[position[0]]
    changes["context_prediction"] = _model(window, OPT_CONTEXT).State == 1
    changes["next_word"] = _model(window, OPT_NEXT_WORD).State == 1
    for name, key in (
        (OPT_ACCEPT_COMPLETION, "accept_completion"),
        (OPT_ACCEPT_NEXT_WORD, "accept_next_word"),
    ):
        selected = list(_model(window, name).SelectedItems)
        if selected and 0 <= selected[0] < len(keymap.ACCEPT_KEY_CHOICES):
            changes[key] = keymap.ACCEPT_KEY_CHOICES[selected[0]]
    if selected and 0 <= selected[0] < len(items):
        changes["language"] = items[selected[0]]
    current = {key: app.settings[key] for key in changes}
    if changes != current:
        app.update_settings(**changes)
