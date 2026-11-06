"""Командная строка: aprepro <команда>.

  aprepro manifest [--out FILE]               скачать манифест firmware.ardupilot.org и показать выпуски
  aprepro plan --manifest FILE --out PLAN     составить план (группы, панель, случайная выборка)
  aprepro matrix PLAN                         группы плана в JSON для матрицы GitHub Actions
  aprepro run PLAN --group ID [--board B ...] пересобрать платы группы и сверить с официальными
  aprepro report PLAN [--md FILE] [--check]   сводка результатов
  aprepro show FILE.apj                       метаданные и дайджесты прошивки
  aprepro compare OFFICIAL.apj REBUILT.apj    сверить две прошивки
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .util import ToolError, rjson, wjson

DEFAULT_GROUPS = ["Copter-4.4.4", "Copter-4.5.7", "Copter-4.6.3", "Copter-4.7.2",
                  "Plane-4.4.4", "Plane-4.5.7", "Plane-4.6.3", "Plane-4.7.2",
                  "Rover-4.4.0", "Rover-4.5.7", "Rover-4.6.3", "Rover-4.7.2"]


def _split(g: str) -> tuple:
    vehicle, _, version = g.partition("-")
    if not version:
        raise ToolError(f"группа задаётся как Изделие-версия, а не {g!r}")
    return vehicle, version


def cmd_manifest(args) -> int:
    from .manifest import VEHICLES, Manifest
    m = Manifest.download(args.out) if not Path(args.out).exists() or args.refresh else Manifest.load(args.out)
    print(json.dumps(m.meta, ensure_ascii=False))
    for v in VEHICLES:
        vs = m.stable_versions(v)
        print(f"{v}: {len(vs)} стабильных выпусков, последние: {', '.join(vs[-6:])}")
    return 0


def cmd_plan(args) -> int:
    from .manifest import Manifest
    from .plan import PANEL, make
    m = Manifest.load(args.manifest)
    if args.manifest_meta:
        m.meta.update(rjson(args.manifest_meta))
    groups = [_split(g) for g in (args.group or DEFAULT_GROUPS)]
    tag_groups = [_split(g) for g in args.tag_group]
    plan = make(m, groups, seed=args.seed, sample=args.sample, panel=args.panel or PANEL, tag_groups=tag_groups)
    wjson(args.out, plan)
    n = sum(len(g["targets"]) for g in plan["groups"])
    print(f"план {args.out}: групп {len(plan['groups'])}, сборок {n}")
    for g in plan["groups"]:
        print(f"  {g['id']:<16} {len(g['targets']):>2} плат из {g['boards_in_release']}: "
              + ", ".join(t["board"] for t in g["targets"]))
    return 0


def cmd_matrix(args) -> int:
    plan = rjson(args.plan)
    print(json.dumps([g["id"] for g in plan["groups"]]))
    return 0


def cmd_run(args) -> int:
    from .run import run_group
    plan = rjson(args.plan)
    rows = run_group(plan, Path(args.plan).stem, args.group, Path(args.work), Path(args.results), boards=args.board,
                     jobs=args.jobs, keep=args.keep, log=lambda m: print(m, flush=True))
    return 0 if all(r["verdict"] != "error" for r in rows) else 3


def cmd_report(args) -> int:
    from .report import environments, load, markdown, readme_block, readme_with_block, summary
    plan = rjson(args.plan)
    name = Path(args.plan).stem
    rows = load(Path(args.results), name)
    text = markdown(plan, name, rows, environments(Path(args.results), name))
    if args.json:
        wjson(args.json, summary(plan, rows))
    outputs = []
    if args.md:
        outputs.append((Path(args.md), lambda cur: text))
    if args.readme:
        outputs.append((Path(args.readme), lambda cur: readme_with_block(cur, readme_block(plan, rows))))
    if not outputs:
        print(text)
    stale = []
    for path, make in outputs:
        cur = path.read_text(encoding="utf-8") if path.exists() else ""
        new = make(cur)
        if cur != new:
            if args.check:
                stale.append(str(path))
            else:
                path.write_text(new, encoding="utf-8")
    if stale:
        print(f"устарели: {', '.join(stale)}; выполните aprepro report {args.plan} --md docs/results.md "
              "--readme README.md", file=sys.stderr)
        return 1
    return 0


def cmd_show(args) -> int:
    from .apj import parse
    a = parse(Path(args.file).read_bytes())
    print(json.dumps({**a.meta, **a.digests()}, ensure_ascii=False, indent=1))
    return 0


def cmd_compare(args) -> int:
    from .compare import compare
    res = compare(Path(args.official).read_bytes(), Path(args.rebuilt).read_bytes())
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res["verdict"] in ("reproducible", "image-identical") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="aprepro", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("manifest", help="манифест firmware.ardupilot.org")
    p.add_argument("--out", default="work/manifest.json.gz")
    p.add_argument("--refresh", action="store_true", help="скачать заново, даже если файл есть")
    p.set_defaults(func=cmd_manifest)
    p = sub.add_parser("plan", help="составить план исследования")
    p.add_argument("--manifest", required=True)
    p.add_argument("--manifest-meta", help="JSON с url и датой загрузки манифеста")
    p.add_argument("--out", required=True)
    p.add_argument("--group", action="append", help="Изделие-версия, например Copter-4.7.2 (по умолчанию 11 групп)")
    p.add_argument("--tag-group", action="append", default=[], help="Изделие-версия, собрать панель ещё и из тега")
    p.add_argument("--panel", nargs="+")
    p.add_argument("--sample", type=int, default=4)
    p.add_argument("--seed", required=True)
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("matrix", help="группы плана для матрицы CI")
    p.add_argument("plan")
    p.set_defaults(func=cmd_matrix)
    p = sub.add_parser("run", help="пересобрать и сверить платы группы")
    p.add_argument("plan")
    p.add_argument("--group", required=True)
    p.add_argument("--board", action="append")
    p.add_argument("--work", default="work")
    p.add_argument("--results", default="results")
    p.add_argument("-j", "--jobs", type=int)
    p.add_argument("--keep", action="store_true", help="не удалять каталоги сборки")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("report", help="сводка результатов")
    p.add_argument("plan")
    p.add_argument("--results", default="results")
    p.add_argument("--md")
    p.add_argument("--readme", help="обновить сводку в README между метками results:begin/end")
    p.add_argument("--json")
    p.add_argument("--check", action="store_true")
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("show", help="метаданные прошивки .apj")
    p.add_argument("file")
    p.set_defaults(func=cmd_show)
    p = sub.add_parser("compare", help="сверить две прошивки .apj")
    p.add_argument("official")
    p.add_argument("rebuilt")
    p.set_defaults(func=cmd_compare)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ToolError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
