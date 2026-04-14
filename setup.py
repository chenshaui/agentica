# -*- coding: utf-8 -*-
import sys
from setuptools import setup, find_packages

__version__ = ""
exec(open('agentica/version.py').read())

if sys.version_info < (3, 10):
    sys.exit('Sorry, Python >= 3.10 is required.')

with open('README.md', 'r', encoding='utf-8') as f:
    readme = f.read()

setup(
    name='agentica',
    version=__version__,
    description='AI Agent SDK',
    long_description=readme,
    long_description_content_type='text/markdown',
    author='XuMing',
    author_email='xuming624@qq.com',
    url='https://github.com/shibing624/agentica',
    license="Apache License 2.0",
    zip_safe=False,
    python_requires=">=3.10.0",
    entry_points={
        "console_scripts": [
            "agentica = agentica.cli:main",
            "agentica-gateway = agentica.gateway.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Intended Audience :: Education",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    keywords='Agentica,Agent Tool,action,agent,agentica',
    install_requires=[
        "aiofiles",
        "typing_extensions>=4.0; python_version<'3.12'",
        "httpx",
        "loguru",
        "beautifulsoup4",
        "openai",
        "python-dotenv",
        "pydantic",
        "requests",
        "sqlalchemy",
        "scikit-learn",
        "markdownify",
        "tqdm",
        "rich",
        "prompt_toolkit>=3.0",
        "pyyaml",
        "tiktoken",
        "mcp",
        "puremagic",
        "qdrant-client",
        "langfuse",
        "python-frontmatter",
    ],
    packages=find_packages(exclude=['tests', 'tests.*', 'examples', 'examples.*', 'docs']),
    # Runtime data files: prompt templates (.md), browser page script (.js), static assets
    package_data={'agentica': ['**/*.md', '**/*.js', '**/*.html', '**/*.css']},
    extras_require={
        "gateway": [
            "fastapi>=0.109.0",
            "uvicorn>=0.27.0",
            "websockets>=12.0",
            "lark-oapi>=1.0.0",
            "apscheduler>=3.10.0",
        ],
        "telegram": ["python-telegram-bot>=20.0"],
        "discord": ["discord.py>=2.0"],
    },
)
