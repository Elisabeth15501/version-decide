#!/usr/bin/env python3
"""量测 SKILL.md 的合规指标：description 长度、token 估算、分区 token 预算。

用途：重构前后对比，确保 description 不超 1536 字符上限、正文留足压缩余量。
用法：python measure_skill.py <SKILL.md 路径>
"""
import io
import re
import sys


def cjk_ratio(text):
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk, len(text) - cjk


def est_tokens(text):
    """粗估 token：中文约 1.5 字符/token，ASCII 约 4 字符/token。"""
    cjk, other = cjk_ratio(text)
    return int(cjk / 1.5 + other / 4)


def main():
    path = sys.argv[1]
    text = io.open(path, encoding="utf-8").read()
    front = text.split("---", 2)[1] if text.startswith("---") else ""

    def field(name):
        """取标量或块标量字段。块标量（| / >）按缩进收集到下一个非缩进行。"""
        m = re.search(r"^%s:\s*(\|.*|>.*)$" % re.escape(name), front, re.M)
        if m:
            block = re.search(r"^%s:\s*[|>][-+]?\d*$" % re.escape(name), front, re.M)
            if block:
                start = block.end()
                lines, pos = [], start
                rows = front[start:].splitlines()
                for row in rows:
                    if row.strip() and not row.startswith((" ", "\t")):
                        break
                    lines.append(row.strip())
                    pos += len(row) + 1
                return " ".join(x for x in lines if x).strip()
        m2 = re.search(r"^%s:\s*(.+)$" % re.escape(name), front, re.M)
        return m2.group(1).strip() if m2 else ""

    desc = field("description")
    when = field("when_to_use")

    body = text.split("---", 2)[2] if text.startswith("---") else text

    print("文件:", path)
    print("description 字符数: %d / 1536 上限  %s" % (
        len(desc), "OK" if len(desc) <= 1536 else "!! 超限"))
    print("when_to_use 字符数: %d" % len(when))
    print("两者拼接估算: %d / 1536  %s" % (
        len(desc) + len(when) + 1,
        "OK" if len(desc) + len(when) + 1 <= 1536 else "!! 超限"))
    print("正文 token 估算: 约 %d / 8000 上限  %s" % (
        est_tokens(body), "OK" if est_tokens(body) <= 8000 else "!! 超限"))
    print("压缩余量（8000 - 正文）: 约 %d token" % (8000 - est_tokens(body)))
    print("正文总行数: %d" % len(body.splitlines()))

    # 前 3000 token 预算内包含哪些一级小节
    lines = body.splitlines()
    acc, heads, cur = 0, [], None
    for ln in lines:
        if ln.startswith("## "):
            cur = ln.strip()
        acc += est_tokens(ln)
        if cur and cur not in heads and acc > 3000:
            break
        if cur and cur not in heads:
            heads.append(cur)
    print("\n--- 3000 token 预算内可容纳的一级小节 ---")
    for h in heads:
        print("  ", h)
    if heads and len(heads) < len([l for l in lines if l.startswith("## ")]):
        print("   ...（其余被截断，见下方'核心规则'是否已前置）")
    all_h = [l.strip() for l in lines if l.startswith("## ")]
    print("\n全部一级小节:")
    for h in all_h:
        print("  ", h)


if __name__ == "__main__":
    main()
