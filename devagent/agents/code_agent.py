"""Professional code generation agent with multi-step project scaffolding.

Produces production-grade project output:
1. Project scaffold (pyproject.toml, requirements.txt, Dockerfile, .env.example, Makefile)
2. Core domain models with type hints, docstrings, and validation
3. Service/repository layer with error handling and logging
4. API endpoints (FastAPI) if applicable
5. Configuration management with environment variables
6. Entry points and CLI
"""

import json
import os
from .base_agent import BaseAgent
from ..agent_core.state import AgentState
from ..tools.quality import run_quality
from ..tools.artifact_registry import ArtifactRegistry
from ..agent_core.schemas import Artifact as ArtifactModel


CODE_PROMPT = """You are a senior software engineer generating a production-ready Python project.

===== CRITICAL STRUCTURE RULES =====
1. **PICK ONE STRUCTURE: flat OR package. NEVER BOTH.**
2. **All files in a module group must share the same structure.**
3. **No file/directory name collision.**
4. **All internal imports must be self-consistent.**
5. **Cross-check every import statement** — ensure the imported name actually exists.

===== ENGINEERING STANDARDS =====
1. **Type hints** on all function signatures (parameters AND return type)
2. **Comprehensive docstrings** (Google style) on all public APIs — purpose, args, returns, raises
3. **Proper error handling** with custom exception hierarchy (see exceptions.py)
4. **Structured logging** — use `logging` with module-level loggers, JSON format
5. **Configuration management** — pydantic-settings with env var overrides
6. **Clean Architecture**: domain logic / application services / infrastructure / presentation
7. **Dependency injection** — pass dependencies via constructors, not global state
8. **Input validation** at ALL boundaries (API, CLI, file I/O, DB queries)
9. **Async/await** for all I/O operations
10. **SOLID Principles**: Single Responsibility, Open/Closed, Liskov, Interface Segregation, Dependency Inversion

===== 🔒 SECURITY RULES (MANDATORY) =====
S1. NEVER use `eval()`, `exec()`, `pickle.loads()` on untrusted data
S2. NEVER use `shell=True` in subprocess calls — use list arguments
S3. NEVER concatenate user input into SQL — use parameterized queries
S4. NEVER hardcode secrets (API keys, passwords, tokens) — use env vars
S5. Hash passwords with `bcrypt` or `argon2` — NEVER MD5/SHA for passwords
S6. Generate `.env.example` with PLACEHOLDER values (not real secrets)
S7. Validate and sanitize ALL file paths — use `os.path.realpath()` checks
S8. All log entries: NEVER include passwords, tokens, or PII
S9. Secrets/tokens: use `secrets.token_urlsafe()` for cryptographic randomness
S10. Add `@require_auth` decorator or middleware for protected endpoints

===== 📊 OBSERVABILITY RULES =====
O1. Every service MUST expose `/health` endpoint with dependency status
O2. Use structured (JSON) logging: timestamp, level, module, message, request_id
O3. Log at service boundaries: request received → processing → response sent
O4. Catch exceptions at boundaries, log with traceback, return safe error response
O5. Propagate `X-Request-ID` header through all service calls

===== 🏗️ DESIGN PATTERNS TO APPLY =====
- Repository pattern for data access (abstract persistence)
- Factory pattern for complex object creation
- Strategy pattern for swappable algorithms
- Decorator pattern for cross-cutting concerns (auth, logging, caching)
- Circuit Breaker for external service calls (retry + timeout + fallback)

===== ⚠️ ANTI-PATTERNS TO AVOID =====
- God Class (>300 lines, >10 methods) → Split by responsibility
- Long Method (>50 lines) → Extract helper methods
- Magic Numbers → Named constants (UPPER_CASE at module level)
- Deep Nesting (>3 levels) → Early returns + extract methods
- Bare `except:` → Always specify exception types
- `except Exception: pass` → At minimum, log the error
- Circular imports → Extract shared interface/abc
- Hard-coded configuration → Environment variables

===== PROJECT STRUCTURE =====
```
project/
├── pyproject.toml          # metadata + build config
├── requirements.txt        # pinned production deps (== exact versions)
├── requirements-dev.txt    # dev deps (pytest, black, ruff, mypy)
├── Dockerfile              # multi-stage build, non-root user
├── .env.example            # template with PLACEHOLDER values
├── .gitignore
├── Makefile                # install, test, lint, format, run, clean
├── src/
│   ├── __init__.py
│   ├── config.py           # pydantic-settings, env var loading
│   ├── exceptions.py       # custom exception hierarchy
│   ├── logging_config.py   # JSON structured logging setup
│   ├── models/             # domain entities (OR src/models.py — pick ONE)
│   ├── services/           # business logic
│   ├── repositories/       # data access layer
│   ├── api/                # FastAPI routes + middleware
│   └── main.py             # entry point with health check
└── tests/
    ├── conftest.py         # shared fixtures
    ├── test_models.py      # domain logic tests
    ├── test_services.py    # service tests
    └── test_api.py         # integration tests
```

===== ✅ MEASURABLE QUALITY CHECKLIST (auto-verified) =====
Before outputting your code, VERIFY:
- [ ] 100% of public functions have type hints (params AND return)
- [ ] 100% of public classes/functions have docstrings
- [ ] 0 bare `except:` clauses (use `except SpecificError:`)
- [ ] 0 `print()` statements (use `logging` module)
- [ ] 0 lines > 120 characters
- [ ] No function > 50 lines (extract helpers)
- [ ] All imports are resolvable and self-consistent
- [ ] .env.example exists with placeholder values
- [ ] Dockerfile uses non-root USER
- [ ] Health check endpoint at `/health` returning status JSON

Output ONLY valid JSON:
{
  "project_structure": {
    "path/to/file.py": "purpose of this file"
  },
  "files": {
    "relative/file/path.py": "complete, production-grade source code",
    ".env.example": "environment template with PLACEHOLDER values",
    "Dockerfile": "multi-stage build with non-root user",
    "Makefile": "install, test, lint, format, run targets",
    "src/main.py": "entry point with health check + if __name__ guard"
  },
  "dependencies": {
    "production": ["fastapi>=0.100.0", "pydantic>=2.0.0"],
    "development": ["pytest>=7.0", "black", "ruff", "mypy"]
  },
  "setup_instructions": "step-by-step: venv → pip install → run",
  "usage_examples": ["curl http://localhost:8000/health", "python -m pytest"]
}

Generate complete, runnable, secure, production-grade code."""


