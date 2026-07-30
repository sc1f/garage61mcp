#!/usr/bin/env python3
"""Setup script for garage61-mcp package."""

from setuptools import setup

setup(
    name="garage61-mcp",
    version="1.0.0",
    # Sources live in src/ but install as the `garage61_mcp` package.
    package_dir={"garage61_mcp": "src"},
    packages=["garage61_mcp"],
    install_requires=[
        "httpx>=0.25.0",
        "pydantic>=2.0.0",
        "python-dotenv>=1.0.0",
        # mcp 2.x removed the Server decorator API this server uses.
        "mcp>=1.0.0,<2.0.0"
    ],
    entry_points={
        "console_scripts": [
            "garage61-mcp=garage61_mcp.__main__:main",
        ],
    },
    python_requires=">=3.10",
    description="MCP server for Garage61 iRacing telemetry integration",
    author="Claude Code",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)