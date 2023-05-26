#!/usr/bin/env -S pytest -sv

import aiohttp
import asyncio
import importlib
import numpy as np
import os
import pathlib
import pytest
import requests
import subprocess
import test_module as tm
import textwrap
import tuber
import weakref

from requests.packages.urllib3.util.retry import Retry

TUBERD_PORT = 8080
TUBERD_HOSTNAME = f"localhost:{TUBERD_PORT}"
TUBERD_URI = f"http://{TUBERD_HOSTNAME}/tuber"


# REGISTRY DEFINITIONS
#
# Tuberd needs a registry to export. Since it's intimately connected with the
# test code, we place them into the same Python file. This also allows us to
# verify that network-exported Python code works the same when run locally.
class NullObject:
    pass


class ObjectWithMethod:
    def method(self):
        return "expected return value"


class ObjectWithProperty:
    PROPERTY = "expected property value"


class Types:
    # These properties can be accessed as properties, directly
    STRING = "this is a string property"
    INTEGER = 1234
    FLOAT = 0.1234
    LIST = [1, 2, 3, 4]
    DICT = {"1": "2", "3": "4"}

    # Properties are also exposed as default arguments to functions
    def string_function(self, arg=STRING):
        assert isinstance(arg, str)
        return arg

    def integer_function(self, arg=INTEGER):
        assert isinstance(arg, int)
        return arg

    def float_function(self, arg=FLOAT):
        assert isinstance(arg, float)
        return arg

    def list_function(self, arg=LIST):
        assert isinstance(arg, list)
        return arg

    def dict_function(self, arg=DICT):
        assert isinstance(arg, dict)
        return arg


class NumPy:
    def returns_numpy_array(self):
        return np.array([0, 1, 2, 3])


registry = {
    "NullObject": NullObject(),
    "ObjectWithMethod": ObjectWithMethod(),
    "ObjectWithProperty": ObjectWithProperty(),
    "Types": Types(),
    "NumPy": NumPy(),
    "Wrapper": tm.Wrapper(),
}


@pytest.fixture(scope="session")
def tuberd(pytestconfig):
    """Spawn (and kill) a tuberd"""

    bin_dir = pathlib.Path(os.environ.get("CMAKE_BINARY_DIR", "."))
    src_dir = pathlib.Path(os.environ.get("CMAKE_SOURCE_DIR", "."))

    tuberd = bin_dir / "tuberd"
    preamble = src_dir / "py/preamble.py"
    registry = src_dir / "tests/test.py"

    argv = [
        f"{tuberd}",
        f"-p{TUBERD_PORT}",
        f"--preamble={preamble}",
        f"--registry={registry}",
    ]

    argv.extend(pytestconfig.getoption("tuberd_option"))

    if pytestconfig.getoption("orjson_with_numpy"):
        # If we can't import orjson here, it's presumably missing from the
        # tuberd execution environment as well - in which case, we should skip
        # the test.
        pytest.importorskip("orjson")
        argv.append("--orjson-with-numpy")

    s = subprocess.Popen(argv)
    yield s
    s.terminate()


#
# Sanity Checks - Python -> JSON -> C++ -> Python and back again
#


# This fixture provides a much simpler, synchronous wrapper for functionality
# normally provided by tuber.py.  It's coded directly - which makes it less
# flexible, less performant, and easier to understand here.
@pytest.fixture(scope="session")
def tuber_call(tuberd):
    # Although the tuberd argument is not used here, it creates a dependency on
    # the daemon so it's launched and terminated.

    # The tuber daemon can take a little while to start (in particular, it
    # sources this script as a registry) - rather than adding a magic sleep to
    # the subprocess command, we teach the client interface to wait patiently.
    adapter = requests.adapters.HTTPAdapter(max_retries=Retry(total=10, backoff_factor=1))
    session = requests.Session()
    session.mount(TUBERD_URI, adapter)

    def tuber_call(json=None, **kwargs):
        # The most explicit call style passes POST content via an explicit
        # "json" parameter.  However, for convenience's sake, we also allow
        # kwargs to supply a dict parameter since we often call with dicts and
        # this results in a more readable code style.
        return session.post(
            TUBERD_URI,
            json=kwargs if json is None else json,
        ).json()

    yield tuber_call


def Succeeded(args=None, **kwargs):
    """Wrap a return value for a successful call in its JSON-RPC wrapper"""
    return dict(result=kwargs or args)


def Failed(**kwargs):
    """Wrap a return value for an error in its JSON-RPC wrapper"""
    return dict(error=kwargs)


def test_empty_request_array(tuber_call):
    assert tuber_call(json=[]) == []


def test_fetch_null_metadata(tuber_call):
    assert tuber_call(object="NullObject") == Succeeded(__doc__=None, methods=[], properties=[])


def test_call_nonexistent_object(tuber_call):
    assert tuber_call(object="NothingHere") == Failed(
        message="Request for an object (NothingHere) that wasn't in the registry!"
    )


def test_call_nonexistent_method(tuber_call):
    assert tuber_call(object="NullObject", method="does_not_exist") == Failed(
        message="AttributeError: 'NullObject' object has no attribute 'does_not_exist'"
    )


def test_property_types(tuber_call):
    assert tuber_call(object="Types", property="STRING") == Succeeded(Types.STRING)
    assert tuber_call(object="Types", property="INTEGER") == Succeeded(Types.INTEGER)
    assert tuber_call(object="Types", property="FLOAT") == Succeeded(pytest.approx(Types.FLOAT))
    assert tuber_call(object="Types", property="LIST") == Succeeded(Types.LIST)
    assert tuber_call(object="Types", property="DICT") == Succeeded(Types.DICT)


