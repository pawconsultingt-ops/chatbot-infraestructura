import json, pathlib

MAX_LEN = 4090

for path in ["test_payloads.jsonl", "test_payloads_burst.jsonl"]:
    p = pathlib.Path(path)
    lines = p.read_text(encoding="utf-8").splitlines()
    fixed = 0
    out = []
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        if len(r["message"]) > MAX_LEN:
            r["message"] = r["message"][:MAX_LEN]
            r["estimated_tokens_in"] = min(r["estimated_tokens_in"], MAX_LEN // 4)
            fixed += 1
        out.append(json.dumps(r, ensure_ascii=False))
    p.write_text("\n".join(out), encoding="utf-8")
    print(f"{path}: {fixed} messages truncated to {MAX_LEN} chars")
