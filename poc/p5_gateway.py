"""p5 재평가(#35): 백엔드의 실제 extract_query 프롬프트·출력 모델로 조건 추출 정확도를 다시 잰다.

- 프롬프트·스키마·공급자 요청은 복사하지 않고 backend/app의 DefaultPromptBuilder·QueryConditions·
  OllamaProvider·GeminiProvider를 그대로 불러 쓴다(서비스와 같은 요청).
  (프롬프트가 바뀌면 이 스크립트를 다시 돌리면 된다. AI-REQ-14 프롬프트 버전별 평가 기록)
- 평가셋: eval/p5_questions_genre_groups.json = p5_questions.json 22문항(정답 장르를 장르 묶음 12개로 바꿈)
  + 4문항(묶음에 없는 장르 2개: 장르를 비워야 함 / 묶음 안 장르 2개: 묶음 이름으로 바꿔야 함)
- 지표·채점은 p5_llm.py와 같다(필드 9개 일치율, 범위 밖 1필드). 스키마 통과는 QueryConditions 검증으로 본다.
  v2와 나란히 보려고 v2와 같은 22문항(Q01~Q22)의 일치율도 따로 낸다.
- Gemini는 무료 한도(모델당 하루 20회) 때문에 문항별로 저장하며 이어 실행한다(--gemini).

실행: python p5_gateway.py             # 로컬 모델(동시 1·4)
      python p5_gateway.py --gemini    # Gemini(GEMINI_API_KEY 필요, 다음 날 다시 실행하면 이어서 함)
출력: results/p5_gateway.json, results/p5_gateway_answers.json, results/p5_gateway_gemini_answers.json
"""
import asyncio
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from common import ROOT, env, now, save
from p5_llm import FIELDS, Stop, field_eq, p95

sys.path.insert(0, str(ROOT.parent / "backend"))
from pydantic import ValidationError  # noqa: E402

from app.data.ai_gateway.errors import RATE_LIMITED, UPSTREAM, ProviderError  # noqa: E402
from app.data.ai_gateway.prompts import DefaultPromptBuilder  # noqa: E402
from app.data.ai_gateway.providers import GeminiProvider, OllamaProvider  # noqa: E402
from app.data.ai_gateway.tasks import TASKS, QueryConditions  # noqa: E402
from app.support.privacy import mask  # noqa: E402

SPEC = TASKS["extract_query"]
SAME_AS_V2 = {f"Q{i:02d}" for i in range(1, 23)}


def prompt(q):
    return DefaultPromptBuilder().build(SPEC, {"query": mask(q)})  # 서비스와 같이 마스킹한 문장만 넣는다


def call_ollama(q):
    provider = OllamaProvider(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                              os.getenv("OLLAMA_MODEL", "qwen3:8b"))
    t = time.perf_counter()
    raw = asyncio.run(provider.complete(prompt(q), timeout_s=120))
    return raw, time.perf_counter() - t


def call_gemini(q):
    provider = GeminiProvider(env("GEMINI_API_KEY"), os.getenv("GEMINI_MODEL", "gemini-3.5-flash"))
    t = time.perf_counter()
    for wait in (5, 15, 30, None):
        try:
            raw = asyncio.run(provider.complete(prompt(q), timeout_s=60))
            return raw, time.perf_counter() - t
        except ProviderError as e:
            if str(e) == "HTTP 404":
                raise Stop("Gemini 모델 404(종료·오타)") from None
            if e.kind == RATE_LIMITED and wait is None:
                raise Stop("Gemini 무료 등급 한도 초과(429). 다음 날 다시 실행하면 이어서 함") from None
            if e.kind not in (RATE_LIMITED, UPSTREAM) or wait is None:
                raise
            time.sleep(wait)
    raise RuntimeError("재시도 초과")


def grade(gold, raw):
    n = 1 if gold["out_of_scope"] else len(FIELDS)
    try:
        o = json.loads(raw)
    except (ValueError, TypeError):
        return {"schema_ok": False, "match": 0.0, "fields_ok": 0, "fields": n}
    try:
        QueryConditions.model_validate(o)
        ok = True
    except ValidationError:
        ok = False
    if not isinstance(o, dict):
        return {"schema_ok": ok, "match": 0.0, "fields_ok": 0, "fields": n}
    if gold["out_of_scope"]:
        m = o.get("out_of_scope") is True
        return {"schema_ok": ok, "match": float(m), "fields_ok": int(m), "fields": 1}
    if o.get("out_of_scope"):
        return {"schema_ok": ok, "match": 0.0, "fields_ok": 0, "fields": n, "wrong": ["out_of_scope"]}
    good = [f for f in FIELDS if field_eq(f, o.get(f), gold[f])]
    return {"schema_ok": ok, "match": len(good) / n, "fields_ok": len(good), "fields": n,
            "wrong": [f for f in FIELDS if f not in good]}


