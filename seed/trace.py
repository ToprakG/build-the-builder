"""Plain-text trace of one builder run.

The file has a start time, every model call, the Freya ids, the self-tests, and an end time.
"""
import os
from datetime import datetime

def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class Trace:
    def __init__(self, path, brief):
        self.path = path
        self.models = 0
        self._models_closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w")
        self.line(f"Brief: {brief}")
        self.line(f"Start: {stamp()}")
        self.line("")
        self.line("Model calls:")
        os.environ["BUILDER_TRACE"] = str(path)

    def line(self, text=""):
        self._file.write(text + "\n")
        self._file.flush()

    def model(self, model, purpose):
        self.models += 1
        self.line(f"- {model}: {purpose}")

    def _close_models(self):
        if self._models_closed:
            return
        self._models_closed = True
        if self.models == 0:
            self.line("- none. This build compiles the policy into the workflow and does not call a model.")

    def section(self, title):
        self._close_models()
        self.line("")
        self.line(f"{title}:")

    def finish(self):
        self._close_models()
        self.line("")
        self.line(f"End: {stamp()}")
        self._file.close()
        os.environ.pop("BUILDER_TRACE", None)

def note_model(model, purpose):
    """Append one model call to the open trace. No effect when no build is running."""
    path = os.environ.get("BUILDER_TRACE")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(f"- {model}: {purpose}\n")
