from . import base, coreutils, text  # noqa: F401
try:
    from . import sysprogs, archiv, screen, awk  # noqa: F401
except ImportError as e:  # waehrend der Entwicklung
    import sys
    print("emu: progs unvollstaendig:", e, file=sys.stderr)
