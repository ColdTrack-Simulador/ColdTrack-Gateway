import json
from copy import deepcopy
from pathlib import Path

from flasgger import Swagger


def add_unique(target, entries):
    duplicates = target.keys() & entries.keys()
    if duplicates:
        raise ValueError(f"Definiciones OpenAPI duplicadas: {', '.join(sorted(duplicates))}")
    target.update(entries)


def load_specification():
    folder = Path(__file__).with_name("openapi")
    template = json.loads((folder / "base.json").read_text(encoding="utf-8"))
    for path in sorted(folder.glob("*.json")):
        if path.name == "base.json":
            continue
        fragment = json.loads(path.read_text(encoding="utf-8"))
        for route, operations in fragment.get("paths", {}).items():
            add_unique(template["paths"].setdefault(route, {}), operations)
        for section, definitions in fragment.get("components", {}).items():
            add_unique(template["components"].setdefault(section, {}), definitions)
    return template


def configure_swagger(app):
    template = load_specification()
    config = deepcopy(Swagger.DEFAULT_CONFIG)
    config["openapi"] = template["openapi"]
    config["uiversion"] = 3
    config["specs"][0]["rule_filter"] = lambda _rule: False
    return Swagger(app, config=config, template=template)
