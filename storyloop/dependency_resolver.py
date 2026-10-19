"""Offline wheel metadata resolver: pure py3-none-any only, never import/install/extract.

Configured argv uses this exact engine script with -I, --wheel, --cache-dir and --output.
The engine copies this trusted script and the committed wheel into a temporary tree.
"""
import argparse
from email.parser import Parser
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

MAX_WHEEL = 8 * 1024 * 1024
MAX_METADATA = 64 * 1024


def _metadata(archive: zipfile.ZipFile, name: str):
    info = archive.getinfo(name)
    if info.file_size > MAX_METADATA:
        raise ValueError('Wheel metadata too large')
    return Parser().parsestr(archive.read(info).decode('utf-8'))


def resolve(wheel: Path) -> dict:
    """Prove only bounded, local metadata resolvability; unsupported requirements fail closed."""
    if wheel.stat().st_size > MAX_WHEEL:
        raise ValueError('Wheel too large')
    match = re.fullmatch(r'([A-Za-z0-9_]+)-([0-9]+(?:\.[0-9]+)*)-py3-none-any\.whl', wheel.name)
    if not match:
        raise ValueError('Only exact numeric-version py3-none-any wheels are supported')
    distribution, version = match.groups()
    prefix = f'{distribution}-{version}.dist-info/'
    with zipfile.ZipFile(wheel) as archive:
        _check_members(archive)
        metadata = _metadata(archive, prefix + 'METADATA')
        description = _metadata(archive, prefix + 'WHEEL')
    if (metadata.get_all('Requires-Dist') or metadata.get_all('Requires-Python')
            or metadata.get_all('Dynamic')):
        raise ValueError('Dependency, Python-version or dynamic requirements need a supported resolver')
    if (metadata.get_all('Version') != [version] or len(metadata.get_all('Name', [])) != 1
            or re.sub(r'[-_.]+', '_', metadata['Name']).lower() != distribution.lower()):
        raise ValueError('Wheel identity differs from metadata')
    if (description.get_all('Tag') != ['py3-none-any']
            or description.get_all('Root-Is-Purelib') != ['true']
            or description.get_all('Wheel-Version') != ['1.0']):
        raise ValueError('Unsupported wheel compatibility')
    return {'ecosystem': 'python', 'name': metadata['Name'], 'version': version}


def _check_members(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    names = [item.filename for item in members]
    if len(members) > 2000 or len(names) != len(set(names)):
        raise ValueError('Duplicate or excessive wheel members')
    if sum(item.file_size for item in members) > 32 * 1024 * 1024:
        raise ValueError('Wheel expands beyond limit')
    for item in members:
        name = item.filename
        if (not name or '\\' in name or ':' in name or name.startswith('/')
                or '..' in PurePosixPath(name).parts or any(ord(c) < 32 for c in name)
                or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1):
            raise ValueError('Unsafe wheel member')


def main() -> int:
    """Emit the engine evidence protocol without installing or executing package code."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel', required=True)
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = {'resolved': [resolve(Path(args.wheel))]}
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(result, sort_keys=True)
        output.write_text(rendered + '\n', encoding='utf-8')
        print(rendered)
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        print('Unsupported or invalid offline wheel; provide verified dependency evidence')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
