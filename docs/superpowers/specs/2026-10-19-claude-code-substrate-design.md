# Claude Code Substrate + sdcshield 适配器设计

日期：2026-10-19
状态：已与用户逐节确认
目标仓库：harness2（本仓库）

## 0. 决策记录

| 决策点 | 选择 |
|---|---|
| 系统落点 | 直接完整集成进 harness2（新 substrate + 新 benchmark adapter，方法层零改动） |
| 任务流 | 重放 sdcshield 历史 PR（263+ 个） |
| 评测信号 | L1 编译 + L2 bats + L3 板级（0201/0202 部署压测 + sdc-excite 激发用例） |
| 采纳方式 | 全自动采纳（机器门禁替代人工审查） |
| 防泄题 | 保留（三道墙，见 §4） |

## 1. 背景与目标

用户日常用 Claude Code + GLM 开发 sdcshield（SDC 检测工具，C/meson 项目）。目标：把
Harness² 的双层递归（任务级 Propose–Probe–Compose + harnessing 级经验库）移植到该工作流，
让 Claude Code 的外围 harness（CLAUDE.md、技能、子 agent、钩子、脚本）被自动、持续地优化。

闭源 Claude Code 不是障碍：Harness² 的前提是模型/框架权重不动、外围表面优化，而 Claude Code
的全部 harness 表面都是磁盘文件，可按 run 暂存替换。

## 2. 总体架构

```
┌──────────────── harness2 方法层（零改动，全复用）────────────────┐
│ recursion.py(PPC递归)  selection.py(对比选择)  library.py(经验库) │
│ schedule.py(调度)  ledger.py(审计)  judge.py(评分入口)            │
└────────────┬────────────────────────────────────┬───────────────┘
    SubstrateLike│(solver执行环)        BenchmarkAdapter│(任务+评分)
   ┌────────────▼─────────────┐      ┌───────────────▼──────────────┐
   │ substrate/claude_code.py │      │ benchmarks/sdcshield.py      │
   │ 新增 ~500行              │      │ 新增 ~350行                  │
   │ CLAUDE_CODE_SURFACE      │      │ PR重放任务流+三层评分        │
   │ materialise/execute/     │      │ L1编译 L2 bats L3 板级       │
   │ read_trajectory/preflight│      │ (0201/0202编排现有运维脚本)  │
   └────────────┬─────────────┘      └──────────────────────────────┘
                │ claude_cli 通道（已存在，core/proposer.py:210）
                ▼
     claude -p (GLM) ← proposer / selector / reflector 改进环
                │
                ▼ execute() 也走 claude -p (GLM) 在隔离 worktree 里干活
```

核心对称性：改进环与执行环都是 `claude -p` + GLM（"Claude Code 优化 Claude Code"）。

核心改动量：
- `core/spec.py`：`INSTALL_MODES` 增加 `"claude_workspace"`（1 行，纯校验枚举）；
- `substrate/__init__.py`：导出新 substrate；
- `config.py`：substrate/模型映射条目。
- 方法层逻辑零改动。

## 3. 组件映射（CLAUDE_CODE_SURFACE）

概念：树路径 = 提议者的编辑契约；staged 落点 = materialise 翻译成的 Claude Code 原生布局。

| 组件 | 树路径（编辑契约） | staged 落点（原生） | 说明 |
|---|---|---|---|
| c1 系统提示 | `CLAUDE.md`（always_on） | worktree 根 `CLAUDE.md` | 种子 = 用户现有 164 行 |
| c2 护栏 | `GUARDRAILS.md`（always_on） | 拼入 staged CLAUDE.md `<!-- c2 -->` 段 | 技能自动触发行写这里 |
| c3 记忆 | `MEMORY.md`（always_on） | 拼入 `<!-- c3 -->` 段 | |
| c4 工具指引 | `TOOL_GUIDANCE.md`（always_on） | 拼入 `<!-- c4 -->` 段 | 4 文件合计 cap 24,000 字符（同 opencode） |
| c5 子agent | `sub_agents/<n>/AGENT.md` | `.claude/agents/<n>.md` | 树路径被 `_res` 正则硬编码，翻译零核心改动；frontmatter 需 `description:` |
| c6 技能 | `.claude/skills/<n>/SKILL.md` | 原位，无需翻译 | skill_dir=".claude/skills" 匹配正则；frontmatter name+description 与 Claude Code 原生约定一致 |
| c7 脚本 | `scripts/<n>.py` | worktree 根 `scripts/` | 保留 RESERVED_SCRIPTS（read_doc.py/pack.py/unpack.py） |
| c8 钩子 | `.claude/settings.harness.json`（hooks_file） | `claude -p --settings` 注入 | 只许 `hooks` 键，见 §7 |

