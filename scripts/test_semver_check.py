#!/usr/bin/env python3
"""semver_check.py 的回归测试。用法：python test_semver_check.py"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from semver_check import (Version, BadVersion, analyze, next_version, normalize_level,
                     level_for_change, set_lang, LEVEL_ORDER)


class TestParse(unittest.TestCase):
    def test_plain(self):
        v = Version.parse("1.2.3")
        self.assertEqual((v.major, v.minor, v.patch), (1, 2, 3))
        self.assertEqual(str(v), "1.2.3")

    def test_v_prefix_and_metadata(self):
        self.assertEqual(str(Version.parse("v1.2.3")), "1.2.3")
        v = Version.parse("1.0.0-alpha.1+21AF26D3")
        self.assertEqual(v.pre, ("alpha", 1))
        self.assertEqual(v.build, "21AF26D3")
        self.assertEqual(str(v), "1.0.0-alpha.1+21AF26D3")

    def test_rejects_leading_zero(self):
        for bad in ("01.2.3", "1.02.3", "1.2.03", "1.2", "1.2.3.4", "1.0.0-alpha.01", ""):
            with self.assertRaises(BadVersion, msg=bad):
                Version.parse(bad)

    def test_rejects_pep440(self):
        for bad in ("1.0.0a1", "1.0.0rc1", "1!1.0.0", "1.0.0.post1", "1.0.0.dev1"):
            with self.assertRaises(BadVersion, msg=bad):
                Version.parse(bad)


class TestPrecedence(unittest.TestCase):
    """SemVer §11 官方示例序列，必须逐项成立。"""

    CHAIN = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]

    def test_official_chain(self):
        vs = [Version.parse(s) for s in self.CHAIN]
        for a, b in zip(vs, vs[1:]):
            self.assertTrue(a < b, "%s 应小于 %s" % (a, b))

    def test_build_metadata_ignored(self):
        a = Version.parse("1.0.0+build.1")
        b = Version.parse("1.0.0+build.2")
        self.assertEqual(a, b)          # 优先级相同
        self.assertEqual(a.cmp_key(), b.cmp_key())

    def test_numeric_beats_alphanumeric_position(self):
        # 数字段优先级低于非数字段：1 < alpha
        self.assertTrue(Version.parse("1.0.0-1") < Version.parse("1.0.0-alpha"))

    def test_more_fields_wins(self):
        self.assertTrue(Version.parse("1.0.0-alpha") < Version.parse("1.0.0-alpha.1"))


class TestBump(unittest.TestCase):
    def bump(self, cur, level, **kw):
        return str(next_version(Version.parse(cur), level, **kw)[0])

    def test_1x_rules(self):
        self.assertEqual(self.bump("1.4.2", "patch"), "1.4.3")
        self.assertEqual(self.bump("1.4.2", "minor"), "1.5.0")
        self.assertEqual(self.bump("1.4.2", "major"), "2.0.0")
        self.assertEqual(self.bump("1.9.0", "minor"), "1.10.0")  # 非字典序

    def test_0x_rules(self):
        # 0.x 阶段 minor 即破坏性边界
        self.assertEqual(self.bump("0.1.9", "patch"), "0.1.10")
        self.assertEqual(self.bump("0.1.9", "minor"), "0.2.0")
        self.assertEqual(self.bump("0.1.9", "major"), "0.2.0")
        self.assertEqual(self.bump("0.0.3", "patch"), "0.0.4")
        self.assertEqual(self.bump("0.0.3", "minor"), "0.1.0")

    def test_stable_graduation(self):
        self.assertEqual(self.bump("0.9.4", "stable"), "1.0.0")
        # 已稳定时 stable 无操作
        self.assertEqual(self.bump("2.1.0", "stable"), "2.1.0")

    def test_none_keeps_core(self):
        self.assertEqual(self.bump("1.2.3", "none"), "1.2.3")


class TestPrerelease(unittest.TestCase):
    def bump(self, cur, level, pre):
        return str(next_version(Version.parse(cur), level, pre=pre)[0])

    def test_open_channel_from_stable(self):
        self.assertEqual(self.bump("1.1.0", "minor", "alpha"), "1.2.0-alpha.1")

    def test_same_stage_increments(self):
        self.assertEqual(self.bump("2.0.0-alpha.1", "major", "alpha"), "2.0.0-alpha.2")

    def test_stage_advance_resets_counter(self):
        self.assertEqual(self.bump("2.0.0-alpha.3", "major", "beta"), "2.0.0-beta.1")
        self.assertEqual(self.bump("2.0.0-beta.2", "major", "rc"), "2.0.0-rc.1")

    def test_cannot_skip_stage(self):
        with self.assertRaises(ValueError):
            next_version(Version.parse("2.0.0-alpha.1"), "major", pre="rc")

    def test_rc_to_final_drops_identifier(self):
        self.assertEqual(self.bump("2.0.0-rc.2", "major", None), "2.0.0")

    def test_breaking_change_during_alpha_majority(self):
        # alpha 通道内允许继续破坏而不必再抬 major
        self.assertEqual(self.bump("2.0.0-alpha.1", "major", "alpha"), "2.0.0-alpha.2")


class TestChangeAnalysis(unittest.TestCase):
    def run_an(self, cur, changes, **kw):
        return analyze(Version.parse(cur), changes, **kw)

    def test_takes_max_level(self):
        r = self.run_an("1.2.3", ["docs: 改 README", "feat: 加导出接口", "fix: 修空指针"])
        self.assertEqual(r["level"], "minor")
        self.assertEqual(r["next"], "1.3.0")

    def test_breaking_overrides_type(self):
        r = self.run_an("1.2.3", ["feat: 重写解析器签名", "break: parse() 参数顺序变更"])
        self.assertEqual(r["level"], "major")
        self.assertTrue(r["has_breaking"])
        self.assertEqual(r["next"], "2.0.0")

    def test_at_prefix_form(self):
        r = self.run_an("1.2.3", ["feat@break: 换掉底层驱动"])
        self.assertEqual(r["level"], "major")

    def test_docs_only_no_release(self):
        r = self.run_an("1.2.3", ["docs: 补示例", "chore: 格式化"])
        self.assertEqual(r["level"], "none")
        self.assertEqual(r["next"], "1.2.3")

    def test_deprecate_is_minor(self):
        r = self.run_an("1.2.3", ["deprecate: 旧 API 标注即将移除"])
        self.assertEqual(r["level"], "minor")
        self.assertEqual(r["next"], "1.3.0")

    def test_0x_breaking_lands_on_minor(self):
        r = self.run_an("0.3.1", ["break: 移除 legacy 入口"])
        self.assertEqual(r["next"], "0.4.0")

    def test_revert_of_breaking_is_major(self):
        r = self.run_an("2.0.1", ["revertbreak: 撤回 2.0 的字段重命名"])
        self.assertEqual(r["level"], "major")
        self.assertEqual(r["next"], "3.0.0")

    def test_plain_revert_is_patch(self):
        r = self.run_an("1.5.2", ["revert: 回滚上次的样式调整"])
        self.assertEqual(r["next"], "1.5.3")

    def test_every_result_is_parseable(self):
        cases = [("1.2.3", ["feat: a"]), ("0.1.0", ["break: b"]), ("2.0.0-rc.1", ["fix: c"]),
                 ("0.0.1", ["feat: d"]), ("3.4.5", ["docs: e"])]
        for cur, ch in cases:
            out = self.run_an(cur, ch)["next"]
            Version.parse(out)  # 不合法会抛异常
            self.assertIsInstance(out, str)


class TestHelpers(unittest.TestCase):
    def test_level_aliases(self):
        self.assertEqual(normalize_level("fix"), "patch")
        self.assertEqual(normalize_level("BREAKING"), "major")
        with self.assertRaises(ValueError):
            normalize_level("nonsense")

    def test_stable_is_reachable_via_normalize(self):
        """SKILL.md 文档推荐 `--level stable`，实现必须真的接受它。"""
        self.assertEqual(normalize_level("stable"), "stable")
        # 也要能从 CLI 走到
        v, _ = next_version(Version.parse("0.9.4"), normalize_level("stable"))
        self.assertEqual(str(v), "1.0.0")

    def test_stable_not_in_max_comparison(self):
        """stable 是显式动作，不能被 max() 当成比 major 更高的级别自动选出。"""
        self.assertNotIn("stable", LEVEL_ORDER)
        r = analyze(Version.parse("0.3.1"), ["feat: 新功能"])
        self.assertEqual(r["level"], "minor")

    def test_unknown_type_lists_options(self):
        try:
            level_for_change("wibble: 东西")
        except ValueError as e:
            self.assertIn("feat", str(e))
        else:
            self.fail("应当抛出 ValueError")

    def test_empty_spec_rejected(self):
        with self.assertRaises(ValueError):
            level_for_change("   ")

    def test_determinism(self):
        """同一输入必须永远同输出——这是本技能"唯一建议"承诺的基础。"""
        args = ("1.4.7", ["feat: 新增导出", "fix: 边界条件"])
        outs = {analyze(Version.parse(args[0]), args[1])["next"] for _ in range(50)}
        self.assertEqual(len(outs), 1)


class TestI18n(unittest.TestCase):
    """跨语言：reasons/错误文案随 --lang 变，但版本号与级别不变。"""

    def test_keys_match_between_locales(self):
        import json as _json
        import os as _os
        base = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "locales")
        with open(_os.path.join(base, "en.json"), encoding="utf-8") as f:
            en = _json.load(f)
        with open(_os.path.join(base, "zh.json"), encoding="utf-8") as f:
            zh = _json.load(f)
        self.assertEqual(set(en), set(zh))

    def test_reasons_localized_but_version_stable(self):
        set_lang("zh")
        r_zh = analyze(Version.parse("1.2.3"), ["feat: 新功能", "fix: 修 bug"])
        set_lang("en")
        r_en = analyze(Version.parse("1.2.3"), ["feat: new feature", "fix: fix bug"])
        # 版本号与级别与语言无关
        self.assertEqual(r_zh["next"], r_en["next"])
        self.assertEqual(r_zh["level"], r_en["level"])
        # 文案随语言变化
        self.assertNotEqual(r_zh["reasons"], r_en["reasons"])
        self.assertTrue(any("向后兼容" in s for s in r_zh["reasons"]))
        set_lang("en")  # 还原，避免影响其他用例

    def test_error_message_localized(self):
        set_lang("zh")
        try:
            level_for_change("wibble: x")
        except ValueError as e_zh:
            zh_msg = str(e_zh)
        set_lang("en")
        try:
            level_for_change("wibble: x")
        except ValueError as e_en:
            en_msg = str(e_en)
        self.assertNotEqual(zh_msg, en_msg)
        set_lang("en")

    def test_default_is_english(self):
        set_lang("en")
        r = analyze(Version.parse("1.2.3"), ["feat: x"])
        blob = " ".join(r["reasons"]).lower() + " " + " ".join(
            c["why"] for c in r["changes"]).lower()
        self.assertIn("backward-compatible", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
