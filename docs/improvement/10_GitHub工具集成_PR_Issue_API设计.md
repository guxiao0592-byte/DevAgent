# DevAgent V2 — GitHub 工具集成 PR/Issue API 设计

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | GitHub 工具集成 — PR/Issue API 详细设计方案 |
| 文档版本 | v1.0 |
| 创建日期 | 2026-05-21 |
| 优先级 | P1 |
| 前置依赖 | [02_工具生态扩展详细设计](./02_工具生态扩展详细设计.md) |

## 1. 问题陈述

DevAgent 目前完全在本地文件系统上操作，缺少与真实软件开发工作流的集成。现代软件工程的核心流程——Issue 跟踪、PR 提交、Code Review——完全在 Agent 的视野之外。

## 2. 设计方案

### 2.1 工具总览

```
GitHub 工具集 (6个):
├── gh_issue_read    — 读取 Issue 详情和评论
├── gh_issue_comment — 在 Issue 上添加评论
├── gh_search_code   — GitHub 代码搜索
├── gh_pr_create     — 创建 Pull Request
├── gh_pr_comment    — 在 PR 上添加 Review 评论
└── gh_pr_diff       — 获取 PR 的 diff
```

### 2.2 认证机制

```python
class GitHubAuth:
    """GitHub 认证管理"""

    @staticmethod
    def get_client() -> Github:
        """获取认证的 GitHub 客户端，优先级：
        1. 环境变量 GITHUB_TOKEN
        2. gh CLI 已登录的 token
        3. .devagent/github.token 文件
        """
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            token = GitHubAuth._get_gh_cli_token()
        if not token:
            token = GitHubAuth._get_file_token()
        if not token:
            raise AuthError("GitHub token not found. Set GITHUB_TOKEN env var.")
        return Github(token)
```

### 2.3 工具实现

#### gh_issue_read

```python
class GitHubIssueRead(BaseTool):
    name = "gh_issue_read"
    description = "Read a GitHub issue by URL or owner/repo/number. Shows title, body, labels, and comments."
    parameters = {
        "issue": {
            "type": "string",
            "description": "Issue URL (https://github.com/owner/repo/issues/123) or owner/repo#123"
        }
    }

    async def execute(self, params, workspace):
        issue_ref = params["issue"]
        gh = GitHubAuth.get_client()

        # Parse: URL or owner/repo#123
        owner, repo, number = self._parse_issue_ref(issue_ref)
        issue = gh.get_repo(f"{owner}/{repo}").get_issue(number)

        output = f"""## Issue #{number}: {issue.title}
State: {issue.state} | Labels: {', '.join(l.name for l in issue.labels)}
Author: {issue.user.login}

{issue.body}

---
### Comments ({issue.comments})
"""
        for comment in issue.get_comments():
            output += f"\n**{comment.user.login}**: {comment.body[:500]}\n"

        return ToolResult(True, output[:4000],
                         structured={"number": number, "title": issue.title})
```

#### gh_pr_create

```python
class GitHubPRCreate(BaseTool):
    name = "gh_pr_create"
    description = "Create a Pull Request from the current branch. Use when the task is complete and you want to submit changes for review."
    parameters = {
        "title": {"type": "string", "description": "PR title (short, descriptive)"},
        "body": {"type": "string", "description": "PR description (changes summary, test plan, screenshots)"},
        "base": {"type": "string", "description": "Base branch (default: main)", "default": "main"},
        "draft": {"type": "boolean", "description": "Create as draft PR", "default": False}
    }

    async def execute(self, params, workspace):
        title = params["title"]
        body = params.get("body", "")
        base = params.get("base", "main")
        draft = params.get("draft", False)

        # Get current repo info from git remote
        remote_url = self._get_remote_url(workspace)
        if not remote_url:
            return ToolResult(False, error="No git remote 'origin' found")

        owner, repo = self._parse_remote(remote_url)
        head = self._get_current_branch(workspace)

        gh = GitHubAuth.get_client()
        repository = gh.get_repo(f"{owner}/{repo}")

        pr = repository.create_pull(
            title=title,
            body=body + "\n\n🤖 Generated with [DevAgent](https://github.com/devagent)",
            head=head,
            base=base,
            draft=draft
        )

        return ToolResult(True,
            f"PR created: {pr.html_url}\n#{pr.number}: {pr.title}",
            structured={"number": pr.number, "url": pr.html_url}
        )
```

#### gh_pr_comment

```python
class GitHubPRComment(BaseTool):
    name = "gh_pr_comment"
    description = "Add a review comment or inline code comment on a Pull Request."
    parameters = {
        "pr": {"type": "string", "description": "PR URL or owner/repo#number"},
        "body": {"type": "string", "description": "Comment body (Markdown supported)"},
        "path": {"type": "string", "description": "File path for inline comment (omit for general comment)"},
        "line": {"type": "integer", "description": "Line number for inline comment"}
    }

    # 实现略，类似 gh_pr_create
```

### 2.4 自动 PR 模板

```python
PR_TEMPLATE = """## Summary
{summary}

## Changes
{changes}

## Test Plan
{test_plan}

## Verification
- [ ] All tests pass ({passed}/{total})
- [ ] Lint clean
- [ ] No regression

## Screenshots
{screenshots}

---
🤖 Generated with [DevAgent](https://github.com/devagent) | Task: {task_id} | Iterations: {iterations}
"""
```

### 2.5 配置

```yaml
github:
  enabled: true
  auth:
    provider: auto  # auto | token | gh-cli
    token_env: "GITHUB_TOKEN"
  pr:
    default_base: "main"
    draft_by_default: true  # Agent 创建的 PR 默认是 draft
    auto_label: ["devagent"]
    reviewers: []  # 自动请求的 reviewer 列表
```

## 3. 完整开发流程示例

```
User: "Fix issue #42 about login timeout"

Agent:
  ST-01: gh_issue_read("owner/repo#42") → 理解 Issue
  ST-02: grep_text("login") → 定位代码
  ST-03: file_read("src/auth.py") → 理解逻辑
  ST-04: file_edit("src/auth.py", ...) → 修复
  ST-05: test_run("tests/test_auth.py") → 验证
  ST-06: gh_pr_create(
           title="fix: resolve login timeout (closes #42)",
           body="..."  ) → 提交 PR
  ST-07: gh_issue_comment("#42", "Fixed in #43") → 关联 Issue
```

## 4. 评估指标

| 指标 | 目标 |
|------|------|
| Issue → PR 端到端成功率 | > 60% |
| PR 描述质量（被合并率） | > 50% |
| Issue 理解准确率 | > 80% |