设计决策：
1. 4 个 always_on 文件拼接为单个 staged CLAUDE.md（注释分界），不用 `@import`——避免 import
   静默失败，token 预算集中可控；
2. `plugin_dir=None`（opencode 的 .ts 插件不适用），c8 用 hooks 表达——PreToolUse/PostToolUse
   是配置级强制，等价于"模型跳不过"的杠杆；
3. `base_harness()` 从 sdcshield 现状提取种子：CLAUDE.md → c1；AGENTS.md 不进编辑面（属于
   任务环境）。

SurfaceSpec 关键参数：`name="claude_code"`，`always_on=(CLAUDE.md, GUARDRAILS.md, MEMORY.md,
TOOL_GUIDANCE.md)`，`always_on_max=24_000`，`skill_dir=".claude/skills"`，
`autotrigger_file="GUARDRAILS.md"`，`subagent_requires_mode=False`，`plugin_dir=None`，
`hooks_file=".claude/settings.harness.json"`，`install_mode="claude_workspace"`。

## 4. 执行隔离模型

一次 rollout = 一个 worktree + 一套 overlay + 一次 headless 调用。

1. **任务环境**：`git worktree add` sdcshield @ 该 PR 的 base commit（merge-base）。
2. **harness overlay**：materialise() 暂存组合后的 CLAUDE.md、`.claude/skills/`、
   `.claude/agents/`（翻译自 sub_agents）、`scripts/`、`settings.harness.json`。
3. **全局配置隔离**：`CLAUDE_CONFIG_DIR=$run_dir/claude-home` 每 run 独立——防止用户
   `~/.claude` 全局技能/agents/记忆泄进探针（探针之间必须只有 harness 树不同）。GLM 凭据经
   `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` 环境变量透传。
4. **执行**：`claude -p --output-format stream-json --verbose
   --dangerously-skip-permissions --settings .claude/settings.harness.json --max-turns N`，
   stdin = 任务单，超时兜底。
5. **轨迹与交付**：stream-json → read_trajectory() 渲染共享
   `[ACTION]/[OBSERVATION]/[ERROR]` 语法（obs_trunc 截断）；worktree 的 git diff =
   deliverable，`delivered()` = 非空 diff，`deliverable_text()` = diff 内容。

### 防泄题三道墙

sdcshield 是公开仓库，PR 的真实 diff 可经网络查到，会污染对比选择。防线：

1. **天然墙**：worktree@base——git 历史看不到该 PR 的实现；
2. **固定 deny-list 钩子**：base 版 `settings.harness.json` 内置 PreToolUse 拒绝
   `WebFetch`/`WebSearch`、Bash 拦截 `gh`、`github.com`、`git fetch` 命令模式。该钩子列入
   **不可移除清单**：validate_tree 强制其不可删除、不可改为 allow；proposer 只能新增其他
   钩子。所有探针（base 与候选）一致携带，不影响对比公平性；
3. **环境清洗**：execute() 的 env 白名单剔除 `GH_TOKEN`/`GITHUB_TOKEN`/`GH_HOST` 等凭据，
   仅透传 GLM 端点变量与运行必需变量。

务实边界：不追求完全断网（curl 等通用通道不拦），deny-list 覆盖主要泄题通道
（gh / WebFetch / WebSearch / github.com 直连）。

## 5. PR 重放任务流（benchmarks/sdcshield.py）

| 契约方法 | 实现 |
|---|---|
| `tasks()` | 枚举 merged PR → `pr-<n>`，稳定排序（库可复现契约）；支持 `--only-domains` 过滤 |
| `domain_of()` | PR 标签/前缀 + 改动路径启发 → 域：`detector-cpu` / `detector-gpu` / `detector-idxd` / `build-ci` / `testing` / `docs` / `orchestration` / `pr-loop` 等 |
| `task_prompt()` | PR title + body，剥掉 review checklist（契约要求 criteria stripped），渲染成中文工作单 |
| `task_files()` | `None`（解决方案不进上下文） |
| `execute()` | 经 substrate 按 RunRequest 执行 |
| `base_harness()` | 从 sdcshield 现状提取种子树 |
| `delivered()` / `deliverable_files()` | 非空 diff / diff 内容 |

域内任务有序、域间并发（`--workers N`，schedule.py 现成语义）。

## 6. 三层评分（score）

```
L1 编译（分钟级）: meson setup builddir && ninja          → pass/fail + 警告数
L2 测试（分钟级）: bats sanity-check + yamltest 子集       → 通过率
L3 板级（小时级）: 0201/0202 部署 → sdc-stressng 压测 → sdc-excite 激发用例
                  → 部署健康 + 检出/无误报
```