def answer(item, fn):
    try:
        raw, dt = fn(item["q"])
        return {"id": item["id"], "raw": raw, "latency_s": dt, "error": None, **grade(item["gold"], raw)}
    except Stop:
        raise
    except Exception as e:  # 한 문항의 실패는 스키마 위반으로 센다
        n = 1 if item["gold"]["out_of_scope"] else len(FIELDS)
        return {"id": item["id"], "raw": None, "latency_s": None, "error": repr(e)[:200],
                "schema_ok": False, "match": 0.0, "fields_ok": 0, "fields": n}


def pct(ans):
    tot = sum(a["fields"] for a in ans)
    return round(100 * sum(a["fields_ok"] for a in ans) / tot, 1) if tot else None


def summarize(provider, workers, qs, ans):
    gold = {q["id"]: q["gold"] for q in qs}
    lat = [a["latency_s"] for a in ans if a["latency_s"]]
    s = {"provider": provider, "prompt_version": SPEC.prompt_version, "workers": workers, "questions": len(ans), "complete": len(ans) == len(qs),
         "field_match_pct": pct(ans),
         "field_match_pct_q01_q22": pct([a for a in ans if a["id"] in SAME_AS_V2]),
         "question_mean_match_pct": round(100 * statistics.mean(a["match"] for a in ans), 1),
         "out_of_scope_correct": sum(1 for a in ans if gold[a["id"]]["out_of_scope"] and a["match"] == 1),
         "genre_ok": sum(1 for a in ans if not gold[a["id"]]["out_of_scope"] and "genres" not in a.get("wrong", ["genres"])),
         "genre_questions": sum(1 for q in qs if not q["gold"]["out_of_scope"]),
         "schema_ok_pct": round(100 * sum(a["schema_ok"] for a in ans) / len(ans), 1),
         "latency_p95_s": p95(lat), "latency_mean_s": round(statistics.mean(lat), 2) if lat else None,
         "errors": sum(1 for a in ans if a["error"]),
         "wrong": {a["id"]: a["wrong"] for a in ans if a.get("wrong")}}
    s["pass"] = s["field_match_pct"] >= 90 if s["complete"] else None
    return s


def load_result():
    path = ROOT / "results" / "p5_gateway.json"
    base = {"prompt_version": SPEC.prompt_version, "criterion": "필드 일치율 90% 이상",
            "questions": "eval/p5_questions_genre_groups.json", "runs": []}
    return json.loads(path.read_text()) if path.exists() else base


def main_local():
    qs = json.loads((ROOT / "eval" / "p5_questions_genre_groups.json").read_text())
    res = load_result()
    res["runs"] = [r for r in res["runs"] if not r["provider"].startswith("ollama")]
    res["prompt_version"] = SPEC.prompt_version
    res["local_run_at"] = now()
    model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    call_ollama("워밍업: 해운대 촬영지")  # 모델 적재 시간은 지연에서 뺀다
    allans = {}
    for workers in (1, 4):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            ans = list(ex.map(lambda it: answer(it, call_ollama), qs))
        s = summarize(f"ollama:{model}", workers, qs, ans)
        res["runs"].append(s)
        allans[f"ollama_w{workers}"] = ans
        print(json.dumps(s, ensure_ascii=False))
    save("p5_gateway.json", res)
    save("p5_gateway_answers.json", allans)


def main_gemini():
    qs = json.loads((ROOT / "eval" / "p5_questions_genre_groups.json").read_text())
    path = ROOT / "results" / "p5_gateway_gemini_answers.json"
    done = {a["id"]: a for a in (json.loads(path.read_text()) if path.exists() else [])}
    stopped = None
    for item in qs:
        if item["id"] in done:
            continue
        try:
            done[item["id"]] = answer(item, call_gemini)
        except Stop as e:
            stopped = str(e)
            break
        path.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1))
    res = load_result()
    res["runs"] = [r for r in res["runs"] if not r["provider"].startswith("gemini")]
    ans = [done[q["id"]] for q in qs if q["id"] in done]
    if ans:
        s = summarize(f"gemini:{os.getenv('GEMINI_MODEL', 'gemini-3.5-flash')}", 1, qs, ans)
        res["runs"].append(s)
        print(json.dumps(s, ensure_ascii=False))
    res["gemini_error"] = stopped
    res["gemini_run_at"] = now()
    save("p5_gateway.json", res)
    print("중단:", stopped)


if __name__ == "__main__":
    main_gemini() if "--gemini" in sys.argv else main_local()
