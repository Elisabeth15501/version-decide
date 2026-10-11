#!/usr/bin/env python3
"""semver_check.py -- 版本号判定的确定性算术内核（Python 3.8+，零依赖）。

设计目标：把"版本号怎么算"从人脑/模型的手工推演，变成一次可复算的函数调用。
同一个输入必须永远得到同一个输出，这是本技能"唯一且明确"承诺的技术保证。

子命令：
    check <version>                    校验并规范化版本号，同时报告生态兼容性
    next <version> --level <lvl>       按指定级别计算下一个版本号
    analyze <version> --changes <spec> 从变更描述集合推导版本号（核心入口）
    compare <a> <b>                    比较两个版本号的优先级
    explain <version> --changes <spec> 只输出判定理由链，不打印最终号

级别（level）：
    none    不需要发版
    patch   向后兼容的修复
    minor   向后兼容的新功能 / 标记废弃
    major   向后不兼容的破坏性变更
    stable  0.x 毕业为 1.0.0（显式动作，不来自变更类型）

语言：用户面向文案（reasons / 错误提示 / 级别说明）随 --lang 切换；
默认 auto（探测 LC_ALL/LC_MESSAGES/LANG，回退 en），满足 ClawHub English-default。
JSON 输出键名始终为英文（current/next/level/...），值随语言。
完整规则与依据见 ../SKILL.md 与 ../references/decision-tables.md
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from typing import List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# 国际化（零依赖；默认英文，auto 回退 en）
# --------------------------------------------------------------------------

_LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locales")
_STR: dict = {}


def load_locale(lang: str) -> dict:
    """读取 locales/<lang>.json。文件缺失时回退 en。"""
    path = os.path.join(_LOCALE_DIR, lang + ".json")
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        with io.open(os.path.join(_LOCALE_DIR, "en.json"), encoding="utf-8") as f:
            return json.load(f)


def set_lang(lang: str) -> None:
    global _STR
    _STR = load_locale(lang)


def _(key: str, *args, **kwargs) -> str:
    """按当前语言取文案；key 缺失时原样返回 key，便于排查。"""
    s = _STR.get(key, key)
    if args or kwargs:
        try:
            return s.format(*args, **kwargs)
        except (IndexError, KeyError, ValueError):
            return s
    return s


def detect_lang() -> str:
    """auto 模式：探测环境变量，命中 zh 返回 zh，否则 en（满足 English default）。"""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v:
            code = v.split(".")[0].lower()
            if code.startswith("zh"):
                return "zh"
    return "en"


set_lang("en")  # 模块导入即默认英文，保证测试/无 CLI 场景可用


# --------------------------------------------------------------------------
# 解析
# --------------------------------------------------------------------------

_IDENT = r"(?:0|[1-9]\d*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_PRE_RE = re.compile(r"^v?(?P<m>0|[1-9]\d*)\.(?P<n>0|[1-9]\d*)\.(?P<p>0|[1-9]\d*)"
                     r"(?:-(?P<pre>" + _IDENT + r"(?:\." + _IDENT + r")*))?"
                     r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$")

# PEP 440 特征片段：a/b/rc 后缀、.post、.dev、epoch 的 "!"、下划线归一化
_PEP440_HINTS = (
    (re.compile(r"^\d+!"), "PEP 440 epoch（前缀 N!）"),
    (re.compile(r"(?i)(?:^|\.)(?:a|b|rc)\d+(?:\.post|\.dev)?$"), "PEP 440 预发布后缀（a1/b2/rc1）"),
    (re.compile(r"(?i)\.post\d+"), "PEP 440 post-release（.postN）"),
    (re.compile(r"(?i)\.dev\d+"), "PEP 440 dev-release（.devN）"),
    (re.compile(r"_"), "PEP 440 归一化写法（下划线）"),
)


class BadVersion(ValueError):
    """版本号不符合 SemVer 2.0.0 语法。"""


class Version:
    """一个 SemVer 2.0.0 版本号。"""

    __slots__ = ("major", "minor", "patch", "pre", "build")

    def __init__(self, major: int, minor: int, patch: int,
                 pre: Tuple[object, ...] = (), build: str = "") -> None:
        self.major = major
        self.minor = minor
        self.patch = patch
        self.pre = pre
        self.build = build

    # -- 解析 -------------------------------------------------------------
    @classmethod
    def parse(cls, text: str) -> "Version":
        m = _PRE_RE.match(text.strip())
        if not m:
            raise BadVersion(text)
        pre_raw = m.group("pre")
        pre: Tuple[object, ...] = ()
        if pre_raw is not None:
            parts: List[object] = []
            for chunk in pre_raw.split("."):
                parts.append(int(chunk) if chunk.isdigit() else chunk)
            pre = tuple(parts)
        return cls(int(m.group("m")), int(m.group("n")), int(m.group("p")),
                   pre, m.group("build") or "")

    # -- 序列化 -----------------------------------------------------------
    def core(self) -> str:
        return "%d.%d.%d" % (self.major, self.minor, self.patch)

    def __str__(self) -> str:
        s = self.core()
        if self.pre:
            s += "-" + ".".join(str(x) for x in self.pre)
        if self.build:
            s += "+" + self.build
        return s

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "Version(%r)" % str(self)

    # -- 比较（SemVer 2.0.0 §11）-----------------------------------------
    def _key(self):
        # 预发布整体优先级低于同 core 的正式版：用 (0,) 与 (1,) 做哨兵比较
        return (self.major, self.minor, self.patch,
                1 if not self.pre else 0, self.pre)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self.cmp_key() == other.cmp_key()

    def __hash__(self) -> int:
        return hash(self.cmp_key())

    def cmp_key(self):
        return (self.major, self.minor, self.patch,
                1 if not self.pre else 0, self._normalized_pre())

    def _normalized_pre(self) -> Tuple:
        # 纯数字场按数值比；非数字场按 ASCII 串比；数字场优先级低于非数字场；
        # 前缀全等时，段数多的优先级更高（补 None 占位）
        out: List[Tuple[int, object]] = []
        for ident in self.pre:
            if isinstance(ident, int):
                out.append((0, ident))
            else:
                out.append((1, ident))
        while len(out) < 8:
            out.append((-1, 0))
        return tuple(out[:8])

    def __lt__(self, other: "Version") -> bool:
        return self.cmp_key() < other.cmp_key()


# --------------------------------------------------------------------------
# 级别与递增
# --------------------------------------------------------------------------

LEVEL_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}
LEVELS = list(LEVEL_ORDER)

# "stable" 是显式毕业动作，不由变更类型推导，故不参与 max() 比较
_VALID_LEVELS = set(LEVEL_ORDER) | {"stable"}

_LEVEL_ALIAS = {
    "fix": "patch", "bugfix": "patch", "perf": "patch", "revert": "patch",
    "security": "patch", "patch": "patch", "none": "none", "skip": "none",
    "feat": "minor", "feature": "minor", "deprecate": "minor", "minor": "minor",
    "breaking": "major", "break": "major", "major": "major",
}

PRE_STAGES = ("alpha", "beta", "rc")


def normalize_level(token: str) -> str:
    t = token.strip().lower()
    if t in _VALID_LEVELS:
        return t
    if t in _LEVEL_ALIAS:
        return _LEVEL_ALIAS[t]
    raise ValueError(_("ERR_UNKNOWN_CHANGE_TYPE", token, ", ".join(sorted(_VALID_LEVELS))))


# --------------------------------------------------------------------------
# 核心：给定当前版本与目标级别，算出下一个版本号
# --------------------------------------------------------------------------

def next_version(current: Version, level: str, pre: Optional[str] = None,
                 build: Optional[str] = None, rebase: bool = False) -> Tuple[Version, List[str]]:
    """返回 (新版本, 理由链)。level 必须是 LEVELS 或 stable 之一。

    rebase=True 时强制从当前 core 重新递增（用于预发布线内决定换目标版本号的场景）。
    """
    if level not in _VALID_LEVELS:
        raise ValueError(_("ERR_INVALID_LEVEL", ", ".join(sorted(_VALID_LEVELS))))
    reasons: List[str] = []
    m, n, p = current.major, current.minor, current.patch
    in_prerelease = bool(current.pre)

    if level == "none":
        reasons.append(_("REASON_NONE_NO_BUMP", current.core()))
        return Version(m, n, p, current.pre, current.build), reasons

    if level == "stable":
        if m != 0:
            reasons.append(_("REASON_STABLE_NOT_APPLICABLE", m))
            return Version(m, n, p, current.pre, current.build), reasons
        reasons.append(_("REASON_STABLE_GRADUATION"))
        base = Version(1, 0, 0)
    elif in_prerelease and not rebase:
        # 预发布线已在飞：core 保持不变，新变更累积进同一条预发布线。
        # SemVer 与 npm/Cargo 都允许预发布之间出现破坏性变更，这正是预发布通道的意义。
        base = Version(m, n, p)
        reasons.append(_("REASON_PRERELEASE_LINE", current.core()))
    elif m == 0:
        # ---- 0.x 特殊区：npm/cargo 把 minor 视为破坏性边界 -------------
        if level == "patch":
            base = Version(0, n, p + 1)
            reasons.append(_("REASON_0X_PATCH", n, p))
        else:  # minor / major
            base = Version(0, n + 1, 0)
            reasons.append(_("REASON_0X_MINOR", n, p, level, n + 1))
    else:
        if level == "patch":
            base = Version(m, n, p + 1)
            reasons.append(_("REASON_PATCH"))
        elif level == "minor":
            base = Version(m, n + 1, 0)
            reasons.append(_("REASON_MINOR"))
        else:
            base = Version(m + 1, 0, 0)
            reasons.append(_("REASON_MAJOR"))

    # ---- 预发布标识符处理 ----------------------------------------------
    if pre:
        pre_norm = pre.strip().lstrip("-")
        if not re.match(r"^(alpha|beta|rc)(\.\d+)?$", pre_norm):
            raise ValueError(_("ERR_BAD_PRE", pre))
        # 裸阶段名统一补 .1，保证版本号形状稳定可排序
        pre_tuple: Tuple[object, ...] = _pre_from_stage(pre_norm)

        same_core = (base.major == m and base.minor == n and base.patch == p)
        if current.pre and same_core:
            pre_tuple = _advance_pre(current.pre, pre_norm, reasons)
        else:
            reasons.append(_("REASON_PRE_OPEN", base.core()))
        new = Version(base.major, base.minor, base.patch, pre_tuple,
                      build if build is not None else current.build)
    else:
        if current.pre and base.core() == current.core() and level in ("patch", "minor"):
            reasons.append(_("REASON_PRE_TO_STABLE"))
        new = Version(base.major, base.minor, base.patch, (), build if build is not None else "")

    if new.build:
        reasons.append(_("REASON_BUILD", new.build))
    return new, reasons


def _pre_from_stage(text: str) -> Tuple[object, ...]:
    if "." in text:
        stage, num = text.split(".", 1)
        return (stage, int(num))
    return (text, 1)


def _advance_pre(current_pre: Tuple[object, ...], requested: str,
                 reasons: List[str]) -> Tuple[object, ...]:
    """在同一 core 内推进预发布阶段。"""
    want_stage = requested.split(".")[0]
    cur_stage = current_pre[0] if current_pre else ""
    cur_num = current_pre[1] if len(current_pre) > 1 and isinstance(current_pre[1], int) else 0

    if cur_stage == want_stage:
        reasons.append(_("REASON_PRE_ADVANCE_SAME", cur_stage, cur_num, cur_num + 1))
        return (want_stage, cur_num + 1)

    try:
        cur_idx = PRE_STAGES.index(cur_stage)
    except ValueError:
        cur_idx = -1
    try:
        want_idx = PRE_STAGES.index(want_stage)
    except ValueError:
        want_idx = -1

    if want_idx == cur_idx + 1:
        reasons.append(_("REASON_PRE_ADVANCE_STAGE", cur_stage, want_stage))
        return (want_stage, 1)
    if want_idx == cur_idx:
        return (want_stage, cur_num + 1)
    if want_idx > cur_idx:
        raise ValueError(
            _("ERR_SKIP_PRE", cur_stage, want_stage, PRE_STAGES[cur_idx + 1]))
    reasons.append(_("REASON_PRE_REVERT", cur_stage, want_stage))
    return (want_stage, 1)


# --------------------------------------------------------------------------
# 变更描述 → 级别
# --------------------------------------------------------------------------

# 变更类型 → (基准级别, 文案 key)。破坏性以 "break:" 前缀显式声明，压过类型默认值。
_CHANGE_RULES = {
    "feat": ("minor", "DESC_FEAT"),
    "feature": ("minor", "DESC_FEAT"),
    "fix": ("patch", "DESC_FIX"),
    "bugfix": ("patch", "DESC_FIX"),
    "perf": ("patch", "DESC_PERF"),
    "revert": ("patch", "DESC_REVERT"),
    "security": ("patch", "DESC_SECURITY"),
    "docs": ("none", "DESC_DOCS"),
    "test": ("none", "DESC_TEST"),
    "ci": ("none", "DESC_CI"),
    "chore": ("none", "DESC_CHORE"),
    "style": ("none", "DESC_STYLE"),
    "build": ("none", "DESC_BUILD"),
    "refactor": ("patch", "DESC_REFACTOR"),
    "deprecate": ("minor", "DESC_DEPRECATE"),
    "revertbreak": ("major", "DESC_REVERTBREAK"),
    "breaking": ("major", "DESC_BREAKING"),
}


def level_for_change(spec: str) -> Tuple[str, str, bool]:
    """解析单条变更描述 → (level, 说明, 是否破坏性)。

    语法： "type" | "type: 说明" | "break: 说明" | "type@break: 说明"
    """
    raw = spec.strip()
    if not raw:
        raise ValueError(_("ERR_EMPTY_CHANGE"))

    forced_break = False
    # 1) 先剥离显式破坏性标记
    m_break = re.match(r"^(?:break|breaking)\s*:\s*(.*)$", raw, re.IGNORECASE)
    if m_break:
        forced_break = True
        raw = m_break.group(1).strip()
    if "@break" in raw:
        forced_break = True
        raw = raw.replace("@break", "", 1).strip()

    # 2) 切出类型与说明。类型是冒号前的词；若它不是已知类型，
    #    则视为"未写类型"（仅在强制破坏时允许，整句作为说明）。
    kind, sep, note = raw.partition(":")
    key = kind.strip().lower()
    note = note.strip()
    if key not in _CHANGE_RULES:
        if not forced_break:
            raise ValueError(_("ERR_UNKNOWN_CHANGE_TYPE", kind.strip(), ", ".join(sorted(_CHANGE_RULES))))
        key, note = "breaking", (kind.strip() + (("：" + note) if sep and note else ""))

    level, desc = _CHANGE_RULES[key]
    if forced_break:
        return "major", (note or _(desc)) + _("MSG_FORCED_BREAK"), True
    return level, note or _(desc), False


def analyze(current: Version, specs: Sequence[str], pre: Optional[str] = None,
            build: Optional[str] = None, rebase: bool = False) -> dict:
    """核心入口：变更集合 → 唯一版本号建议。"""
    if not specs:
        raise ValueError(_("ERR_NO_CHANGES"))
    items = [level_for_change(s) for s in specs]
    level = max((lv for lv, _, _ in items), key=lambda x: LEVEL_ORDER[x])
    has_break = any(br for _, _, br in items)
    driver = [d for lv, d, _ in items if lv == level]

    if has_break and current.major == 0 and level != "major":
        reasons_extra = _("REASON_0X_BREAKING_NOTE")
    else:
        reasons_extra = ""

    new, reasons = next_version(current, level, pre=pre, build=build, rebase=rebase)
    report = {
        "current": str(current),
        "level": level,
        "has_breaking": has_break,
        "next": str(new),
        "changes": [
            {"spec": s, "level": lv, "breaking": br, "why": d}
            for s, (lv, d, br) in zip(specs, items)
        ],
        "drivers": driver,
        "reasons": reasons,
    }
    if reasons_extra:
        report["reasons"].append(reasons_extra)
    return report


# --------------------------------------------------------------------------
# 生态兼容性提示
# --------------------------------------------------------------------------

def ecosystem_notes(text: str) -> List[str]:
    notes: List[str] = []
    for rx, label in _PEP440_HINTS:
        if rx.search(text.strip()):
            notes.append(_("MSG_PEP440_HINT", label))
    if text.strip().startswith("v"):
        notes.append(_("MSG_GIT_V_PREFIX"))
    return notes


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="semver_check",
        description=_("MSG_CLI_DESC"))
    # 公共参数：语言（默认 auto，回退 en）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", default="auto", choices=["auto", "en", "zh"],
                        help="输出语言：auto（探测环境，回退 en）| en | zh")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", parents=[common], help=_("HELP_CHECK"))
    p_check.add_argument("version")

    p_next = sub.add_parser("next", parents=[common], help=_("HELP_NEXT"))
    p_next.add_argument("version")
    p_next.add_argument("--level", required=True, help=_("HELP_LEVEL"))
    p_next.add_argument("--pre", help=_("HELP_PRE"))
    p_next.add_argument("--build", help=_("HELP_BUILD"))
    p_next.add_argument("--rebase", action="store_true",
                        help=_("HELP_REBASE"))

    p_an = sub.add_parser("analyze", parents=[common], help=_("HELP_ANALYZE"))
    p_an.add_argument("version")
    p_an.add_argument("--changes", nargs="+", required=True, metavar="SPEC", help=_("HELP_CHANGES"))
    p_an.add_argument("--pre")
    p_an.add_argument("--build")
    p_an.add_argument("--rebase", action="store_true", help=_("HELP_REBASE"))

    p_exp = sub.add_parser("explain", parents=[common], help=_("HELP_EXPLAIN"))
    p_exp.add_argument("version")
    p_exp.add_argument("--changes", nargs="+", required=True, metavar="SPEC", help=_("HELP_CHANGES"))
    p_exp.add_argument("--pre")
    p_exp.add_argument("--build")
    p_exp.add_argument("--rebase", action="store_true", help=_("HELP_REBASE"))

    p_cmp = sub.add_parser("compare", parents=[common], help=_("HELP_COMPARE"))
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")

    args = ap.parse_args(argv)
    # 语言解析（在真正产出任何文案前完成）
    set_lang(detect_lang() if args.lang == "auto" else args.lang)
    try:
        if args.cmd == "compare":
            a, b = Version.parse(args.a), Version.parse(args.b)
            _print({"a": str(a), "b": str(b),
                    "result": "a < b" if a < b else ("a > b" if b < a else "a == b"),
                    "note": _("MSG_COMPARE_BUILD")})
            return 0

        cur = Version.parse(args.version)
        notes = ecosystem_notes(args.version)

        if args.cmd == "check":
            _print({"valid": True, "normalized": str(cur),
                    "is_prerelease": bool(cur.pre), "build": cur.build or None,
                    "ecosystem_notes": notes})
            return 0

        if args.cmd == "next":
            lvl = normalize_level(args.level)
            new, reasons = next_version(cur, lvl, pre=args.pre, build=args.build,
                                       rebase=args.rebase)
            _print({"current": str(cur), "level": lvl, "next": str(new),
                    "reasons": reasons, "ecosystem_notes": notes})
            return 0

        report = analyze(cur, args.changes, pre=args.pre, build=args.build,
                         rebase=args.rebase)
        report["ecosystem_notes"] = notes
        if args.cmd == "explain":
            report.pop("next", None)
        _print(report)
        return 0
    except BadVersion as exc:
        _print({"valid": False, "input": str(exc),
                "error": _("ERR_BAD_VERSION_SYNTAX"),
                "ecosystem_notes": ecosystem_notes(str(exc))})
        return 2
    except ValueError as exc:
        _print({"valid": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
