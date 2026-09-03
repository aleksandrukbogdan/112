import json
from pathlib import Path

for f in Path("content/scenarios").glob("*.json"):
    sc = json.loads(f.read_text(encoding="utf-8"))
    changed = False
    
    if "slug" not in sc and "id" in sc:
        sc["slug"] = sc["id"]
        changed = True
    if "title" not in sc and "nazvanie" in sc:
        sc["title"] = sc["nazvanie"]
        changed = True
    if "difficulty" not in sc and "slozhnost" in sc:
        sc["difficulty"] = sc["slozhnost"]
        changed = True
    if "services" not in sc and "sluzhby" in sc:
        sc["services"] = sc["sluzhby"]
        changed = True
        
    if changed:
        f.write_text(json.dumps(sc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Исправлен: {f.name}")
