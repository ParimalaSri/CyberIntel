"""Prompt registry: prompts are files, versioned as prompts/<skill_dir>/vN.md.

One loader for every caller (adjudicate.py, evals/run_eval.py,
evals/compare_prompts.py) so a version means the same thing everywhere, and
each loaded prompt carries a content hash that is written into the trace log.
That makes "which exact prompt produced this decision" answerable, and
exposes a prompt file edited in place after runs were already recorded.

Rules (see prompts/README.md):
  - A published version is immutable. Change behaviour => add vN+1.md.
  - Each file starts with an HTML comment holding its changelog.
  - The placeholder syntax is {{name}}; render() refuses to leave one unfilled.
"""
import dataclasses
import json
import os
import re

from llm.tracing import text_hash

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_VERSION_FILE = re.compile(r"^v(\d+)\.md$")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_HEADER = re.compile(r"\A\s*<!--(.*?)-->", re.S)


@dataclasses.dataclass(frozen=True)
class Prompt:
    skill: str
    version: str
    path: str
    text: str
    hash: str
    changelog: str

    def render(self, **values) -> str:
        return render(self, **values)


def _skill_dir(skill: str) -> str:
    # skill names are kebab-case ("account-scoring"), directories snake_case
    return os.path.join(PROMPTS_DIR, skill.replace("-", "_"))


def versions(skill: str) -> list[str]:
    """All published versions of a skill's prompt, oldest first (v2 before v10)."""
    d = _skill_dir(skill)
    if not os.path.isdir(d):
        return []
    found = [(int(m.group(1)), f"v{m.group(1)}") for f in os.listdir(d) if (m := _VERSION_FILE.match(f))]
    return [v for _, v in sorted(found)]


def latest(skill: str) -> str:
    vs = versions(skill)
    if not vs:
        raise FileNotFoundError(f"no prompt versions found for skill {skill!r} in {os.path.abspath(_skill_dir(skill))}")
    return vs[-1]


def load(skill: str, version: str | None = None) -> Prompt:
    """Load a prompt version (default: the latest) with its content hash."""
    version = version or latest(skill)
    path = os.path.join(_skill_dir(skill), f"{version}.md")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"prompt {skill!r} {version!r} not found; available versions: {versions(skill) or 'none'}")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = _HEADER.match(text)
    return Prompt(skill=skill, version=version, path=os.path.abspath(path), text=text,
                  hash=text_hash(text), changelog=m.group(1).strip() if m else "")


def render(prompt: Prompt, **values) -> str:
    """Fill {{name}} placeholders. Non-string values are JSON-encoded."""
    needed = set(_PLACEHOLDER.findall(prompt.text))
    missing = needed - values.keys()
    if missing:
        raise KeyError(f"prompt {prompt.skill} {prompt.version} needs {sorted(missing)}, not provided")
    out = prompt.text
    for name in needed:
        v = values[name]
        out = out.replace("{{" + name + "}}", v if isinstance(v, str) else json.dumps(v))
    return out