def test_function_types_with_default_arguments(tuber_call):
    assert tuber_call(object="Types", method="string_function") == Succeeded(Types.STRING)
    assert tuber_call(object="Types", method="integer_function") == Succeeded(Types.INTEGER)
    assert tuber_call(object="Types", method="float_function") == Succeeded(pytest.approx(Types.FLOAT))
    assert tuber_call(object="Types", method="list_function") == Succeeded(Types.LIST)
    assert tuber_call(object="Types", method="dict_function") == Succeeded(Types.DICT)


def test_function_types_with_correct_argument_types(tuber_call):
    assert tuber_call(object="Types", method="string_function", args=["this is a string"]) == Succeeded(
        "this is a string"
    )
    assert tuber_call(object="Types", method="integer_function", args=[6789]) == Succeeded(6789)
    assert tuber_call(object="Types", method="float_function", args=[67.89]) == Succeeded(pytest.approx(67.89))
    assert tuber_call(object="Types", method="list_function", args=[[3, 4, 5, 6]]) == Succeeded([3, 4, 5, 6])
    assert tuber_call(object="Types", method="dict_function", args=[dict(one="two", three="four")]) == Succeeded(
        one="two", three="four"
    )


#
# orjson / numpy fastpath tests
#


@pytest.mark.orjson
def test_numpy_types(tuber_call):
    assert tuber_call(object="NumPy", method="returns_numpy_array") == Succeeded([0, 1, 2, 3])


#
# pybind11 wrappers
#


@pytest.mark.orjson
def test_double_vector(tuber_call):
    assert tuber_call(object="Wrapper", method="increment", args=[[1, 2, 3, 4, 5]]) == Succeeded([2, 3, 4, 5, 6])


#
# pybind11 strenum tests. These tests are direct library imports and do not
# exercise tuberd.
#


def test_cpp_enum_direct_instantiation():
    # Directly instantiate enums
    x = tm.Kind("X")
    y = tm.Kind("Y")
    assert x != y

    # Compare two instiantiations
    assert x == tm.Kind("X")
    assert y == tm.Kind("Y")


def test_cpp_enum_cpp_to_py():
    w = tm.Wrapper()
    x = w.return_x()
    y = w.return_y()

    assert x == tm.Kind("X")
    assert y == tm.Kind("Y")


def test_cpp_enum_py_to_cpp_types():
    w = tm.Wrapper()
    x = tm.Kind("X")
    y = tm.Kind("Y")

    assert w.is_x(x)
    assert w.is_y(y)
    assert not w.is_x(y)


def test_cpp_enum_py_to_cpp_strings():
    w = tm.Wrapper()

    assert w.is_x("X")
    assert w.is_y("Y")
    assert not w.is_x("Y")


@pytest.mark.skip(reason="Semantics are unclear")
def test_cpp_enum_py_to_py():
    x = tm.Kind("X")
    y = tm.Kind("Y")

    assert x == "X"
    assert y == "Y"
    assert y != "X"


@pytest.mark.orjson
def test_cpp_enum_orjson_serialize():
    orjson = pytest.importorskip("orjson")

    x = tm.Kind("X")
    y = tm.Kind("Y")

    assert orjson.dumps(x) == b'"X"'
    assert orjson.dumps(y) == b'"Y"'


#
# tuber.py tests
#


@pytest.mark.asyncio
async def test_tuberpy_hello(tuber_call):
    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    x = await s.increment([1, 2, 3, 4, 5])
    assert x == [2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_tuberpy_dir(tuber_call):
    """Ensure embedded methods end up in dir() of objects.

    This is a crude proxy for the ability to tab-complete."""
    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    assert "increment" in dir(s)


@pytest.mark.asyncio
async def test_tuberpy_module_docstrings(tuber_call):
    """Ensure docstrings in C++ methods end up in the TuberObject's __doc__ dunder."""

    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    assert (
        s.__doc__
        == textwrap.dedent(
            """
        This is the object DocString, defined in C++."""
        ).lstrip()
    )


@pytest.mark.asyncio
async def test_tuberpy_method_docstrings(tuber_call):
    """Ensure docstrings in C++ methods end up in the TuberObject's __doc__ dunder."""

    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    assert (
        s.increment.__doc__
        == textwrap.dedent(
            """
        increment(self: test_module.Wrapper, x: List[int]) -> List[int]

        A function that increments each element in its argument list."""
        ).lstrip()
    )


@pytest.mark.asyncio
async def test_tuberpy_session_cache(tuber_call):
    """Ensure we don't create a new ClientSession with every call."""
    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    await s.increment([1, 2, 3])
    aiohttp.ClientSession = None  # break ClientSession instantiation
    await s.increment([4, 5, 6])
    importlib.reload(aiohttp)
    assert aiohttp.ClientSession  # make sure we fixed it


@pytest.mark.asyncio
async def test_tuberpy_async_context(tuber_call):
    """Ensure we can use tuber_contexts to batch calls."""
    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    async with s.tuber_context() as ctx:
        r1 = ctx.increment([1, 2, 3])
        r2 = ctx.increment([2, 3, 4])

    r1, r2 = await asyncio.gather(r1, r2)
    assert r1 == [2, 3, 4]
    assert r2 == [3, 4, 5]


@pytest.mark.asyncio
async def test_tuberpy_async_context_with_kwargs(tuber_call):
    """Ensure we can use tuber_contexts to batch calls."""
    s = await tuber.resolve("Wrapper", TUBERD_HOSTNAME)
    async with s.tuber_context(x=[1, 2, 3]) as ctx:
        r1 = ctx.increment()
        r2 = ctx.increment()

    r1, r2 = await asyncio.gather(r1, r2)
    assert r1 == [2, 3, 4]
    assert r2 == [2, 3, 4]
