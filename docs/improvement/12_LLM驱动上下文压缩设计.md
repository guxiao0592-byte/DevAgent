# DevAgent V2 — LLM 驱动上下文压缩设计

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | LLM 驱动上下文压缩详细设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-21 |
| 优先级 | P2 |
| 前置依赖 | [03_智能上下文管理设计](./03_智能上下文管理设计.md) |

## 1. 问题陈述

当前 `HistoryCompressor` 使用启发式规则压缩历史，存在不足：
- 规则是静态的，无法自适应任务类型
- 可能丢失对当前任务重要的细节信息
- 无法识别关键决策点
- 不能生成结构化的历史摘要

## 2. 设计方案

### 2.1 自适应压缩策略

```
根据任务类型选择压缩策略：

┌──────────────┬──────────────────────────┐
│ 任务类型      │ 保留重点                  │
├──────────────┼──────────────────────────┤
│ bug fix      │ 错误信息、尝试过的修复、    │
│              │ 测试结果变化               │
├──────────────┼──────────────────────────┤
│ feature add  │ 文件创建列表、API 契约、    │
│              │ 依赖变更                   │
├──────────────┼──────────────────────────┤
│ refactor     │ 受影响的文件列表、          │
│              │ 破坏性变更位置              │
├──────────────┼──────────────────────────┤
│ exploration  │ 文件搜索模式、              │
│              │ 发现的关键符号              │
└──────────────┴──────────────────────────┘
```

### 2.2 LLMCompressor

```python
class LLMCompressor:
    """使用轻量 LLM 调用进行智能历史压缩"""

    COMPRESSION_PROMPTS = {
        "bug_fix": """Compress the following agent execution history for a BUG FIX task.
Preserve with HIGH priority:
- Exact error messages and assertion failures
- File paths and line numbers of edits
- Test results before and after each fix attempt
- Any fix that was tried and FAILED (to avoid repeating)

Drop:
- Exploratory grep searches that returned no results
- Files that were read but not modified
- Redundant test runs with unchanged results

Format as a structured summary with sections: Discoveries, Attempts, Current State.""",

        "feature_add": """Compress the following agent execution history for a FEATURE ADDITION task.
Preserve with HIGH priority:
- New files created and their purpose
- API contracts and function signatures added
- Dependencies installed
- Test coverage added

Drop:
- Search patterns that didn't match
- Internal implementation details of reading standard library files
- Minor formatting changes""",

        "refactor": """Compress the following agent execution history for a REFACTORING task.
Preserve with HIGH priority:
- Files affected by the refactor
- Breaking changes introduced
- Before/after interface diffs

Drop:
- Temporary intermediate states
- Unchanged code that was merely read"""
    }

    def __init__(self, llm_client):
        self.llm = llm_client

    def compress(self, actions: list[dict], observations: list[dict],
                 task_type: str = "bug_fix",
                 recent_rounds: int = 5) -> str:
        if len(actions) <= recent_rounds:
            return ""  # Not enough history to compress

        to_compress = actions[:-recent_rounds]
        obs_to_compress = observations[:-recent_rounds]

        # Build compact representation for LLM
        history_text = self._format_for_compression(to_compress, obs_to_compress)

        # Estimate tokens; skip LLM call if very short
        if self._estimate_tokens(history_text) < 500:
            return self._rule_based_compress(to_compress, obs_to_compress)

        prompt = self.COMPRESSION_PROMPTS.get(
            task_type, self.COMPRESSION_PROMPTS["bug_fix"]
        )

        try:
            summary = self.llm.chat(
                messages=[{"role": "user", "content": history_text}],
                system_prompt=prompt,
                max_tokens=400  # 压缩目标：短摘要
            )
            return summary
        except Exception:
            return self._rule_based_compress(to_compress, obs_to_compress)

    def _format_for_compression(self, actions: list[dict],
                                 observations: list[dict]) -> str:
        """格式化为紧凑的 JSONL 风格"""
        lines = []
        for a, o in zip(actions, observations):
            tool = a.get("tool", "?")
            params = str(a.get("params", {}))[:100]
            success = "OK" if o.get("success") else "FAIL"
            key_output = self._extract_key_output(o, tool)
            lines.append(
                f"[{a.get('iteration', '?')}] {tool}({params}) → {success} {key_output}"
            )
        return "\n".join(lines)

    @staticmethod
    def _extract_key_output(obs: dict, tool: str) -> str:
        """提取关键输出信息"""
        if tool == "test_run":
            sr = obs.get("structured", {})
            return f"passed={sr.get('passed', '?')} failed={sr.get('failed', '?')}"
        elif tool == "grep_text":
            return f"found {obs.get('structured', {}).get('count', '?')}"
        elif tool in ("file_edit", "file_write"):
            return f"modified {obs.get('structured', {}).get('path', '?')}"
        elif tool == "file_read":
            return f"read {obs.get('structured', {}).get('path', '?')}"
        elif not obs.get("success"):
            return f"error: {obs.get('error', '')[:80]}"
        return ""
```

