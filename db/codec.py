"""Bezeztrátový převod JSON objekt <-> řádek SQLite (sdíleno importem i čtením).

extra_json = {"x": nenamapované klíče / hodnoty nevhodného typu,
              "n": namapované klíče s explicitním null,
              "o": pořadí klíčů (jen když se liší od kanonického),
              "a": vnořené seznamy/objekty, které ve zdroji chyběly (u "vždy přítomných")}
"""
import json

_OK = {
    'TEXT': lambda v: isinstance(v, str),
    'INTEGER': lambda v: isinstance(v, int) and not isinstance(v, bool),
    'REAL': lambda v: isinstance(v, float),
    'BOOL': lambda v: isinstance(v, bool),
}


def pack(obj, cols, nested=(), default_present=()):
    """cols: [(key, type)] -> (values dict, extra_json|None).
    nested: klíče obsluhované podřízenými tabulkami (seznamy / 1:1 objekty)."""
    vals, x, n = {}, {}, []
    colkeys = [k for k, _ in cols]
    types = dict(cols)
    for k, v in obj.items():
        if k in types:
            if v is None:
                n.append(k); vals[k] = None
            elif _OK[types[k]](v):
                vals[k] = int(v) if types[k] == 'BOOL' else v
            else:
                x[k] = v; vals[k] = None
        elif k in nested:
            if v is None:
                n.append(k)
            elif not isinstance(v, (list, dict)):
                x[k] = v
        else:
            x[k] = v
    canon = [k for k in list(colkeys) + list(nested) if k in obj and k not in x] + [k for k in obj if k in x]
    meta = {}
    if x: meta['x'] = x
    if n: meta['n'] = n
    if list(obj.keys()) != canon: meta['o'] = list(obj.keys())
    a = [k for k in default_present if k not in obj]
    if a: meta['a'] = a
    p = [k for k in nested if k not in default_present and k in obj and obj[k] is not None and len(obj[k]) == 0]
    if p: meta['p'] = p
    for k in colkeys:
        vals.setdefault(k, None)
    return vals, (json.dumps(meta, ensure_ascii=False) if meta else None)


def unpack(row, cols, extra_json, nested=None, canon_order=None, default_present=()):
    """row: dict col->value, nested: {key: value-or-None(=absent)} -> původní objekt."""
    meta = json.loads(extra_json) if extra_json else {}
    x, nulls = meta.get('x', {}), set(meta.get('n', []))
    types = dict(cols)
    vals = {}
    for k, _ in cols:
        v = row.get(k)
        if k in x:
            continue
        if v is not None:
            vals[k] = bool(v) if types[k] == 'BOOL' else v
        elif k in nulls:
            vals[k] = None
    for k, v in (nested or {}).items():
        if k in nulls:
            vals[k] = None
        elif v is None or k in meta.get('a', []):
            continue
        elif len(v) or k in default_present or k in meta.get('p', []):
            vals[k] = v
    vals.update(x)
    order = meta.get('o') or ([k for k in (canon_order or [c for c, _ in cols] + list(nested or {})) if k in vals] + [k for k in x])
    seen = set(); out = {}
    for k in order:
        if k in vals and k not in seen:
            out[k] = vals[k]; seen.add(k)
    for k in vals:
        if k not in seen:
            out[k] = vals[k]
    return out
