---
name: version-decide
slug: version-decide
version: 0.1.1
displayName: 版本号判定器
summary: 根据代码变更范围判定下一个 SemVer 语义化版本号，面向新手开发者，输出唯一且明确的版本建议。
tags: [版本号, semver, 语义化版本, 发版决策, 版本管理]
description: 根据代码变更范围判定下一个版本号（SemVer 语义化版本），输出唯一且明确的版本号建议。触发场景包括——用户问"这次改动该发什么版本""版本号怎么定""该升 major 还是 minor 还是 patch""这个改动算不算 breaking""0.x 阶段该升哪""要不要发预发布""alpha/beta/rc 怎么推进""回滚要不要升版本""废弃 API 算哪级""changelog 该怎么写版本"，或提到 semver、语义化版本、version bump、发版决策、版本递增时使用。内置确定性算术内核，同输入必得同输出。
when_to_use: |
  典型触发：「下一版加了新角色和新功能，该发什么版本？」
  「1.2.3 现在升到哪个版本合适？」
  「0.3.1 升 minor 还是 patch？」
  明确不触发（勿误用）：
  - CalVer / 日期版本号项目（版本号即发布年月，如 26.04、2026.08.19）——先确认方案再处理
  - 只问时间「v1.5.0 什么时候发的」——那是查询，不是发版决策
  - 问依赖安装命令「怎么装 v2 版本」——那是包管理问题，不是版本判定
  - 其他项目当前版本号是多少——只需读取，无需本技能
allowed-tools: Read, Grep, Glob, Bash(python*), AskUserQuestion
disable-model-invocation: false
user-invocable: true
---

# 版本号判定

给开发者（尤其新手）一个**唯一且明确**的版本号建议，并说明依据。

---

## ⚠️ 贯穿性硬约束（每轮必守，不得因上下文压缩而丢弃）

> 以下 5 条在**每一轮对话**都必须遵守。若上下文中已看不到本节，重新读取本文件。

1. **不替用户假设破坏性变更。** 版本号的全部意义是向依赖方作出兼容性承诺，猜错一次的成本是下游线上故障。凡涉及"是否破坏"，一律先问。
2. **不猜当前版本号。** 必须先从项目里查（见 Step 1）。查不到就问。禁止假设从 1.0.0 或 0.0.1 起算。
3. **一次只给一个版本号建议。** 若存在两种合理选择，标出主建议并说明另一种的成立前提，禁止罗列多个让用户自己挑。
4. **依据必须引用具体变更 + 标准条款号**（如 SemVer §8）。禁止用"因为是重大改动"这类空话代替推理。
5. **算术交给脚本。** 版本号一律用 `scripts/semver_check.py` 计算，禁止心推。脚本失败时把原始报错照原样呈现给用户，不得编造版本号。

---

## 执行流程（5 步，单轮闭环）

单次执行 = 走完 Step 1 → 5。任一步失败都走各自的兜底，**不得跳步，也不得靠下一轮重新读文件**。

### Step 1 — 拿齐 4 组输入

| 输入 | 怎么取 | 取不到时（兜底） |
|---|---|---|
| 当前版本号 | 查项目：见下方命令 | **问用户**："当前发的是哪个版本？" |
| 公共 API 边界 | 库看导出列表；应用问"哪些有外部用户在用" | 若项目从未声明 → 告知"先定义公共 API 再谈版本号"，本次不出数字 |
| 变更清单 + 是否破坏 | 用 AskUserQuestion 或直接提问 | 用户说不清 → **列出具体问句**逐项问，不要替其假设"应该不破坏" |
| 目标生态 / 发布通道 | 提问 | 用户不关心 → 假设 npm 语义，但**必须在输出里注明此假设** |

查版本号的命令（按优先级）：

```bash
cat package.json 2>/dev/null | grep '"version"'          # npm / Node
cat pyproject.toml 2>/dev/null | grep -i version          # Python
grep -m1 '^VERSION' Makefile 2>/dev/null                  # C/C++
git tag --sort=-v:refname 2>/dev/null | head -5           # git tag
grep -rnoE "v[0-9]+\.[0-9]+\.[0-9]+" --include=*.md . | head   # 版本注释/文档
```

**破坏性必查六项**（逐条问，不能默认不破坏）：
① 公共导出被移除/重命名？② 函数签名（参数增删/顺序/类型/返回）变了吗？
③ **原本能成功的调用现在会失败或抛异常吗？** ④ 配置项/环境变量/命令行参数/默认值变了吗？
⑤ 依赖最低版本要求提高了吗？⑥ 类型或字段被收窄了吗？

第 ⑥ 项最易漏——泛型约束收紧、入参类型收窄，即使编译能过也是破坏性。
第 ③ 项最易轻描淡写——语义变化比签名变化更隐蔽。

**⛔ 若 Step 1 发现项目是 CalVer**（主版本段 ≥20 且像年份，如 `26.04`）：停下来告知用户
本技能不适用，不要套用 SemVer 规则。

### Step 2 — 定级

| 变更 | 级别 |
|---|---|
| 移除/重命名公共导出、签名变化、行为改变、配置项变更、删除模块 | **major** |
| 标记废弃（仍可用）、新增公共 API、依赖约束放宽 | **minor** |
| 内部重构、性能优化（行为等价）、修 bug、安全修复（不改 API） | **patch** |
| 纯文档、测试、CI、格式 | **none**（不需发版） |

