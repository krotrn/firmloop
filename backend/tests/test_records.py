"""The shared record models import nothing heavy, so the database layer can use them."""

import subprocess
import sys


def test_records_do_not_pull_in_the_agent_or_the_checker() -> None:
    code = (
        "import sys, firmloop.records;"
        "loaded = [m for m in sys.modules if m.startswith('firmloop')];"
        "print(sorted(loaded));"
        "assert not any(m.startswith(('firmloop.agent', 'firmloop.safety', 'firmloop.db',"
        " 'firmloop.clients')) for m in loaded)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
