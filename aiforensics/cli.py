from __future__ import annotations

import argparse
import json
import os
import sys

from . import recorder, sanitize
from .rules import load_rules
from .scorer import identify

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(os.path.dirname(HERE), "rules")
DEFAULT_FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures")


def cmd_identify(args):
    rules = load_rules(args.rules)
    with open(args.request, encoding="utf-8") as fh:
        record = json.load(fh)
    result = identify(record, rules)
    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "features"}, indent=1))
        return 0
    print(f"decision: {result['decision']}" + (f"  product: {result['product']}" if result["product"] else ""))
    print(f"family:   {result['family']}")
    for s in result["scores"]:
        flag = "*" if s["eligible"] else " "
        extra = f"  ({s['reason']})" if s["reason"] else ""
        print(f" {flag} {s['product']:<14} {s['score']:>3}  matched={','.join(s['matched']) or '-'}{extra}")
    return 0


def cmd_sanitize(args):
    with open(args.request, encoding="utf-8") as fh:
        record = json.load(fh)
    out = sanitize.sanitize(record)
    out["agent"] = args.agent
    out["agent_version"] = args.version
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_eval(args):
    rules = load_rules(args.rules)
    rows = []
    for label in sorted(os.listdir(args.fixtures)):
        d = os.path.join(args.fixtures, label)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                res = identify(json.load(fh), rules)
            rows.append((label, name, res["decision"], res["product"]))
    products = sorted({r["product"] for r in rules})
    print(f"{'product':<14} {'tp':>3} {'fp':>3} {'fn':>3} {'precision':>9} {'recall':>7}")
    for p in products:
        tp = sum(1 for l, _, d, pr in rows if l == p and pr == p)
        fp = sum(1 for l, _, d, pr in rows if l != p and pr == p)
        fn = sum(1 for l, _, d, pr in rows if l == p and pr != p)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        print(f"{p:<14} {tp:>3} {fp:>3} {fn:>3} {prec:>9.2f} {rec:>7.2f}")
    bad = [(l, n, d, pr) for l, n, d, pr in rows if pr != l]
    print(f"\nfixtures: {len(rows)}  misclassified: {len(bad)}")
    for l, n, d, pr in bad:
        print(f"  {l}/{n}: {d} {pr or ''}")
    return 1 if bad else 0


def cmd_record(args):
    recorder.serve(args.out, args.host, args.port)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="aiforensics", description="Identify which AI coding agent sent an LLM API request.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("identify", help="score one captured request against the rules")
    s.add_argument("request"); s.add_argument("--rules", default=DEFAULT_RULES); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_identify)
    s = sub.add_parser("sanitize", help="strip personal/session data from a raw capture to make a fixture")
    s.add_argument("request"); s.add_argument("--agent", required=True); s.add_argument("--version", default=""); s.add_argument("-o", "--output"); s.set_defaults(fn=cmd_sanitize)
    s = sub.add_parser("eval", help="precision/recall of the rules over fixtures/<label>/*.json")
    s.add_argument("--rules", default=DEFAULT_RULES); s.add_argument("--fixtures", default=DEFAULT_FIXTURES); s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("record", help="run the dead-end recorder")
    s.add_argument("--out", default="capture/raw"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=18599); s.set_defaults(fn=cmd_record)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
