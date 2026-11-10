"""Lexer und Parser fuer Bash-Syntax (Teilmenge, aber mit Bash-Fehlermeldungen).

Erzeugt einen AST, der von shell.py ausgefuehrt wird. Woerter bleiben Rohtext und werden erst bei der
Ausfuehrung expandiert (wie in der Bash).
"""
import re


class Incomplete(Exception):
    """Eingabe ist noch nicht fertig (offenes Anfuehrungszeichen, fehlendes fi/done ...) -> PS2 zeigen."""


class SyntaxErr(Exception):
    def __init__(self, token):
        super().__init__(token)
        self.token = token

    def text(self):
        if self.token == "\n":
            return "syntax error near unexpected token `newline'"
        if self.token is None:
            return "syntax error: unexpected end of file"
        return f"syntax error near unexpected token `{self.token}'"


RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "case", "esac", "while", "until", "for", "in", "{", "}",
            "!", "[[", "]]", "function", "select", "time"}
OPS = ["&&", "||", ";;", ";&", ";;&", "|&", "<<<", "<<-", "<<", ">>", "&>>", "&>", "<&", ">&", "<>", ">|", ";", "&",
       "|", "<", ">", "(", ")", "\n"]
OPS.sort(key=len, reverse=True)
REDIR_OPS = {"<", ">", ">>", ">|", "<>", "<&", ">&", "&>", "&>>", "<<", "<<-", "<<<"}
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(\[[^\]]*\])?(\+?)=")


class Tok:
    __slots__ = ("typ", "val", "start", "end", "ionum")

    def __init__(self, typ, val, start, end, ionum=None):
        self.typ, self.val, self.start, self.end, self.ionum = typ, val, start, end, ionum

    def __repr__(self):
        return f"Tok({self.typ},{self.val!r})"