JAVA_CODE_PROMPT = """You are a senior Java software engineer generating a production-ready Java project.

===== CRITICAL STRUCTURE RULES =====
1. Use Maven standard directory layout (src/main/java, src/test/java).
2. Package naming: all lowercase, no underscores (com.example.projectname).
3. One public class per .java file; filename MUST match the public class name.
4. All imports must be explicit (no wildcard imports).

===== ENGINEERING STANDARDS =====
1. **JavaDoc** on all public classes and methods (@param, @return, @throws)
2. **Proper exception handling** with custom exception hierarchy
3. **SLF4J/Logback** for structured logging
4. **JUnit 5 + AssertJ** for testing
5. **Clean Architecture**: controller → service → repository
6. **Dependency injection** via constructor injection (Spring or manual DI)
7. **Input validation** at ALL boundaries (Bean Validation / javax.validation)
8. **Immutability**: prefer `final` fields, immutable DTOs (Java 17+ records)
9. **Builder pattern** for complex object construction (Lombok @Builder or manual)
10. **SOLID Principles**: Single Responsibility, Open/Closed, Liskov, Interface Segregation, Dependency Inversion

===== 🔒 SECURITY RULES (MANDATORY) =====
S1. NEVER use `Runtime.exec()` with user input — use ProcessBuilder with sanitized args
S2. NEVER concatenate user input into SQL — use PreparedStatement or JPA parameterized queries
S3. NEVER hardcode secrets — use environment variables or external config
S4. Hash passwords with BCrypt (org.mindrot.jbcrypt or Spring Security)
S5. Enable CSRF protection for web endpoints
S6. Validate ALL file paths — use Path.normalize() + traversal checks
S7. All log entries: NEVER include passwords, tokens, or PII
S8. Use `java.security.SecureRandom` for cryptographic randomness (NOT Math.random())

===== 📊 OBSERVABILITY RULES =====
O1. Every service MUST expose `/health` endpoint (Spring Actuator or manual)
O2. Use structured (JSON) logging with MDC context propagation
O3. Log at service boundaries: request received → processing → response sent
O4. Catch exceptions at controller layer, log with traceback, return safe error response
O5. Propagate `X-Request-ID` header through all service calls

===== 🏗️ PROJECT STRUCTURE =====
```
project/
├── pom.xml                   # Maven build with dependencies
├── Makefile                  # build, test, run, clean targets
├── Dockerfile                # multi-stage build, non-root user
├── .env.example              # template with PLACEHOLDER values
├── .gitignore
├── src/
│   ├── main/java/com/example/app/
│   │   ├── App.java          # entry point (main method)
│   │   ├── config/           # configuration classes
│   │   ├── controller/       # REST controllers
│   │   ├── service/          # business logic services
│   │   ├── repository/       # data access layer
│   │   ├── model/            # domain entities / DTOs
│   │   └── exception/        # custom exceptions
│   └── main/resources/
│       ├── application.properties
│       └── logback.xml
└── src/test/java/com/example/app/
    ├── service/
    ├── controller/
    └── repository/
```

===== ✅ QUALITY CHECKLIST =====
- [ ] All public methods have JavaDoc
- [ ] 0 bare `catch (Exception e)` without logging
- [ ] 0 `System.out.println()` (use Logger)
- [ ] All resources use try-with-resources (AutoCloseable)
- [ ] pom.xml has explicit dependency versions (no ranges)
- [ ] Dockerfile uses non-root USER
- [ ] Health check endpoint at `/health`

Output ONLY valid JSON:
{
  "project_structure": {
    "path/to/file.java": "purpose of this file"
  },
  "files": {
    "pom.xml": "complete Maven POM with dependencies",
    "src/main/java/com/example/app/App.java": "entry point",
    "src/main/java/com/example/app/service/CalculatorService.java": "business logic",
    "src/test/java/com/example/app/service/CalculatorServiceTest.java": "JUnit 5 tests",
    ".env.example": "environment template",
    "Dockerfile": "multi-stage build",
    "Makefile": "build, test, run targets"
  },
  "dependencies": {
    "production": [
      {"groupId": "org.springframework.boot", "artifactId": "spring-boot-starter-web", "version": "3.2.0"}
    ],
    "test": [
      {"groupId": "org.junit.jupiter", "artifactId": "junit-jupiter", "version": "5.10.0"}
    ]
  },
  "setup_instructions": "step-by-step: mvn clean install → mvn spring-boot:run",
  "usage_examples": ["curl http://localhost:8080/health", "mvn test"]
}

Generate complete, runnable, secure, production-grade Java code."""


