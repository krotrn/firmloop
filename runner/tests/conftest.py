"""Run `async def` tests with asyncio.run (pytest-asyncio is not a runner dependency)."""

import asyncio
import inspect
from typing import Any

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> Any:
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    args = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(pyfuncitem.obj(**args))
    return True