# ---------------------------------------------------------------------------- Lexer
class Lexer:
    def __init__(self, src, aliases=None):
        self.src = src
        self.pos = 0
        self.heredocs = []      # offene Here-Docs: Redir-Objekte, deren Text nach dem naechsten Zeilenende kommt
        self.aliases = aliases or {}

    def _skip_blank(self):
        s = self.src
        while self.pos < len(s):
            c = s[self.pos]
            if c in " \t":
                self.pos += 1
            elif c == "\\" and s[self.pos + 1:self.pos + 2] == "\n":
                self.pos += 2
            elif c == "#":
                while self.pos < len(s) and s[self.pos] != "\n":
                    self.pos += 1
            else:
                break

    def next(self):
        self._skip_blank()
        s = self.src
        if self.pos >= len(s):
            if self.heredocs:
                raise Incomplete()
            return Tok("EOF", None, self.pos, self.pos)
        start = self.pos
        if s[self.pos] in "<>" and s[self.pos + 1:self.pos + 2] == "(":
            ende = self._paren_ende(self.pos + 2)
            self.pos = ende
            rest = self._wort() if self.pos < len(s) and s[self.pos] not in " \t\n;&|<>()" else ""
            return Tok("WORD", s[start:ende] + rest, start, self.pos)
        # IO-Nummer vor Umleitung: 2>file
        m = re.match(r"\d+(?=[<>])", s[self.pos:])
        if m:
            nach = self.pos + m.end()
            for op in OPS:
                if s.startswith(op, nach) and op in REDIR_OPS:
                    self.pos = nach + len(op)
                    return Tok("OP", op, start, self.pos, int(m.group()))
        for op in OPS:
            if s.startswith(op, self.pos):
                self.pos += len(op)
                if op == "\n":
                    self._heredocs_lesen()
                return Tok("OP", op, start, self.pos)
        wort = self._wort()
        return Tok("WORD", wort, start, self.pos)

    def _wort(self):
        s, i = self.src, self.pos
        start = i
        while i < len(s):
            c = s[i]
            if c == "(" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", s[start:i]):
                i = self._paren_ende(i + 1)
                continue
            if c in " \t\n;&|<>()":
                break
            if c == "\\":
                if i + 1 >= len(s):
                    raise Incomplete()
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise Incomplete()
                i = j + 1
                continue
            if c == '"':
                i = self._dq_ende(i + 1)
                continue
            if c == "`":
                i = self._bq_ende(i + 1)
                continue
            if c == "$" and i + 1 < len(s):
                n = s[i + 1]
                if n == "(":
                    i = self._paren_ende(i + 2)
                    continue
                if n == "{":
                    i = self._brace_ende(i + 2)
                    continue
                if n == "'":
                    i = self._ansi_ende(i + 2)
                    continue
            i += 1
        self.pos = i
        return s[start:i]

    def _ansi_ende(self, i):
        s = self.src
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == "'":
                return i + 1
            i += 1
        raise Incomplete()

    def _dq_ende(self, i):
        s = self.src
        while i < len(s):
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                return i + 1
            if c == "`":
                i = self._bq_ende(i + 1)
                continue
            if c == "$" and i + 1 < len(s) and s[i + 1] == "(":
                i = self._paren_ende(i + 2)
                continue
            if c == "$" and i + 1 < len(s) and s[i + 1] == "{":
                i = self._brace_ende(i + 2)
                continue
            i += 1
        raise Incomplete()

    def _bq_ende(self, i):
        s = self.src
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == "`":
                return i + 1
            i += 1
        raise Incomplete()

    def _paren_ende(self, i):
        s, tiefe = self.src, 1
        while i < len(s):
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise Incomplete()
                i = j + 1
                continue
            if c == '"':
                i = self._dq_ende(i + 1)
                continue
            if c == "`":
                i = self._bq_ende(i + 1)
                continue
            if c == "(":
                tiefe += 1
            elif c == ")":
                tiefe -= 1
                if tiefe == 0:
                    return i + 1
            i += 1
        raise Incomplete()

    def _brace_ende(self, i):
        s, tiefe = self.src, 1
        while i < len(s):
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "'":
                j = s.find("'", i + 1)
                if j < 0:
                    raise Incomplete()
                i = j + 1
                continue
            if c == '"':
                i = self._dq_ende(i + 1)
                continue
            if c == "{":
                tiefe += 1
            elif c == "}":
                tiefe -= 1
                if tiefe == 0:
                    return i + 1
            i += 1
        raise Incomplete()

    def _heredocs_lesen(self):
        s = self.src
        while self.heredocs:
            hd = self.heredocs.pop(0)
            zeilen = []
            while True:
                if self.pos >= len(s):
                    self.heredocs.insert(0, hd)
                    raise Incomplete()
                j = s.find("\n", self.pos)
                if j < 0:
                    zeile, naechst = s[self.pos:], len(s)
                    letzte = True
                else:
                    zeile, naechst = s[self.pos:j], j + 1
                    letzte = False
                vergleich = zeile.lstrip("\t") if hd.op == "<<-" else zeile
                if vergleich == hd.delim:
                    self.pos = naechst
                    break
                if letzte:
                    self.heredocs.insert(0, hd)
                    raise Incomplete()
                zeilen.append(vergleich)
                self.pos = naechst
            hd.body = "".join(z + "\n" for z in zeilen)

    def ersetzen(self, start, end, text):
        """Alias-Expansion: Text an der Stelle einsetzen und dort weiterlesen."""
        self.src = self.src[:start] + text + self.src[end:]
        self.pos = start


# ---------------------------------------------------------------------------- AST
class Node:
    pass


class Redir(Node):
    def __init__(self, op, fd, ziel):
        self.op, self.fd, self.ziel = op, fd, ziel
        self.delim = None
        self.quoted = False
        self.body = None


class Simple(Node):
    def __init__(self):
        self.assigns, self.words, self.redirs = [], [], []
        self.lineno = 1


class Pipeline(Node):
    def __init__(self, cmds, negate=False, stderr_mit=None, timed=False):
        self.cmds, self.negate, self.timed = cmds, negate, timed
        self.stderr_mit = stderr_mit or [False] * len(cmds)


class AndOr(Node):
    def __init__(self, first):
        self.first, self.rest = first, []


class ListN(Node):
    def __init__(self):
        self.items = []  # (andor, background)


class If(Node):
    def __init__(self):
        self.zweige, self.sonst, self.redirs = [], None, []


class Loop(Node):
    def __init__(self, cond, body, until=False):
        self.cond, self.body, self.until, self.redirs = cond, body, until, []


class For(Node):
    def __init__(self, var, words, body):
        self.var, self.words, self.body, self.redirs = var, words, body, []


class ArithFor(Node):
    def __init__(self, init, cond, step, body):
        self.init, self.cond, self.step, self.body, self.redirs = init, cond, step, body, []


