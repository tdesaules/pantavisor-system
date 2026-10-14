"""Read newc entries, including concatenated archives; never extract paths."""
import posixpath
import stat


def resolve_member(entries, name):
    """Resolve archive symlinks without following anything on the host filesystem."""
    name = name.lstrip('/')
    for _ in range(40):
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            if prefix in entries and stat.S_ISLNK(entries[prefix][0]):
                target = entries[prefix][1].decode()
                target = target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(prefix), target)
                name = posixpath.normpath(posixpath.join(target, *parts[i:]))
                break
        else:
            if name not in entries:
                raise FileNotFoundError(name)
            return name
    raise ValueError(f'Symlink loop: {name}')


def newc_members(data):
    offset = 0
    while offset < len(data):
        # Padding after a trailer is zero-filled to an archive block boundary.
        if data[offset] == 0:
            offset += 1
            continue
        if offset + 110 > len(data):
            raise ValueError('Truncated newc header')
        header = data[offset:offset + 110]
        if header[:6] != b'070701':
            raise ValueError(f'Unsupported newc header at {offset}')
        fields = [int(header[i:i+8], 16) for i in range(6, 110, 8)]
        size, namesize = fields[6], fields[11]
        start = offset + 110
        if namesize < 1 or start + namesize > len(data) or data[start + namesize - 1] != 0:
            raise ValueError('Invalid newc name')
        name = data[start:start + namesize - 1].decode()
        start = (start + namesize + 3) & ~3
        if start + size > len(data):
            raise ValueError('Truncated newc payload')
        content = data[start:start + size]
        offset = (start + size + 3) & ~3
        if name != 'TRAILER!!!':
            yield name.removeprefix('./'), fields[1], content
