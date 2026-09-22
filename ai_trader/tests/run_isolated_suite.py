"""Run offline Python tests with data manifests and explicit side-effect guards.

Run: python -B tests/run_isolated_suite.py
Evidence is written to a new OS temporary directory, never to data/.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
from pathlib import Path
import py_compile
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'


def manifest():
    return [dict(name=p.relative_to(DATA).as_posix(), size=p.stat().st_size,
                 sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(DATA.rglob('*')) if p.is_file()]


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8')


def audit_guard(event, args):
    if event == 'sqlite3.connect':
        name = os.fsdecode(args[0])
        if name == ':memory:':
            return
        if name.startswith('file:'):
            name = unquote(urlparse(name).path)
            if os.name == 'nt' and len(name) > 2 and name[0] == '/' and name[2] == ':':
                name = name[1:]
        if Path(name).resolve().is_relative_to(ROOT):
            raise RuntimeError('TEST_SQLITE_IN_REPOSITORY_BLOCKED')
    elif event == 'open':
        path, mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if writing and Path(os.fsdecode(path)).resolve().is_relative_to(DATA):
                raise RuntimeError('TEST_DATA_WRITE_BLOCKED')
    elif event in ('socket.connect', 'socket.getaddrinfo'):
        address = args[1] if event == 'socket.connect' else args[0]
        host = address[0] if isinstance(address, tuple) else address
        if host not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('TEST_EXTERNAL_NETWORK_BLOCKED')


def main():
    sys.dont_write_bytecode = True
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    evidence = Path(tempfile.mkdtemp(prefix='nog-test-audit-'))
    before = manifest()
    (evidence / 'data-before.json').write_bytes(encoded(before))
    print('EVIDENCE', evidence, flush=True)
    result = None
    checks_ok = False
    try:
        # Compile to temporary outputs, avoiding __pycache__ changes in the repo.
        sources = sorted(ROOT.rglob('*.py'))
        with tempfile.TemporaryDirectory(prefix='nog-compile-') as tmp:
            for i, source in enumerate(sources):
                ast.parse(source.read_text(encoding='utf-8-sig'), filename=str(source))
                py_compile.compile(str(source), cfile=str(Path(tmp) / f'{i}.pyc'), doraise=True)
        print('AST_AND_PY_COMPILE_PASS', len(sources), flush=True)
        import openai
        import MetaTrader5 as mt5
        # The hook also catches aliases of sqlite3.connect and socket.connect.
        # This runner is a dedicated process; its audit hook ends with the process.
        sys.addaudithook(audit_guard)
        with contextlib.ExitStack() as stack:
            for name in ('OpenAI', 'AsyncOpenAI'):
                stack.enter_context(patch.object(openai, name, side_effect=RuntimeError('TEST_OPENAI_BLOCKED')))
            for name in ('initialize', 'login', 'order_send', 'order_check'):
                stack.enter_context(patch.object(mt5, name, side_effect=RuntimeError('TEST_MT5_BLOCKED')))
            with (evidence / 'unittest.log').open('w', encoding='utf-8') as log:
                suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            print((evidence / 'unittest.log').read_text(encoding='utf-8'), end='')
        checks_ok = result.wasSuccessful()
    finally:
        after = manifest()
        (evidence / 'data-after.json').write_bytes(encoded(after))
        identical = before == after
        summary = dict(files=len(before), bytes=sum(row['size'] for row in before),
                       before_sha256=hashlib.sha256(encoded(before)).hexdigest(),
                       after_sha256=hashlib.sha256(encoded(after)).hexdigest(),
                       identical=identical, tests=result.testsRun if result else None,
                       failures=len(result.failures) if result else None,
                       errors=len(result.errors) if result else None,
                       skipped=len(result.skipped) if result else None)
        (evidence / 'summary.json').write_bytes(encoded(summary))
        print('SUMMARY', json.dumps(summary), flush=True)
    return 0 if checks_ok and identical else 1


if __name__ == '__main__':
    raise SystemExit(main())