### 2.3 分层压缩触发策略

```python
class CompressionScheduler:
    """根据上下文压力决定何时触发压缩"""

    def __init__(self, max_context_tokens: int = 8000):
        self.max_tokens = max_context_tokens

    def should_compress(self, context: str,
                        history_length: int) -> tuple[bool, str]:
        """返回 (是否压缩, 压缩级别)"""
        tokens = self._estimate_tokens(context)

        if tokens > self.max_tokens * 0.9:
            return True, "aggressive"  # 激进压缩：仅保留最近2轮
        elif tokens > self.max_tokens * 0.7:
            return True, "moderate"    # 中度压缩：保留最近5轮，压缩早期
        elif history_length > 15:
            return True, "light"       # 轻度压缩：早期轮次摘要
        return False, "none"
```

### 2.4 关键决策点保护

```python
class KeyDecisionDetector:
    """识别不应被压缩的关键决策点"""

    KEY_PATTERNS = [
        # (condition, label)
        (lambda a, o: a.get("tool") == "test_run"
         and o.get("structured", {}).get("passed", 0) > 0
         and o.get("structured", {}).get("failed", 0) == 0,
         "FIRST_PASSING_TEST"),            # 首次测试通过 — 里程碑

        (lambda a, o: a.get("tool") in ("file_edit", "file_write")
         and o.get("success"),
         "CODE_CHANGE"),                   # 每次成功的代码修改

        (lambda a, o: not o.get("success")
         and o.get("error", ""),
         "FAILURE"),                       # 每次失败

        (lambda a, o: a.get("tool") == "submit",
         "SUBMIT"),
    ]

    @classmethod
    def detect(cls, action: dict, observation: dict) -> list[str]:
        labels = []
        for condition, label in cls.KEY_PATTERNS:
            if condition(action, observation):
                labels.append(label)
        return labels
```

### 2.5 配置

```yaml
context:
  compression:
    strategy: "adaptive"  # adaptive | llm | rule_based
    scheduler:
      max_tokens: 8000
      aggressive_threshold: 0.9
      moderate_threshold: 0.7
    llm_compressor:
      enabled: true
      model: "deepseek-chat"  # 使用便宜的模型做压缩
      max_compression_tokens: 400
    key_decisions:
      protect: true  # 关键决策点永不压缩
      labels: ["FIRST_PASSING_TEST", "CODE_CHANGE", "FAILURE"]
```

## 3. 评估指标

| 指标 | 当前(规则压缩) | 目标(LLM压缩) |
|------|-------------|-------------|
| 关键信息保留率 | ~85% | > 95% |
| 压缩比 | 3:1 | 5:1+ |
| 压缩耗时 | <1ms | <500ms |
| 长任务稳定性 | 中等 | 高 |