class Case(Node):
    def __init__(self, word):
        self.word, self.items, self.redirs = word, [], []


class Group(Node):
    def __init__(self, body, subshell=False):
        self.body, self.subshell, self.redirs = body, subshell, []


class FuncDef(Node):
    def __init__(self, name, body, src=""):
        self.name, self.body, self.src = name, body, src


class DblBracket(Node):
    def __init__(self, toks):
        self.toks, self.redirs = toks, []


class Arith(Node):
    def __init__(self, expr):
        self.expr, self.redirs = expr, []


# ---------------------------------------------------------------------------- Parser
class Parser:
    def __init__(self, src, aliases=None):
        self.lx = Lexer(src, aliases)
        self.aliases = aliases or {}
        self.tok = None
        self.alias_aktiv = set()
        self._next()

    def _next(self):
        self.tok = self.lx.next()
        return self.tok

    def _ist_op(self, *ops):
        return self.tok.typ == "OP" and self.tok.val in ops

    def _ist_wort(self, *w):
        return self.tok.typ == "WORD" and self.tok.val in w

    def _fehler(self):
        if self.tok.typ == "EOF":
            raise Incomplete()
        raise SyntaxErr(self.tok.val)

    def _newlines(self):
        while self._ist_op("\n"):
            self._next()

    def parse(self):
        self._newlines()
        lst = self.liste(top=True)
        if self.tok.typ != "EOF":
            raise SyntaxErr(self.tok.val)
        return lst

    STOP = {"then", "else", "elif", "fi", "do", "done", "esac", "}", "]]"}

    def liste(self, top=False, stop=()):
        lst = ListN()
        while True:
            self._newlines()
            if self.tok.typ == "EOF":
                break
            if self.tok.typ == "WORD" and self.tok.val in self.STOP:
                break
            if self._ist_op(")", ";;", ";&", ";;&"):
                break
            if self._ist_op(";", "&", "|", "||", "&&", "|&"):
                raise SyntaxErr(self.tok.val)
            start = self.tok.start
            ao = self.and_or()
            ao.src = self.lx.src[start:self.tok.start].strip()
            bg = False
            if self._ist_op("&"):
                bg = True
                self._next()
            elif self._ist_op(";"):
                self._next()
            elif self._ist_op("\n"):
                pass
            elif self.tok.typ == "EOF" or self.tok.typ == "WORD" and self.tok.val in self.STOP \
                    or self._ist_op(")", ";;", ";&", ";;&"):
                lst.items.append((ao, bg))
                break
            else:
                raise SyntaxErr(self.tok.val)
            lst.items.append((ao, bg))
        return lst

    def and_or(self):
        ao = AndOr(self.pipeline())
        while self._ist_op("&&", "||"):
            op = self.tok.val
            self._next()
            self._newlines()
            if self.tok.typ == "EOF":
                raise Incomplete()
            ao.rest.append((op, self.pipeline()))
        return ao

    def pipeline(self):
        negate = timed = False
        if self._ist_wort("time"):
            timed = True
            self._next()
        while self._ist_wort("!"):
            negate = not negate
            self._next()
        cmds, err = [self.command()], []
        while self._ist_op("|", "|&"):
            err.append(self.tok.val == "|&")
            self._next()
            self._newlines()
            if self.tok.typ == "EOF":
                raise Incomplete()
            cmds.append(self.command())
        err.append(False)
        return Pipeline(cmds, negate, err, timed)

    def command(self):
        t = self.tok
        if t.typ == "OP" and t.val == "(":
            src = self.lx.src
            if src.startswith("((", t.start):  # (( arith ))
                i, tiefe = t.start + 2, 0
                while i < len(src):
                    if src[i] == "(":
                        tiefe += 1
                    elif src[i] == ")":
                        if tiefe == 0 and src.startswith("))", i):
                            break
                        tiefe -= 1
                    i += 1
                else:
                    raise Incomplete()
                n = Arith(src[t.start + 2:i])
                self.lx.pos = i + 2
                self._next()
                self._redirs_nach(n)
                return n
            self._next()
            body = self.liste()
            if not self._ist_op(")"):
                self._fehler()
            self._next()
            g = Group(body, subshell=True)
            self._redirs_nach(g)
            return g
        if t.typ == "WORD":
            v = t.val
            if v.startswith("((") and v == "((":
                pass
            if v == "{":
                self._next()
                body = self.liste()
                if not self._ist_wort("}"):
                    self._fehler()
                self._next()
                g = Group(body)
                self._redirs_nach(g)
                return g
            if v == "if":
                return self._if()
            if v in ("while", "until"):
                self._next()
                cond = self.liste()
                if not self._ist_wort("do"):
                    self._fehler()
                self._next()
                body = self.liste()
                if not self._ist_wort("done"):
                    self._fehler()
                self._next()
                n = Loop(cond, body, until=v == "until")
                self._redirs_nach(n)
                return n
            if v == "for":
                return self._for()
            if v == "case":
                return self._case()
            if v == "[[":
                return self._dbl()
            if v == "function":
                self._next()
                if self.tok.typ != "WORD":
                    self._fehler()
                name = self.tok.val
                self._next()
                if self._ist_op("("):
                    self._next()
                    if not self._ist_op(")"):
                        self._fehler()
                    self._next()
                self._newlines()
                return FuncDef(name, self.command())
            if v in ("then", "else", "elif", "fi", "do", "done", "esac", "}", "in", "]]"):
                raise SyntaxErr(v)
        if t.typ == "OP" and t.val not in REDIR_OPS:
            if t.val == "\n":
                raise SyntaxErr("\n")
            raise SyntaxErr(t.val)
        return self.simple()

    def _redirs_nach(self, node):
        while self.tok.typ == "OP" and self.tok.val in REDIR_OPS:
            node.redirs.append(self.redir())

    def _if(self):
        n = If()
        self._next()
        while True:
            cond = self.liste()
            if not self._ist_wort("then"):
                self._fehler()
            self._next()
            body = self.liste()
            n.zweige.append((cond, body))
            if self._ist_wort("elif"):
                self._next()
                continue
            if self._ist_wort("else"):
                self._next()
                n.sonst = self.liste()
            if not self._ist_wort("fi"):
                self._fehler()
            self._next()
            break
        self._redirs_nach(n)
        return n

    def _for(self):
        self._next()
        if self.tok.typ == "OP" and self.tok.val == "(":
            # for (( init; cond; step ))
            src = self.lx.src
            start = self.tok.start
            if not src.startswith("((", start):
                self._fehler()
            ende = src.find("))", start + 2)
            if ende < 0:
                raise Incomplete()
            teile = src[start + 2:ende].split(";")
            if len(teile) != 3:
                raise SyntaxErr("((")
            self.lx.pos = ende + 2
            self._next()
            while self._ist_op(";", "\n"):
                self._next()
            body = self._do_body()
            n = ArithFor(*[x.strip() for x in teile], body)
            self._redirs_nach(n)
            return n
        if self.tok.typ != "WORD":
            self._fehler()
        var = self.tok.val
        if not NAME_RE.fullmatch(var):
            raise SyntaxErr(var) if False else _BadFor(var)
        self._next()
        words = None
        self._newlines()
        if self._ist_wort("in"):
            self._next()
            words = []
            while self.tok.typ == "WORD":
                words.append(self.tok.val)
                self._next()
            if not self._ist_op(";", "\n"):
                self._fehler()
            self._next()
        elif self._ist_op(";"):
            self._next()
        self._newlines()
        body = self._do_body()
        n = For(var, words, body)
        self._redirs_nach(n)
        return n

    def _do_body(self):
        self._newlines()
        if self._ist_wort("{"):
            self._next()
            body = self.liste()
            if not self._ist_wort("}"):
                self._fehler()
            self._next()
            return body
        if not self._ist_wort("do"):
            self._fehler()
        self._next()
        body = self.liste()
        if not self._ist_wort("done"):
            self._fehler()
        self._next()
        return body

    def _case(self):
        self._next()
        if self.tok.typ != "WORD":
            self._fehler()
        n = Case(self.tok.val)
        self._next()
        self._newlines()
        if not self._ist_wort("in"):
            self._fehler()
        self._next()
        while True:
            self._newlines()
            if self._ist_wort("esac"):
                self._next()
                break
            if self.tok.typ == "EOF":
                raise Incomplete()
            if self._ist_op("("):
                self._next()
            muster = []
            while True:
                if self.tok.typ != "WORD":
                    self._fehler()
                muster.append(self.tok.val)
                self._next()
                if self._ist_op("|"):
                    self._next()
                    continue
                break
            if not self._ist_op(")"):
                self._fehler()
            self._next()
            body = self.liste()
            ende = ";;"
            if self._ist_op(";;", ";&", ";;&"):
                ende = self.tok.val
                self._next()
            elif not self._ist_wort("esac"):
                self._fehler()
            n.items.append((muster, body, ende))
        self._redirs_nach(n)
        return n

    def _dbl(self):
        self._next()
        toks = []
        while not self._ist_wort("]]"):
            if self.tok.typ == "EOF":
                raise Incomplete()
            if self._ist_op("\n"):
                self._next()
                continue
            toks.append((self.tok.typ, self.tok.val))
            if self.tok.typ == "WORD" and self.tok.val == "=~":
                # rechte Seite roh lesen (Klammern gehoeren zum Regex)
                self._next()
                src = self.lx.src
                i = self.tok.start
                tiefe = 0
                while i < len(src):
                    c = src[i]
                    if c == "\\":
                        i += 2
                        continue
                    if c in "'\"":
                        j = src.find(c, i + 1)
                        i = (j if j >= 0 else len(src)) + 1
                        continue
                    if c == "(":
                        tiefe += 1
                    elif c == ")":
                        if tiefe == 0:
                            break
                        tiefe -= 1
                    elif c in " \t\n" and tiefe == 0:
                        break
                    i += 1
                toks.append(("WORD", src[self.tok.start:i]))
                self.lx.pos = i
            self._next()
        self._next()
        if not toks:
            raise SyntaxErr("]]")
        n = DblBracket(toks)
        self._redirs_nach(n)
        return n

    def redir(self):
        t = self.tok
        op, fd = t.val, t.ionum
        self._next()
        if self.tok.typ != "WORD":
            if self.tok.typ == "EOF":
                raise SyntaxErr("\n")
            raise SyntaxErr(self.tok.val)
        r = Redir(op, fd, self.tok.val)
        if op in ("<<", "<<-"):
            d = self.tok.val
            r.quoted = any(c in d for c in "'\"\\")
            r.delim = _quote_entfernen(d)
            self.lx.heredocs.append(r)
        self._next()
        return r

    def simple(self):
        c = Simple()
        c.lineno = self.lx.src.count("\n", 0, self.tok.start) + 1
        erstes = True
        while True:
            t = self.tok
            if t.typ == "OP" and t.val in REDIR_OPS:
                c.redirs.append(self.redir())
                continue
            if t.typ != "WORD":
                break
            v = t.val
            if not c.words:
                m = ASSIGN_RE.match(v)
                if m:
                    c.assigns.append(v)
                    self._next()
                    continue
                # Alias-Expansion
                if erstes and v in self.aliases and v not in self.alias_aktiv and not any(q in v for q in "'\"\\"):
                    self.alias_aktiv.add(v)
                    self.lx.ersetzen(t.start, t.end, self.aliases[v] + " ")
                    self._next()
                    continue
                # Funktionsdefinition name() { ... }
                if NAME_RE.fullmatch(v.replace("-", "_").replace(".", "_")) and not c.assigns and not c.redirs:
                    lx = self.lx
                    rest = lx.src[lx.pos:]
                    m2 = re.match(r"[ \t]*\(\s*\)", rest)
                    if m2:
                        lx.pos += m2.end()
                        self._next()
                        self._newlines()
                        if self.tok.typ == "EOF":
                            raise Incomplete()
                        return FuncDef(v, self.command())
            erstes = False
            c.words.append(v)
            self._next()
        if not c.words and not c.assigns and not c.redirs:
            self._fehler()
        self.alias_aktiv = set()
        return c


class _BadFor(SyntaxErr):
    def __init__(self, var):
        super().__init__(var)
        self.var = var

    def text(self):
        return f"`{self.var}': not a valid identifier"


def _quote_entfernen(w):
    out, i = [], 0
    while i < len(w):
        c = w[i]
        if c == "\\" and i + 1 < len(w):
            out.append(w[i + 1])
            i += 2
        elif c in "'\"":
            j = w.find(c, i + 1)
            if j < 0:
                j = len(w)
            out.append(w[i + 1:j])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse(src, aliases=None):
    return Parser(src, aliases).parse()
