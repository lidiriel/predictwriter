"""UNO component registration for Predictive Writer.

LibreOffice loads this file as a Python UNO component (see
META-INF/manifest.xml) and adds ``python/pythonpath`` to ``sys.path``.

* ``PredictiveJob`` runs at application start (Jobs.xcu, event OnStartApp).
* ``OptionsHandler`` backs Tools → Options → Languages and Locales →
  Predictive Text (OptionsDialog.xcu + dialogs/OptionsPage.xdl).
"""

import logging
from typing import Any

import unohelper
from com.sun.star.awt import XContainerWindowEventHandler
from com.sun.star.task import XJob

JOB_IMPLEMENTATION = "net.scilink.predictivewriter.Job"
OPTIONS_IMPLEMENTATION = "net.scilink.predictivewriter.OptionsHandler"


class PredictiveJob(unohelper.Base, XJob):
    """Startup job: installs the document tracker and key handlers."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def execute(self, arguments: Any) -> None:
        """Called by the job framework at OnStartApp."""
        try:
            # Imported lazily so that an error in our package cannot prevent
            # the component from being registered.
            from predictive import uno_glue

            uno_glue.start(self.ctx)
        except Exception:
            logging.getLogger("predictive").exception("startup failed")
        return None


class OptionsHandler(unohelper.Base, XContainerWindowEventHandler):
    """Event handler of the Options page.

    LibreOffice calls ``callHandlerMethod(window, event, "external_event")``
    with ``event`` = ``"initialize"``, ``"ok"`` or ``"back"``.
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def callHandlerMethod(self, window: Any, event: Any, method: str) -> bool:
        if method != "external_event":
            return False
        try:
            from predictive import uno_glue

            return uno_glue.options_page_event(self.ctx, window, str(event))
        except Exception:
            logging.getLogger("predictive").exception("options page failed")
            return False

    def getSupportedMethodNames(self) -> tuple:
        return ("external_event",)


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    PredictiveJob, JOB_IMPLEMENTATION, (JOB_IMPLEMENTATION, "com.sun.star.task.Job")
)
g_ImplementationHelper.addImplementation(
    OptionsHandler, OPTIONS_IMPLEMENTATION, (OPTIONS_IMPLEMENTATION,)
)
