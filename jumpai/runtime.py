"""Detect a usable C++ toolchain and build native programs in temporary folders."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .schema import JumpError, validate


# Persist only host capability and preference, never shell commands or flags.
DEFAULT_EXECUTION = {'preferred_runtime': 'python', 'cpp_compiler': '', 'cpp_style': 'gnu'}
EXECUTION_SCHEMA = {
    'type': 'object',
    'required': ['preferred_runtime', 'cpp_compiler', 'cpp_style'],
    'additionalProperties': False,
    'properties': {
        'preferred_runtime': {'type': 'string', 'enum': ['python', 'cpp']},
        'cpp_compiler': {'type': 'string'},
        'cpp_style': {'type': 'string', 'enum': ['gnu', 'msvc']},
    },
}


def check_execution(settings):
    """Validate host settings; reject relative compiler paths or unbacked C++ selection."""
    validate(settings, EXECUTION_SCHEMA, 'execution')
    compiler = settings['cpp_compiler']
    if compiler and ('\x00' in compiler or not Path(compiler).is_absolute()):
        raise JumpError('C++ compiler must be an absolute executable path')
    if settings['preferred_runtime'] == 'cpp' and not compiler:
        raise JumpError('C++ selection requires a verified compiler; reinstall to detect one')
    return settings


def compile_cpp(settings, source, directory, *, dependencies=False):
    """Compile one C++17 entrypoint; return its binary or raise an actionable error.

    Build files stay in the supplied disposable directory. Compiler subprocesses
    have a 30-second limit and are reaped, including their POSIX process group.
    """
    from .runner import Runner

    check_execution(settings)
    compiler = settings['cpp_compiler']
    if not compiler:
        raise JumpError('No verified C++ compiler; reinstall to detect a toolchain')
    binary = Path(directory) / ('program.exe' if os.name == 'nt' else 'program')
    if settings['cpp_style'] == 'msvc':
        command = [compiler, '/nologo', '/std:c++17', '/EHsc', str(source), f'/Fe:{binary}']
    else:
        command = [compiler, '-std=c++17', '-O2', str(source), '-o', str(binary)]
    if dependencies:
        command += (['/sourceDependencies', str(Path(directory) / 'dependencies.json')]
                    if settings['cpp_style'] == 'msvc'
                    else ['-MD', '-MF', str(Path(directory) / 'dependencies.d')])

    # Keep compiler output off pipes and clean up descendants on every exit path.
    with tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(command, cwd=directory, stdin=subprocess.DEVNULL,
                                       stdout=errors, stderr=errors,
                                       start_new_session=(os.name == 'posix'))
        except OSError as exc:
            raise JumpError(f'Could not start C++ compiler: {exc}') from exc
        try:
            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:
                raise JumpError('C++ compilation exceeded 30 seconds') from exc
            if code != 0 or not binary.is_file():
                errors.seek(0)
                detail = errors.read(4096).decode('utf-8', errors='replace').strip()
                raise JumpError(f'C++ compilation failed (exit {code}): {detail}')
        finally:
            Runner._stop(process)
    return binary


def detect_execution():
    """Compile and run a standard-library probe; return settings plus failed attempts.

    Try CXX (a single executable path), c++, g++, clang++, then MSVC cl.
    Missing, broken, or nonnative toolchains fall back to Python without failing
    installation. Temporary files are removed; probe failures remain visible.
    """
    failures = []
    seen = set()
    candidates = [os.environ.get('CXX'), 'c++', 'g++', 'clang++', 'cl']
    with tempfile.TemporaryDirectory(prefix='jumpai-cpp-probe-') as temp:
        source = Path(temp) / 'probe.cpp'
        source.write_text('// Verify native linking and standard-library output.\n'
                          '#include <iostream>\n'
                          '// Emit the expected marker for the installation probe.\n'
                          'int main() { std::cout << "jumpai-cpp-ok"; }\n')
        for candidate in candidates:
            if not candidate:
                continue
            compiler = shutil.which(candidate)
            if not compiler or compiler in seen:
                continue
            seen.add(compiler)
            settings = {'preferred_runtime': 'cpp', 'cpp_compiler': str(Path(compiler).absolute()),
                        'cpp_style': 'msvc' if Path(compiler).stem.lower() == 'cl' else 'gnu'}
            try:
                binary = compile_cpp(settings, source, temp)
                probe = subprocess.run([str(binary)], capture_output=True, timeout=5, check=True)
                if probe.stdout != b'jumpai-cpp-ok':
                    raise JumpError('C++ probe returned unexpected output')
            except (JumpError, OSError, subprocess.SubprocessError) as exc:
                failures.append(f'{candidate}: {exc}')
                continue
            return settings, failures
    return dict(DEFAULT_EXECUTION), failures
