"""Glob-Muster (Bash/fnmatch) und Hilfen."""
import re

KLASSEN = {
    "alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
    "space": r" \t\n\r\f\v", "blank": r" \t", "punct": r"!-/:-@\[-`{-~", "xdigit": "0-9A-Fa-f",
    "print": r" -~", "graph": r"!-~", "cntrl": r"\x00-\x1f\x7f", "word": r"\w",
}


def zu_regex(muster, quoted=None, ganz=True):
    """Bash-Muster -> Regex-String. quoted: Menge der Indizes, die woertlich sind."""
    quoted = quoted or set()
    out, i, n = [], 0, len(muster)
    while i < n:
        c = muster[i]
        if i in quoted:
            out.append(re.escape(c))
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(re.escape(muster[i + 1]))
            i += 2
            continue
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = i + 1
            if j < n and muster[j] in "!^":
                j += 1
            if j < n and muster[j] == "]":
                j += 1
            while j < n and muster[j] != "]":
                if muster[j] == "[" and j + 1 < n and muster[j + 1] == ":":
                    k = muster.find(":]", j + 2)
                    j = k + 2 if k >= 0 else j + 1
                    continue
                j += 1
            if j >= n:
                out.append(r"\[")
            else:
                inhalt = muster[i + 1:j]
                neg = inhalt[:1] in ("!", "^")
                if neg:
                    inhalt = inhalt[1:]
                teile, k = [], 0
                while k < len(inhalt):
                    if inhalt.startswith("[:", k):
                        e = inhalt.find(":]", k)
                        if e > 0:
                            teile.append(KLASSEN.get(inhalt[k + 2:e], ""))
                            k = e + 2
                            continue
                    ch = inhalt[k]
                    if ch == "\\" and k + 1 < len(inhalt):
                        teile.append(re.escape(inhalt[k + 1]))
                        k += 2
                        continue
                    if ch in "\\^]":
                        teile.append("\\" + ch)
                    elif ch == "-":
                        teile.append("-")
                    else:
                        teile.append(ch)
                    k += 1
                out.append("[" + ("^" if neg else "") + "".join(teile) + "]")
                i = j + 1
                continue
        else:
            out.append(re.escape(c))
        i += 1
    r = "".join(out)
    return "(?s:" + r + ")\\Z" if ganz else r


def passt(muster, text, quoted=None):
    try:
        return re.match(zu_regex(muster, quoted), text) is not None
    except re.error:
        return muster == text


def hat_glob(text, quoted=None):
    quoted = quoted or set()
    i = 0
    while i < len(text):
        if i in quoted:
            i += 1
            continue
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "*?":
            return True
        if c == "[" and "]" in text[i + 1:]:
            return True
        i += 1
    return False