- **probe 对比**（k 个候选 harness 间的选择）：L1+L2+轨迹行为对比——selection.py 的无
  ground truth 对比选择，快信号驱动；
- **采纳门禁**（组合后的最终 harness）：L1+L2+L3 全绿（0201 和 0202 双板都过）才自动采纳；
- **成本控制**：L3 只对每任务的组合后 harness 跑 1 次，不逐探针跑；两块板 = 2 个串行队列，
  `score()` 内实现 per-board 锁；
- **不重造轮子**：L3 编排复用现有运维资产：`sdc-fleet-orchestration/`、`connect-0202.sh`、
  sdc-stressng、sdc-excite 的部署/启停流程；
- `score()` 返回分层 dict（各层明细 + 汇总）；judge 失败 = `None`，不缓存为 0（契约）；
- BenchPrompts 约束：work 描述不得含 benchmark/evaluation/grader/rubric/score 字样，
  adapter 的提示写成工作描述。

## 7. 全自动采纳的安全机制

无人审查，安全靠四道机器门禁：

1. **编辑面白名单**（validate_tree）：
   - `settings.harness.json` 只许 `hooks` 键——`permissions`/`env` 键直接拒绝（红线：
     权限永不放开）；
   - hook 命令只能引用 harness 树内 `scripts/` 相对路径；
   - deny-list 钩子不可移除（见 §4）；
   - skill/agent 命名与 frontmatter 规则（name 匹配目录、description 必填）；
2. **采纳门禁**：L1+L2+L3 全绿（双板）；
3. **回归回滚**：每次采纳 = harness 树的一个 git commit（谱系可追）；守卫逻辑监测该域后续
   任务的 L1/L2，相对上一采纳点下滑超阈值 → 自动 revert；
4. **审计与隔离**：runs/ 留全链证据（patch、探针轨迹、评分明细、采纳理由），ledger/runmark
   现成；采纳只写 harness 树（独立 git 管理），永不碰 sdcshield 主仓。

## 8. 经验库与部署回日常

- **harnessing 级递归**：采纳后 reflector（claude_cli+GLM）把 harness + 证据支持的教训写入
  per-domain 经验库（library.py 现成，按 benchmark/executor/mode/budget/seed 隔离），下一
  任务的 proposer 先读库再提编辑；
- **起步参数**：`--k 1 --mode parallel` + 单 domain（先 `testing`）5 个 PR 试跑，稳定后升
  `--k 3`、开 sequential 对比；
- **部署落地面**：sync 脚本把采纳树同步到 sdcshield 的 `.claude/` + `CLAUDE.md`（支持
  `--dry-run`）——日常 Claude Code 立即用上优化成果，构成"优化闭源 Claude Code"的闭环。

## 9. 测试与验收

- **单测**（pytest）：4 个 always_on 拼接、`sub_agents/`→`.claude/agents/` 翻译、
  stream-json 轨迹解析、validate_tree 拒绝 permissions 键 / 拒绝移除 deny-list 钩子；
- **集成冒烟**：`--dry-run` 枚举任务 → 1 个 PR 的 `--k 1` 全流程（真实 GLM + 本机 L1/L2；
  `--skip-board` 先跑通，再开 L3）；
- **验收标准**：
  1. 单 PR 全链路跑通并产出首个自动采纳；
  2. 5-PR 集上优化 harness vs base harness 的 L2 通过率/轨迹质量有可测差异；
  3. sync 后日常 Claude Code 正常工作。
- `preflight()`：花钱前检查 claude CLI 可用、GLM 认证 ping 通、meson/bats 就位、0201/0202
  SSH 可达。

## 10. 实现清单

| 文件 | 动作 | 预估 |
|---|---|---|
| `harness2/substrate/claude_code.py` | 新增：CLAUDE_CODE_SURFACE + 5 方法契约 | ~500 行 |
| `harness2/benchmarks/sdcshield.py` | 新增：PR 重放 adapter + 三层评分编排 | ~350 行 |
| `harness2/core/spec.py` | 修改：INSTALL_MODES 加 `claude_workspace` | 1 行 |
| `harness2/substrate/__init__.py` | 修改：导出 | ~4 行 |
| `harness2/config.py` | 修改：substrate/模型映射 | ~5 行 |
| `tests/` | 新增：单测 | 按需 |
| sync 脚本（部署回日常） | 新增 | ~80 行 |

环境前提：sdcshield 本机可 meson 编译；0201/0202 SSH 可达；GLM 端点凭据在环境变量中。
