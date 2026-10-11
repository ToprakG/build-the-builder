#!/bin/bash
# The self-test runner calls its "worker python" with the vendor worker script and its arguments; route that call
# through worker_patch.py so the customer model is ours. LOCAL_RUN_PYTHON picks the interpreter.
exec "${LOCAL_RUN_PYTHON:-python3}" "$(dirname "$0")/worker_patch.py" "$@"
