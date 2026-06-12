from setuptools import setup, find_packages

setup(
    name="devagent",
    version="3.4.0",
    description="DevAgent: Unified LLM-based Software Engineering Agent — Docker-Ready",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="DevAgent Team",
    packages=find_packages(include=["devagent", "devagent.*"]),
    include_package_data=True,
    install_requires=[
        "requests>=2.28.0",
        "pyyaml>=6.0",
    ],
    extras_require={
        "api": [
            "fastapi>=0.104.0",
            "uvicorn[standard]>=0.24.0",
            "pydantic>=2.0.0",
            "websockets>=12.0",
        ],
        "quality": [
            "ruff>=0.1.0",
            "mypy>=1.0",
            "bandit>=1.7",
            "coverage>=7.0",
            "pytest>=7.0.0",
            "pytest-cov>=5.0",
        ],
        "all": [
            "fastapi>=0.104.0",
            "uvicorn[standard]>=0.24.0",
            "pydantic>=2.0.0",
            "websockets>=12.0",
            "ruff>=0.1.0",
            "mypy>=1.0",
            "bandit>=1.7",
            "coverage>=7.0",
            "pytest>=7.0.0",
            "pytest-cov>=5.0",
            "pygls>=1.3.0",
            "Pillow>=10.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "agent=devagent.cli.main:main",
            "devagent=devagent.cli.main:main",
            "devagent-api=devagent.api.app:run_server",
            "devagent-lsp=devagent.lsp.server:main",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development",
        "Topic :: Software Development :: IDEs",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)