class CodeAgent(BaseAgent):
    """Agent for generating professional, production-grade project code.

    Supports: Python (default), Java.
    """

    def run(self, state: AgentState) -> AgentState:
        """Generate production-grade code from design or requirements."""
        language = getattr(state, 'language', 'python') or 'python'
        language = language.lower()

        if state.design_artifacts:
            source = json.dumps(state.design_artifacts, indent=2, ensure_ascii=False)
            context = "architecture design specification"
        elif state.requirements:
            source = json.dumps(state.requirements, indent=2, ensure_ascii=False)
            context = "requirements specification"
        else:
            state.add_error("code", "No design or requirements available for code generation")
            state.status = "IMPLEMENT_DONE"
            return state

        truncated = self._truncate_text(source, max_chars=8000)

        # Select language-appropriate prompt
        if language == "java":
            system_prompt = JAVA_CODE_PROMPT
            lang_label = "Java"
        else:
            system_prompt = CODE_PROMPT
            lang_label = "Python"

        # Step 1: Generate project scaffold and code
        result = self.llm.chat_structured(
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a production-ready {lang_label} project based on the following {context}:\n\n"
                    f"{truncated}"
                )
            }],
            system_prompt=system_prompt
        )

        files = result.get("files", {})
        project_structure = result.get("project_structure", {})
        dependencies = result.get("dependencies", {})
        generated_paths = []

        impl_dir = state.get_output_subdir("implementation")

        for rel_path, content in files.items():
            full_path = os.path.join(impl_dir, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            self.file_tool.write_text(full_path, content)
            generated_paths.append(full_path)
            # Register generated source file to central registry if available
            try:
                reg = getattr(state, "artifact_registry", None)
                if reg is not None:
                    ext = os.path.splitext(rel_path)[1]
                    if ext in (".py",):
                        fmt = "py"
                    elif ext in (".java",):
                        fmt = "java"
                    elif ext in (".xml",):
                        fmt = "xml"
                    elif ext in (".properties",):
                        fmt = "properties"
                    else:
                        fmt = "txt"
                    art = ArtifactModel(id=f"generated_{state.task_id}_{rel_path}",
                                        type="implementation:source",
                                        format=fmt,
                                        content=content,
                                        metadata={"filename": rel_path, "generated_by": "CodeAgent",
                                                  "language": language})
                    reg.register_from_state(state, "implementation", art)
            except Exception as e:
                state.add_warning("registry", f"Failed to register generated file {rel_path}: {e}")

        # Generate professional README
        readme = self._generate_readme(result, project_structure)
        self._save_artifact(state, "implementation", "README.md", readme)

        # Save dependency manifests
        prod_deps = dependencies.get("production", [])
        dev_deps = dependencies.get("development", [])
        req_content = "\n".join(prod_deps) + "\n"
        self._save_artifact(state, "implementation", "requirements.txt", req_content)

        if dev_deps:
            dev_req = "\n".join(dev_deps) + "\n"
            self._save_artifact(state, "implementation", "requirements-dev.txt", dev_req)

        # Post-generation validation: fix conflicting .py + directory names
        conflicts_fixed = self._fix_conflicting_structure(impl_dir)
        if conflicts_fixed:
            state.add_warning("code", f"Fixed {conflicts_fixed} file/directory naming conflict(s)")

        # Re-scan final file list after fixes
        final_files = []
        source_exts = (".py",) if language == "python" else (".java",)
        for root, dirs, files in os.walk(impl_dir):
            for f in files:
                if f.endswith(source_exts):
                    final_files.append(os.path.join(root, f))
        state.code_files = final_files

        # Run quality checks (format, lint, type) on the implementation directory
        if language == "python":
            try:
                quality_report = run_quality(impl_dir)
                registry = ArtifactRegistry(state.output_root or state.input_path or "outputs")
                qr_art = ArtifactModel(id=f"quality_{state.task_id}", type="quality:report", format="json",
                                       content=json.dumps(quality_report.to_dict(), ensure_ascii=False, indent=2),
                                       metadata={"generated_by": "CodeAgent", "provenance": [state.task_id], "filename": "quality_report.json"})
                registry.register_from_state(state, "implementation", qr_art)
            except Exception as e:
                # Do not fail the entire generation if quality tooling is missing or errors
                state.add_warning("quality", f"Quality checks failed: {e}")
        elif language == "java":
            # For Java, skip Python-specific linting (mvn checkstyle could be added later)
            state.add_trace("CodeAgent", "quality_skipped",
                           {"reason": "Java quality checks require Maven checkstyle (not implemented)"})

        state.status = "IMPLEMENT_DONE"
        state.add_trace("CodeAgent", "completed", {
            "files_generated": len(final_files),
            "directories": sorted(set(os.path.dirname(f) for f in final_files)),
            "conflicts_fixed": conflicts_fixed
        })

        return state

    @staticmethod
    def _fix_conflicting_structure(project_dir: str) -> int:
        """Fix cases where both X.py and X/ directory exist (shadowing issue).

        Python resolves 'from src.X import Y' by checking X/ package BEFORE X.py.
        When both exist, the import may fail. We resolve by removing the directory
        if the .py file has meaningful content, or merging if the directory is richer.

        Returns:
            Number of conflicts fixed
        """
        import shutil

        conflicts = 0
        for root, dirs, files in os.walk(project_dir):
            # Skip __pycache__
            dirs[:] = [d for d in dirs if d != '__pycache__']

            # Find basenames appearing as both .py and directory
            file_basenames = {f[:-3] for f in files if f.endswith('.py') and f != '__init__.py'}
            dir_basenames = set(dirs)
            conflicts_names = file_basenames & dir_basenames

            for name in conflicts_names:
                py_file = os.path.join(root, f"{name}.py")
                py_dir = os.path.join(root, name)
                init_file = os.path.join(py_dir, '__init__.py')

                # Check if the .py file has actual code (not just __init__.py re-exports)
                has_code = False
                try:
                    with open(py_file) as f:
                        content = f.read()
                        # Count lines of actual code (not comments/blanks)
                        code_lines = [l for l in content.split('\n')
                                      if l.strip() and not l.strip().startswith('#')]
                        has_code = len(code_lines) > 3  # More than just imports
                except OSError:
                    has_code = False

                # Check if directory has meaningful content beyond __init__.py
                dir_has_content = False
                try:
                    dir_items = [i for i in os.listdir(py_dir)
                                 if i != '__pycache__' and i != '__init__.py'
                                 or (i == '__init__.py' and os.path.getsize(init_file) > 50)]
                    dir_has_content = len(dir_items) > 0
                except OSError:
                    dir_has_content = False

                if has_code and not dir_has_content:
                    # .py has code, directory is empty boilerplate → remove directory
                    shutil.rmtree(py_dir)
                    conflicts += 1
                elif not has_code and dir_has_content:
                    # Directory has content, .py is boilerplate → remove .py
                    try:
                        os.remove(py_file)
                        conflicts += 1
                    except OSError:
                        pass
                elif has_code and dir_has_content:
                    # Both have content — merge .py into dir's __init__.py, remove .py
                    try:
                        with open(py_file) as f:
                            py_content = f.read()
                        if os.path.exists(init_file):
                            with open(init_file) as f:
                                init_content = f.read()
                            # Only merge if not already present
                            if py_content not in init_content:
                                with open(init_file, 'a') as f:
                                    f.write('\n# Merged from {}.py\n'.format(name))
                                    f.write(py_content)
                        else:
                            # __init__.py doesn't exist — create it with the .py content
                            with open(init_file, 'w') as f:
                                f.write('# Auto-merged from {}.py\n'.format(name))
                                f.write(py_content)
                        os.remove(py_file)
                        conflicts += 1
                    except OSError:
                        pass

        return conflicts

    def _generate_readme(self, result: dict, project_structure: dict) -> str:
        """Generate a professional README for the project."""
        setup = result.get("setup_instructions", "")
        examples = result.get("usage_examples", [])

        lines = []
        lines.append("# Project\n")
        lines.append("## Overview\n")
        lines.append("Auto-generated by DevAgent CodeAgent.\n")
        lines.append("## Quick Start\n")
        lines.append("### Prerequisites")
        lines.append("- Python 3.11+")
        lines.append("- pip\n")
        lines.append("### Installation\n")
        if setup:
            lines.append(f"```bash\n{setup}\n```\n")
        else:
            lines.append("```bash\n# Create virtual environment\npython -m venv venv\nsource venv/bin/activate  # On Windows: venv\\Scripts\\activate\n\n# Install dependencies\npip install -r requirements.txt\npip install -r requirements-dev.txt  # Development dependencies\n```\n")

        if examples:
            lines.append("### Usage\n")
            for ex in examples:
                lines.append(f"```bash\n{ex}\n```\n")

        lines.append("## Project Structure\n")
        lines.append("```")
        for path, desc in project_structure.items():
            lines.append(f"{path}  # {desc}")
        lines.append("```\n")

        lines.append("## Development\n")
        result_files = result.get("files", {})
        is_java = any(fp.endswith(".java") for fp in result_files if isinstance(result_files, dict))
        if is_java:
            lines.append("```bash\n# Build and test\nmvn clean test\n\n# Run\nmvn spring-boot:run\n\n# Package\nmvn clean package\n```\n")
        else:
            lines.append("```bash\n# Run tests\npytest tests/\n\n# Lint\nruff check src/\n\n# Format\nblack src/ tests/\n```\n")

        lines.append("## Docker\n")
        lines.append("```bash\n# Build\ndocker build -t devagent-project .\n\n# Run\ndocker run devagent-project\n```")

        return "\n".join(lines)