同批变更**按 major > minor > patch 的优先序判定**：`feat + fix` → minor；`feat + break` → major。
标记废弃必须是 minor（SemVer §7 强制），即使尚未移除。
完整表与灰区见 `references/decision-tables.md`。

**兜底**：判不准某项是否破坏 → 按 major 判并明示"若确认不破坏则降为 minor"，
把判断权交回用户，不擅自乐观。

### Step 3 — 计算

```bash
python scripts/semver_check.py analyze <当前版本> --changes "变更1" "变更2" ...
```

变更描述语法（`scripts/semver_check.py` 的实际格式）：

| 写法 | 级别 |
|---|---|
| `feat: 新增 JSON 输出` / `feature:` | minor |
| `fix:` / `bugfix:` / `perf:` / `security:` / `refactor:` | patch |
| `docs:` / `test:` / `ci:` / `chore:` / `style:` / `build:` | none |
| `deprecate: 旧 API 标注废弃` | minor |
| `revert: 回滚样式调整` | patch |
| `revertbreak: 撤回 2.0 的字段重命名` | major |
| `break: parse() 参数顺序变更` | **强制 major** |
| `feat@break: 换掉底层驱动` | **强制 major** |

参数：`--pre alpha|beta|rc`（开/推进预发布）｜`--pre alpha.2`（指定序号）
｜`--build 20261008.a1`（构建元数据，不参与优先级）｜`--rebase`（预发布线内改投目标版本号）

其他子命令：`check <ver>`（校验+生态提示）｜`next <ver> --level <lvl>`
（`lvl` 含 `stable`，用于 0.x 毕业）｜`explain`（只要理由链）｜`compare a b`（优先级）

**兜底**：脚本报错时，把错误原文给用户并按错误提示修正输入——
**严禁**绕过脚本手算版本号（那正是本技能要消除的不确定性）。

### Step 4 — 输出

固定四段式：结论 → 依据 → 待确认 → 下一步命令。

```
版本号建议：1.3.3 → 1.4.0

判定：minor（向后兼容的新增功能）

依据：
- 新增角色杨廷和 → 新增内容，向后兼容，minor（SemVer §7）
- 新增成就系统 → 同上
- 按 major > minor > patch 的优先序 → minor，patch 归零

需要你确认：
- 是否复用现有 localStorage 存档 key？（复用会导致老玩家进度错乱，可能构成 breaking）

下一步：
- python scripts/semver_check.py next 1.4.0 --level minor
```

有未确认项（灰区：安全修复、性能重构、抬高环境要求、存档格式变更）**必须明说**，
灰区清单见 `references/decision-tables.md` 第四节。

### Step 5 — 收尾

- 若结论依赖某个未确认假设 → 在输出末尾单列"本次假设"写明。
- 若用户追问依据 → 引 `references/standards-comparison.md` 的标准条文，不复述本文件。
- 若判定卡在 0.x 毕业这类需要人拍板的动作 → 明确告知"这需要你确认 API 已冻结，我不能替你决定"。

---

## 高频场景速查

| 情况 | 结论 |
|---|---|
| **0.x 阶段** | 采用 npm/Cargo 语义：**minor 递增即破坏边界**（`^0.2.3` 上界是 `0.3.0`）。故 `0.3.1` 加功能 → `0.4.0`，不是 `0.3.2` |
| **0.x → 1.0.0** | 不自动推导。是显式毕业动作 `--level stable`，含义是"从此 API 稳定"，须由人确认 API 已冻结 |
| **预发布线内** | core 不变（SemVer §9：预发布不保证满足正式版兼容性）→ alpha 阶段继续破坏**无需**抬 major。只能逐级 alpha→beta→rc→正式，跳级被脚本拒绝 |
| **回滚非破坏性变更** | patch |
| **回滚破坏性变更** | **major**——兼容性被二次打破，依赖方刚恢复又被打破 |
| **废弃 API** | minor（规范强制），移除时再 major。给下游一个 minor 周期当迁移窗口 |
| **改 README / 注释** | 不需发版 |
| **已发布版本号要改** | 禁止（§3），只能发新版本号 |
| **并存多版本** | Go v2+ 必须改模块路径（`/v2`）；npm 用 dist-tag（`next`/`beta`）；Python 靠虚拟环境；Rust 同包名可解析多版本 |

### SemVer 之外

- **Python（PEP 440）**：预发布**无连字符**——`1.0.0rc1` 而非 `1.0.0-rc.1`。用 `check` 可检出。epoch（`1!1.0`）仅在切换编号方式时用。
- **CalVer**：见 Step 1 的 ⛔，不适用本技能。

---

## 文件

| 文件 | 用途 | 何时读 |
|---|---|---|
| `references/decision-tables.md` | 完整判定表、确认清单、边界情况、灰区 | 判不准某项时 |
| `references/standards-comparison.md` | SemVer / Conventional Commits / PEP 440 / Cargo / Go / CalVer 官方条文与冲突点 | 用户追问依据时 |
| `scripts/semver_check.py` | 判定算术内核（零依赖） | Step 3 必用 |
| `scripts/test_semver_check.py` | 33 项回归测试 | 改脚本后 |
| `scripts/measure_skill.py` | 量测本文件合规指标 | 改本文件后 |
