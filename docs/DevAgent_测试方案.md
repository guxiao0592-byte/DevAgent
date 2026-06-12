# DevAgent 性能测试方案

> **版本**: 1.0 | **日期**: 2026-06-04 | **目标**: 量化评估 DevAgent 的软件工程能力

---

## 目录

1. [测试目标与维度](#1-测试目标与维度)
2. [方案一: SWE-bench 风格基准测试](#2-方案一-swe-bench-风格基准测试)
3. [方案二: 自定义 Pipeline 端到端评测套件](#3-方案二-自定义-pipeline-端到端评测套件)
4. [两种方案对比与协同](#4-两种方案对比与协同)
5. [使用方法](#5-使用方法)

---

## 1. 测试目标与维度

### 四维评测框架

```
┌─────────────────────────────────────────────────────────────┐
│                    DevAgent 四维评测                         │
├───────────────┬───────────────┬───────────────┬─────────────┤
│   正确性      │    完整性      │    效率性      │   鲁棒性    │
│  Correctness  │  Completeness │  Efficiency   │ Robustness │
├───────────────┼───────────────┼───────────────┼─────────────┤
│ 方案一+方案二  │   方案二       │  方案一+方案二  │  方案一     │
│               │               │               │             │
│ • Bug修复率   │ • 需求覆盖率   │ • 迭代次数     │ • 异常恢复   │
│ • 测试通过率  │ • 测试覆盖率   │ • 时间开销     │ • 幻觉频率   │
│ • 功能一致性  │ • 文档完整性   │ • Token消耗    │ • 边界情况   │
└───────────────┴───────────────┴───────────────┴─────────────┘
```

### 核心评测指标

| 指标 | 定义 | 目标值 |
|------|------|--------|
| **解析率 (Resolved Rate)** | 成功修复/完成的任务比例 | ≥30% |
| **@k 通过率** | 前 k 次尝试中的成功率 | k=1 时 ≥20%, k=3 时 ≥35% |
| **平均修复轮次** | 从失败到全部测试通过的平均修复循环数 | ≤3 |
| **测试覆盖率** | 生成代码的 line/branch 覆盖率 | line ≥85%, branch ≥80% |
| **文档完整度** | IEEE 报告包含的必需章节比例 | ≥90% |
| **平均耗时** | 单任务端到端平均耗时 | ≤5min |
| **Token 效率** | 每解决一个问题的平均 token 消耗 | ≤50K tokens |
| **幻觉率** | 引用不存在的文件/函数/API 的比例 | ≤5% |

---

## 2. 方案一: SWE-bench 风格基准测试

### 2.1 设计原理

SWE-bench 是目前业界评估 AI 软件工程 agent 的**黄金标准**。它从 GitHub 仓库收集真实的 issue → PR 对的修复任务，评估 agent 能否从 issue 描述定位 bug 并生成正确补丁。

我们的方案借鉴 SWE-bench 的核心设计，但将其适配为三阶段递增难度：

```
阶段 A: 单文件 Bug 修复 (debug_repair_cases)     ← 基础
阶段 B: 全流程开发任务 (analysis_design_cases)   ← 综合
阶段 C: 跨仓库开源 issue (swebench_external)     ← 进阶
```

### 2.2 测试用例体系

#### 阶段 A: 单文件 Bug 修复 (4 用例)

| ID | 类别 | 描述 | buggy.py | test_buggy.py | 难度 |
|----|------|------|----------|---------------|------|
| DR-01 | boundary | 除零错误未处理 | `buggy:8` `return a/b` | `test_divide_by_zero` 期望 ValueError | ⭐ |
| DR-02 | null | None 值未检查 | `buggy:5` `return data['key']` 无守卫 | `test_none_input` 期望 KeyError→返回值 | ⭐⭐ |
| DR-03 | logic | 排序逻辑反转 | `buggy:12` `>` 应为 `<` | `test_sort_descending` 结果反序 | ⭐⭐ |
| DR-04 | path | 文件路径未规范 | `buggy:15` `open(path)` 无 sanitize | `test_path_traversal` 路径逃逸 | ⭐⭐⭐ |

**评测指标**:
- Resolved Rate: 修复后全部 test 通过的比例
- Patch Correctness: diff 是否只改了必要行（不多不少）
- 时间效率: 从 issue 输入到 patch 输出的耗时

#### 阶段 B: 全流程开发任务 (8 用例)

| ID | 类别 | 输入 | 期望产出 |
|----|------|------|---------|
| AD-01 | 图书借阅 | `book_lending.md` → 领域模型 + 用例 + 架构图 |
| AD-02 | 选课系统 | `course_selection.md` → FR/NFR + DFD + ER 图 |
| AD-03 | 影院订票 | `cinema_booking.md` → C4 图 + 时序图 + API 合约 |
| AD-04 | Bug 追踪 | `bug_tracker.md` → ADR + 威胁模型 + 状态机 |
| IT-01 | 成绩管理 | `student_grade.md` → 代码 + pytest + 覆盖率报告 |
| IT-02 | Todo 应用 | `todo_app.md` → 完整 CLI + 测试 + README |
| IT-03 | 银行账户 | `bank_account.md` → 领域模型 + 事务 + 安全测试 |
| IT-04 | 文本统计 | `text_stats.md` → 流式处理 + 参数化测试 + Dockerfile |

#### 阶段 C: 跨仓库开源 Issue (可选扩展)

从 [verified subset](https://www.swebench.com/) 选取 10 个 Python issue，验证 DevAgent 在真实世界 Bug 上的表现。

### 2.3 测试执行框架

```
┌─────────────────────────────────────────────────────────┐
│              SWE-bench 风格测试执行器                     │
│                                                         │
│  1. _copy_workspace() ← 创建干净的工作副本               │
│  2. _run_baseline()   ← 记录初始测试状态 (bug baseline)   │
│  3. _execute_agent()  ← 运行 DevAgent (repair/full 模式)  │
│  4. _validate_fix()   ← 对黄金补丁做 diff 验证           │
│  5. _compute_metrics()← 汇总 Resolved/@k/Coverage        │
│  6. _generate_report()← 生成 Markdown + JSON 报告         │
└─────────────────────────────────────────────────────────┘
```

### 2.4 评测矩阵

```
输出文件:
  benchmark_report/
  ├── result.json          ← 完整结构化结果
  ├── summary.md           ← 人类可读总结
  ├── summary.csv          ← 表格数据
  ├── {case_id}/
  │   ├── agent_diff.patch ← Agent 生成的补丁
  │   ├── test_results.json← 修复后测试结果
  │   └── metrics.json     ← 用例级详细指标
  └── swe_lite_comparison.md ← 与业界对比 (SWE-agent, ACR, ...)
```

### 2.5 评分算法

```python
# 核心评分: 每个用例 0-100 分

def score_case(case_id, result):
    score = 0

    # 1. 修复正确性 (权重: 60%)
    if result['all_tests_pass']:
        score += 60
    elif result['tests_passed_ratio'] >= 0.5:
        score += 30

    # 2. 补丁质量 (权重: 20%)
    if result['patch_is_minimal']:      # 只改了必要行
        score += 10
    if result['patch_is_readable']:     # 补丁有注释/清晰
        score += 5
    if not result['introduced_regressions']:
        score += 5

    # 3. 效率 (权重: 20%)
    if result['duration_sec'] < 180:    # 3 分钟内
        score += 10
    elif result['duration_sec'] < 300:  # 5 分钟内
        score += 5
    if result['iterations'] <= 20:      # 20 轮以内
        score += 5
    if result['token_count'] <= 50000:  # 50K tokens 以内
        score += 5

    return score
```

---

## 3. 方案二: 自定义 Pipeline 端到端评测套件

### 3.1 设计原理

此方案不依赖外部基准，而是为 DevAgent **量身定制**一套完整的 Pipeline 质量评测维度，覆盖从需求到交付的全部 7 个阶段。

```
┌──────────────────────────────────────────────────────────┐
│         Pipeline 质量评测套件 (7 阶段 × 5 维度)            │
│                                                          │
│  Phase 1: 需求分析     ──── SRS 文档评测                  │
│  Phase 2: 架构设计     ──── SDD 文档 + 图表评测            │
│  Phase 3: 代码生成     ──── 代码质量 + 安全检查             │
│  Phase 4: 测试执行     ──── 测试覆盖率 + 变异测试           │
│  Phase 5: Bug 修复     ──── 修复正确性 + 补丁质量           │
│  Phase 6: 最终交付     ──── 报告完整度 + Dashboard 质量     │
│  Phase 7: 交互修改     ──── 响应准确率 + 迭代效率           │
└──────────────────────────────────────────────────────────┘
```

### 3.2 评测用例集

设计 **10 个覆盖不同领域的需求文档**，每个包含明确的验收标准和期望产出。

| ID | 项目 | 规模 | 核心测试维度 |
|----|------|------|------------|
| PE-01 | 计算器应用 | 小 | 基本全流程 |
| PE-02 | REST API 服务 | 中 | API 设计 + 安全测试 |
| PE-03 | CLI 数据处理工具 | 中 | 参数化测试 + 边界值 |
| PE-04 | 领域模型 + 仓库层 | 大 | 架构设计 + 测试覆盖率 |
| PE-05 | 状态机驱动系统 | 中 | 状态图 + 流程图 |
| PE-06 | 多用户认证系统 | 大 | 安全需求 + 威胁模型 |
| PE-07 | 事件驱动系统 | 中 | DFD + 时序图 |
| PE-08 | 缓存 + 数据库 | 大 | 部署图 + ADR |
| PE-09 | 文件解析器 | 小 | 错误处理 + 边界值 |
| PE-10 | Web Scraper | 中 | 依赖管理 + 可观测性 |

### 3.3 五维评分体系

每个 Pipeline 用例在五个维度上独立评分：

```
得分 = Σ(维度权重 × 阶段得分)

维度 1: 正确性 (40%) — 功能是否按需求实现
维度 2: 完整性 (25%) — 产物是否齐全
维度 3: 代码质量 (20%) — 代码是否符合工程标准
维度 4: 文档质量 (10%) — 报告是否达到专业水准
维度 5: 可维护性 (5%)  — 代码是否易于后续维护
```

#### 维度 1: 正确性 (40%)

| 检查项 | 方法 | 满分 |
|--------|------|------|
| 功能测试通过率 | pytest 全部通过 | 40 |
| 验收标准覆盖 | 手动对照需求文档 | 30 |
| 边界/错误处理 | 边界值测试 + 异常注入 | 20 |
| 回归无回退 | 多次运行一致性 | 10 |

#### 维度 2: 完整性 (25%)

| 检查项 | 方法 | 满分 |
|--------|------|------|
| 需求文档完整 | IEEE 830 章节统计 | 25 |
| 设计文档完整 | IEEE 1016 章节 + 图表数 | 25 |
| 源代码完整 | 文件数 ≥ 预期 | 20 |
| 测试覆盖完整 | 每个 public 函数 ≥1 测试 | 20 |
| 报告完整 | Dashboard + RTM + 建议 | 10 |

#### 维度 3: 代码质量 (20%)

| 检查项 | 方法 | 满分 |
|--------|------|------|
| ruff lint 零错误 | 自动检测 | 25 |
| 类型标注完整 | mypy strict mode | 25 |
| 安全扫描 | bandit / pip-audit | 20 |
| 复杂度控制 | radon (圈复杂度 < 10) | 15 |
| 文档字符串质量 | pydocstyle | 15 |

#### 维度 4: 文档质量 (10%)

| 检查项 | 方法 | 满分 |
|--------|------|------|
| 文档控制表 | 检查 ID/版本/日期/作者/状态 | 20 |
| 图表质量 | 每张图语法有效 + 语义正确 | 30 |
| 术语表 | 关键术语定义完整 | 20 |
| 追溯矩阵 | FR ↔ 设计 ↔ 代码 ↔ 测试 | 30 |

#### 维度 5: 可维护性 (5%)

| 检查项 | 方法 | 满分 |
|--------|------|------|
| 无循环依赖 | import-linter | 35 |
| 接口隔离 | 抽象类/接口统计 | 35 |
| 配置外部化 | .env 模板存在 + 无硬编码 | 30 |

### 3.4 自动化评测执行器

```python
class PipelineEvaluator:
    def __init__(self):
        self.dimensions = {
            "correctness":  {"weight": 0.40, "checks": [...]},
            "completeness": {"weight": 0.25, "checks": [...]},
            "code_quality": {"weight": 0.20, "checks": [...]},
            "doc_quality":  {"weight": 0.10, "checks": [...]},
            "maintainability": {"weight": 0.05, "checks": [...]},
        }

    def evaluate(self, case_id: str, output_dir: str) -> dict:
        """Run ALL checks against pipeline output.

        Steps:
          1. Parse SRS → check IEEE 830 completeness
          2. Parse SDD → check IEEE 1016 + diagram counts
          3. Run pytest + coverage → correctness + coverage
          4. Run ruff + mypy + bandit → code quality
          5. Parse executive report → document quality
          6. Compute weighted total score
        """
```

### 3.5 评测报告格式

```markdown
# DevAgent Pipeline 评测报告

## 总览
| 指标 | 值 |
|------|-----|
| 测试用例数 | 10 |
| 通过用例 | 7 |
| 平均总分 | 78.5/100 |
| 正确性均分 | 72.3/100 |
| 完整性均分 | 85.1/100 |
| 代码质量均分 | 76.8/100 |
| 文档质量均分 | 82.4/100 |
| 可维护性均分 | 71.2/100 |

## 各阶段评分

| 阶段 | 均分 | 最低分 | 最高分 |
|------|------|--------|--------|
| 需求分析 | 82 | 65 (PE-07) | 95 (PE-01) |
| 架构设计 | 78 | 54 (PE-03) | 92 (PE-06) |
| 代码生成 | 74 | 48 (PE-08) | 91 (PE-01) |
| 测试执行 | 68 | 35 (PE-05) | 88 (PE-02) |
| Bug 修复 | 81 | 60 (PE-04) | 96 (PE-09) |
| 最终交付 | 85 | 70 (PE-08) | 98 (PE-01) |
| 交互修改 | 72 | 50 (PE-06) | 89 (PE-03) |

## 常见失败模式
- 类型标注缺失: 6/10 用例 (mypy strict 失败)
- 安全扫描告警: 3/10 用例 (硬编码路径)
- 追溯矩阵不完整: 4/10 用例 (缺少 FR↔设计映射)
```

---

## 4. 两种方案对比与协同

| 维度 | 方案一 (SWE-bench 风格) | 方案二 (Pipeline 套件) |
|------|------------------------|------------------------|
| **目标** | 与业界对标，量化修复能力 | 全覆盖，量化全流程质量 |
| **用例数** | 4(DR) + 8(AD/IT) + 10(SWE) | 10 (定制) + 可扩展 |
| **评分方式** | 单维: Resolved/Fail | 五维: 正确性/完整性/代码质量/文档质量/可维护性 |
| **自动化** | 全自动 (pytest 验证) | 半自动 (部分需人工评估) |
| **外部对比** | ✅ 可对比 SWE-agent/ACR/Claude Code | ❌ 仅内部对比 |
| **覆盖阶段** | 修复 (repair) + 全流程 (full) | 全部 7 个阶段 |
| **运行耗时** | ~2h (含 SWE 外部用例) | ~1h (10 用例 × 6min) |
| **适用场景** | 论文/报告量化对比 | CI/回归测试/版本迭代 |

### 协同使用

```
开发迭代:
  每次 git push → 运行方案二 (快速, 10 用例, ~1h)
  每版本发布 → 运行方案一 (完整, 含外部对比)

论文/报告:
  方案一 → 量化对比表 (与 SWE-agent, ACR, Claude Code)
  方案二 → 深度分析 (各维度雷达图, 失败模式分布)
```

---

## 5. 使用方法

### 方案一执行

```bash
# 内置基准套件
python -m devagent.benchmarks.benchmark_runner

# 指定测试集
python -m devagent.benchmarks.benchmark_runner \
  --suite default \
  --output ./benchmark_report \
  --provider deepseek \
  --parallel 2

# 单用例调试
python -m devagent.benchmarks.benchmark_runner \
  --case DR-01 \
  --verbose
```

### 方案二执行

```bash
# 运行全部 10 个 Pipeline 用例
python -m devagent.benchmarks.pipeline_evaluator \
  --suite pipeline_v1 \
  --output ./pipeline_report

# 指定维度
python -m devagent.benchmarks.pipeline_evaluator \
  --suite pipeline_v1 \
  --dimensions correctness,code_quality \
  --output ./partial_report
```

### CI 集成

```yaml
# .github/workflows/benchmark.yml
benchmark:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v3
    - name: Quick Pipeline Test
      run: |
        python -m devagent.benchmarks.pipeline_evaluator \
          --suite quick --cases 3 --timeout 300
    - name: Full Benchmark (on release)
      if: startsWith(github.ref, 'refs/tags/v')
      run: |
        python -m devagent.benchmarks.benchmark_runner --suite default
```

---

> **后续计划**:
> - 实现 `PipelineEvaluator` 类 (`devagent/benchmarks/pipeline_evaluator.py`)
> - 扩展 `BenchmarkRunner` 支持外部 SWE-bench 用例
> - 集成到 GitLab CI
> - 建立历史基线数据库，支持趋势分析
