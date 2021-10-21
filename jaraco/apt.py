import re
import os
import argparse
import shlex
import subprocess
import logging
import contextlib
import itertools

import yg.lockfile


class PackageName(str):
    """A package name possibly with other attributes"""

    @classmethod
    def from_apt(cls, name):
        automatic = name.endswith('{a}')
        if automatic:
            name = name[:-3]
        res = cls(name)
        res.automatic = automatic
        return res


def parse_new_packages(apt_output, include_automatic=False):
    """
    Given the output from an apt or aptitude command, determine which packages
    are newly-installed.
    """
    pat = r'^The following NEW packages will be installed:[\r\n]+(.*?)[\r\n]\w'
    matcher = re.search(pat, apt_output, re.DOTALL | re.MULTILINE)
    if not matcher:
        return []
    new_pkg_text = matcher.group(1)
    raw_names = re.findall(r'[\w{}\.+-]+', new_pkg_text)
    all_packages = list(map(PackageName.from_apt, raw_names))
    manual_packages = [pack for pack in all_packages if not pack.automatic]
    return all_packages if include_automatic else manual_packages


@contextlib.contextmanager
def dependency_context(package_names, aggressively_remove=False):
    """
    Install the supplied packages and yield. Finally, remove all packages
    that were installed.
    Currently assumes 'aptitude' is available.
    """
    installed_packages = []
    log = logging.getLogger(__name__)
    try:
        if not package_names:
            logging.debug('No packages requested')
        if package_names:
            lock = yg.lockfile.FileLock('/tmp/.pkg-context-lock', timeout=30 * 60)
            log.info('Acquiring lock to perform install')
            lock.acquire()
            log.info('Installing ' + ', '.join(package_names))
            output = subprocess.check_output(
                ['sudo', 'aptitude', 'install', '-y'] + package_names,
                stderr=subprocess.STDOUT,
            )
            log.debug('Aptitude output:\n%s', output)
            installed_packages = parse_new_packages(
                output, include_automatic=aggressively_remove
            )
            if not installed_packages:
                lock.release()
            log.info('Installed ' + ', '.join(installed_packages))
        yield installed_packages
    except subprocess.CalledProcessError:
        log.error("Error occurred installing packages")
        raise
    finally:
        if installed_packages:
            log.info('Removing ' + ','.join(installed_packages))
            subprocess.check_call(
                ['sudo', 'aptitude', 'remove', '-y'] + installed_packages,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            lock.release()


def _parse_args():
    """
    >>> mp = getfixture('monkeypatch')
    >>> mp.setattr('sys.argv', ['foo.py', "python -c pass"])
    >>> ns = _parse_args()
    >>> ns.command
    ['python', '-c', 'pass']
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--deps-def',
        default=list(
            itertools.chain(
                data_lines_from_file("system deps.txt"),
                data_lines_from_file("build deps.txt"),
            )
        ),
        help="A file specifying the dependencies (one per line)",
        type=data_lines_from_file,
        dest="spec_deps",
    )
    parser.add_argument(
        '--dep',
        action="append",
        default=[],
        help="A specific dependency (multiple allowed)",
        dest="deps",
    )
    parser.add_argument(
        'command',
        type=shlex.split,
        default=shlex.split("python2.7 setup.py test"),
        help="Command to invoke in the context of the dependencies",
    )
    parser.add_argument(
        '--do-not-remove',
        default=False,
        action="store_true",
        help="Keep any installed packages",
    )
    parser.add_argument(
        '--aggressively-remove',
        default=False,
        action="store_true",
        help="When removing packages, also remove those automatically installed"
        " as dependencies",
    )
    parser.add_argument(
        '-l',
        '--log-level',
        default=logging.INFO,
        type=log_level,
        help="Set log level (DEBUG, INFO, WARNING, ERROR)",
    )
    return parser.parse_args()


def run():
    """
    Run a command in the context of the system dependencies.
    """
    args = _parse_args()
    logging.basicConfig(level=args.log_level)
    context = dependency_context(
        args.spec_deps + args.deps, aggressively_remove=args.aggressively_remove
    )
    with context as to_remove:
        if args.do_not_remove:
            del to_remove[:]
        raise SystemExit(subprocess.Popen(args.command).wait())


def log_level(level_string):
    """
    Return a log level for a string
    """
    return getattr(logging, level_string.upper())


def data_lines_from_file(filename):
    return filter(None, strip_comments(file_lines_if_exists(filename)))


def file_lines_if_exists(filename):
    """
    Return the lines from a file as a list if the file exists, or an
    empty list otherwise.

    >>> file_lines_if_exists('/doesnotexist.txt')
    []
    >>> file_lines_if_exists('setup.py')
    [...]
    """
    if not os.path.isfile(filename):
        return []
    return list(open(filename))


def strip_comments(lines):
    """
    Returns the lines from a list of a lines with comments and trailing
    whitespace removed.

    >>> strip_comments(['abc', '  ', '# def', 'egh '])
    ['abc', '', '', 'egh']

    It should not remove leading whitespace
    >>> strip_comments(['  bar # baz'])
    ['  bar']

    It should also strip trailing comments.
    >>> strip_comments(['abc #foo'])
    ['abc']
    """
    return [line.partition('#')[0].rstrip() for line in lines]
