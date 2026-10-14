"""Safe local revision identifiers shared by host tooling and generated GRUB."""
import re

# Pantavisor limits the suffix after locals/ to 64 characters. Start with an
# alphanumeric character to exclude dot directories and option-like names.
# This subset remains safe unquoted in the generated kernel command line.
LOCAL_REVISION_PATTERN = r'locals/[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}'
BOOT_REVISION_PATTERN = rf'(0|{LOCAL_REVISION_PATTERN})'


def valid_revision(value, *, allow_factory=True):
    pattern = BOOT_REVISION_PATTERN if allow_factory else LOCAL_REVISION_PATTERN
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None
