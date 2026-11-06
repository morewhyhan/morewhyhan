import json
with open("data/project-aliases.json", encoding="utf-8") as fh:
    data = json.load(fh)
print(json.dumps(data, ensure_ascii=False))
print([hex(ord(c)) for c in list(data)[0][:3]])
