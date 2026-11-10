"""Bash-Arithmetik $(( ... )) mit 64-Bit-Ganzzahlen und Bash-Fehlermeldungen."""
import re

TOK = re.compile(r"\s*(0[xX][0-9a-fA-F]+|\d+#[0-9a-zA-Z@_]+|\d+|[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?|"
                 r"\*\*=|<<=|>>=|\+\+|--|\*\*|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%<>=!~&|^?:(),])")


class ArithError(Exception):
    def __init__(self, msg, token=""):
        super().__init__(msg)
        self.msg, self.token = msg, token


def _wrap(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


def _zahl(text):
    t = text.strip()
    if t == "":
        return 0
    try:
        if t.lower().startswith("0x"):
            return int(t, 16)
        if "#" in t:
            b, z = t.split("#", 1)
            return int(z, int(b))
        if len(t) > 1 and t.startswith("0") and t.isdigit():
            return int(t, 8)
        return int(t)
    except ValueError:
        return None


class Arith:
    def __init__(self, expr, getv, setv):
        self.src = expr
        self.getv, self.setv = getv, setv
        self.toks = []
        self.pos = []
        pos = 0
        s = expr
        while pos < len(s):
            if s[pos:].strip() == "":
                break
            m = TOK.match(s, pos)
            if not m:
                rest = s[pos:].lstrip()
                raise ArithError("syntax error: invalid arithmetic operator", rest)
            self.toks.append(m.group(1))
            self.pos.append(m.start(1))
            pos = m.end()
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def nimm(self):
        t = self.peek()
        self.i += 1
        return t

    def rest(self):
        return " ".join(self.toks[self.i:])

    def run(self):
        if not self.toks:
            return 0
        v = self.comma()
        if self.peek() is not None:
            raise ArithError("syntax error in expression", self.rest())
        return v

    def comma(self):
        v = self.assign()
        while self.peek() == ",":
            self.nimm()
            v = self.assign()
        return v

    def assign(self):
        t = self.peek()
        nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
        if t and re.match(r"[A-Za-z_]", t) and nxt in ("=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^=",
                                                          "**="):
            self.nimm()
            self.nimm()
            rechts = self.assign()
            if nxt == "=":
                v = rechts
            else:
                v = self.binop(nxt[:-1], self._var(t), rechts)
            self.setv(t, str(v))
            return v
        return self.ternary()

    def ternary(self):
        c = self.lor()
        if self.peek() == "?":
            self.nimm()
            a = self.assign()
            if self.nimm() != ":":
                raise ArithError("expected `:'", self.rest())
            b = self.assign()
            return a if c else b
        return c

    def lor(self):
        v = self.land()
        while self.peek() == "||":
            self.nimm()
            r = self.land()
            v = 1 if (v or r) else 0
        return v

    def land(self):
        v = self.bor()
        while self.peek() == "&&":
            self.nimm()
            r = self.bor()
            v = 1 if (v and r) else 0
        return v

    def _links(self, ops, nxt):
        v = nxt()
        while self.peek() in ops:
            op = self.nimm()
            v = self.binop(op, v, nxt())
        return v

    def bor(self):
        return self._links(("|",), self.bxor)

    def bxor(self):
        return self._links(("^",), self.band)

    def band(self):
        return self._links(("&",), self.eq)

    def eq(self):
        return self._links(("==", "!="), self.rel)

    def rel(self):
        return self._links(("<", ">", "<=", ">="), self.shift)

    def shift(self):
        return self._links(("<<", ">>"), self.add)

    def add(self):
        return self._links(("+", "-"), self.mul)

    def mul(self):
        return self._links(("*", "/", "%"), self.pow)

    def pow(self):
        v = self.unary()
        if self.peek() == "**":
            self.nimm()
            e = self.pow()
            if e < 0:
                raise ArithError("exponent less than 0", str(e))
            return _wrap(v ** e)
        return v

    def unary(self):
        t = self.peek()
        if t in ("-", "+", "!", "~"):
            self.nimm()
            v = self.unary()
            return {"-": -v, "+": v, "!": 0 if v else 1, "~": ~v}[t]
        if t in ("++", "--"):
            self.nimm()
            name = self.nimm()
            if not name or not re.match(r"[A-Za-z_]", name):
                raise ArithError("syntax error: operand expected", name or "")
            v = self._var(name) + (1 if t == "++" else -1)
            self.setv(name, str(v))
            return v
        return self.postfix()

    def postfix(self):
        t = self.nimm()
        if t is None:
            vorher = self.pos[self.i - 2] if self.i >= 2 and self.i - 2 < len(self.pos) else 0
            raise ArithError("syntax error: operand expected", self.src[vorher:])
        if t == "(":
            v = self.comma()
            if self.nimm() != ")":
                raise ArithError("missing `)'", self.rest())
            return v
        if re.match(r"[A-Za-z_]", t):
            if self.peek() in ("++", "--"):
                op = self.nimm()
                v = self._var(t)
                self.setv(t, str(v + (1 if op == "++" else -1)))
                return v
            return self._var(t)
        v = _zahl(t)
        if v is None:
            raise ArithError("value too great for base", t)
        return v

    def _var(self, name, tiefe=0):
        roh = self.getv(name) or ""
        z = _zahl(roh)
        if z is not None:
            return z
        if tiefe < 10 and roh.strip():
            sub = Arith(roh, self.getv, self.setv)
            return sub.run()
        return 0

    def binop(self, op, a, b):
        if op in ("/", "%") and b == 0:
            raise ArithError("division by 0", str(b))
        if op == "/":
            q = abs(a) // abs(b)
            return _wrap(q if (a >= 0) == (b >= 0) else -q)
        if op == "%":
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        f = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "<<": lambda: a << (b & 63),
             ">>": lambda: a >> (b & 63), "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
             "<": lambda: int(a < b), ">": lambda: int(a > b), "<=": lambda: int(a <= b), ">=": lambda: int(a >= b),
             "==": lambda: int(a == b), "!=": lambda: int(a != b), "**": lambda: a ** b}
        return _wrap(f[op]())


def auswerten(expr, getv, setv):
    return Arith(expr, getv, setv).run()
