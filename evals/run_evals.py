from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import groq

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from agents import meal_agent, shopping_agent

from evals.groq_models import list_groq_models, pick_default_text_candidates
from evals.judge import judge_conversation
from evals.mock_shop import apply_mock_shop
from evals.scoring import (
    check_allowed_ingredients,
    check_min_chars,
    check_must_include_any,
    check_must_not_include,
    dumps_json,
    score_case,
)
from evals.config_tools import update_model_constant


def _load_streamlit_secrets() -> dict[str, Any]:
    secrets_path = Path(".streamlit") / "secrets.toml"
    if not secrets_path.exists():
        raise FileNotFoundError(
            "Missing .streamlit/secrets.toml. Create it with GROQ_API_KEY = '...'."
        )
    import tomllib

    return tomllib.loads(secrets_path.read_text("utf-8"))


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


def _apply_text_model_override(model_id: str) -> None:
    # config.py is the canonical place, but many modules imported TEXT_MODEL by value.
    config.TEXT_MODEL = model_id
    meal_agent.TEXT_MODEL = model_id
    shopping_agent.TEXT_MODEL = model_id


def _apply_vision_model_override(model_id: str) -> None:
    config.VISION_MODEL = model_id
    from services import vision

    vision.VISION_MODEL = model_id


@dataclass
class EvalOutcome:
    case_id: str
    model_id: str
    ok: bool
    score: dict[str, Any]
    elapsed_s: float
    conversation: list[dict[str, str]]
    judge: dict[str, Any] | None = None


@dataclass
class MmluOutcome:
    model_id: str
    total: int
    correct: int
    elapsed_s: float
    by_subject: dict[str, dict[str, int]]
    rows: list[dict[str, Any]]


@dataclass
class VisionOutcome:
    case_id: str
    model_id: str
    ok: bool
    score: dict[str, Any]
    elapsed_s: float
    output: str


def _load_mmlu_cases(
    path: Path,
    subjects_csv: str | None,
    limit: int,
    seed: int,
) -> list[dict[str, str]]:
    subjects = None
    if subjects_csv:
        subjects = {s.strip() for s in subjects_csv.split(",") if s.strip()}

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            subject = str(row.get("Subject", "") or "")
            if subjects and subject not in subjects:
                continue
            if not all(row.get(k) for k in ("Question", "A", "B", "C", "D", "Answer", "Subject")):
                continue
            rows.append({
                "question": str(row["Question"]),
                "A": str(row["A"]),
                "B": str(row["B"]),
                "C": str(row["C"]),
                "D": str(row["D"]),
                "answer": str(row["Answer"]).strip().upper()[:1],
                "subject": subject,
            })

    rng = random.Random(seed)
    rng.shuffle(rows)
    if limit > 0:
        rows = rows[:limit]
    return rows


def _image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _encode_file_image(path: Path) -> tuple[str, str]:
    import base64

    raw = path.read_bytes()
    return base64.b64encode(raw).decode("utf-8"), _image_mime(path)


def _load_vision_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


def _score_vision_case(case: dict[str, Any], output: str) -> dict[str, Any]:
    asserts = case.get("assert", {}) or {}
    checks = [
        check_must_include_any(output, asserts.get("must_include_any", []) or []),
        check_must_not_include(output, asserts.get("must_not_include", []) or []),
        check_min_chars(output, int(asserts.get("min_chars", 0) or 0)),
        check_allowed_ingredients(
            output,
            asserts.get("allowed_ingredients", []) or [],
            asserts.get("allowed_pantry", None),
            asserts.get("ingredient_watchlist", None),
        ),
    ]
    return {
        "ok": all(c.ok for c in checks),
        "checks": [c.__dict__ for c in checks],
    }


def _run_vision_case(client: groq.Groq, case: dict[str, Any]) -> str:
    from services import vision

    image_path = Path(str(case.get("image_path", "")))
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    if not image_path.exists():
        raise FileNotFoundError(f"Vision eval image not found: {image_path}")
    b64, mime = _encode_file_image(image_path)
    mode = str(case.get("mode", "ingredients") or "ingredients")
    if mode == "grocery_list":
        return vision.extract_grocery_list(client, b64, mime)
    return vision.analyze_ingredients(client, b64, mime)


def _run_vision_evals(args: argparse.Namespace, api_key: str) -> int:
    cases = _load_vision_cases(Path(args.vision_cases))
    if not cases:
        raise ValueError("No vision cases found.")

    client = groq.Groq(api_key=api_key)
    model_ids = _iter_models(api_key, args.models or args.vision_model, False)
    outcomes: list[VisionOutcome] = []

    for model_id in model_ids:
        _apply_vision_model_override(model_id)
        for case in cases:
            start = time.perf_counter()
            output = _run_vision_case(client, case)
            elapsed = time.perf_counter() - start
            score = _score_vision_case(case, output)
            outcome = VisionOutcome(
                case_id=str(case.get("id", "")),
                model_id=model_id,
                ok=bool(score["ok"]),
                score=score,
                elapsed_s=elapsed,
                output=output,
            )
            outcomes.append(outcome)
            status = "PASS" if outcome.ok else "FAIL"
            print(f"[{status}] {outcome.case_id}  model={model_id}  {elapsed:.2f}s")
            if not outcome.ok:
                print("  " + dumps_json(score))
                print(f"  output: {output[:300]!r}")

    by_model: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        stats = by_model.setdefault(outcome.model_id, {"passed": 0, "total": 0})
        stats["total"] += 1
        stats["passed"] += int(outcome.ok)

    leaderboard = sorted(
        (
            {"model": model, **stats, "pass_rate": stats["passed"] / max(1, stats["total"])}
            for model, stats in by_model.items()
        ),
        key=lambda r: (r["pass_rate"], r["passed"]),
        reverse=True,
    )
    print("\nVision leaderboard:")
    for row in leaderboard:
        print(f"- {row['model']}: {row['passed']}/{row['total']} ({row['pass_rate']:.0%})")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for outcome in outcomes:
                f.write(json.dumps(outcome.__dict__, ensure_ascii=False) + "\n")

    return 0


def _mmlu_prompt(row: dict[str, str]) -> str:
    return (
        "Answer this multiple-choice question. "
        "Return only the single letter A, B, C, or D.\n\n"
        f"Subject: {row['subject']}\n"
        f"Question: {row['question']}\n"
        f"A. {row['A']}\n"
        f"B. {row['B']}\n"
        f"C. {row['C']}\n"
        f"D. {row['D']}\n\n"
        "Answer:"
    )


def _extract_choice(text: str) -> str | None:
    cleaned = text.strip().upper()
    if cleaned[:1] in {"A", "B", "C", "D"}:
        return cleaned[:1]
    match = re.search(r"\b([ABCD])\b", cleaned)
    return match.group(1) if match else None


def _run_mmlu(
    client: groq.Groq,
    model_id: str,
    cases: list[dict[str, str]],
) -> MmluOutcome:
    rows: list[dict[str, Any]] = []
    by_subject: dict[str, dict[str, int]] = {}
    correct = 0
    start = time.perf_counter()

    for idx, row in enumerate(cases, start=1):
        resp = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": _mmlu_prompt(row)}],
            temperature=0,
            max_tokens=8,
        )
        raw = resp.choices[0].message.content or ""
        pred = _extract_choice(raw)
        ok = pred == row["answer"]
        correct += int(ok)

        subject_stats = by_subject.setdefault(row["subject"], {"total": 0, "correct": 0})
        subject_stats["total"] += 1
        subject_stats["correct"] += int(ok)

        rows.append({
            "index": idx,
            "subject": row["subject"],
            "answer": row["answer"],
            "prediction": pred,
            "ok": ok,
            "raw": raw,
            "question": row["question"],
        })

        if idx % 10 == 0 or idx == len(cases):
            print(f"  {model_id}: {idx}/{len(cases)} MMLU questions")

    elapsed = time.perf_counter() - start
    return MmluOutcome(
        model_id=model_id,
        total=len(cases),
        correct=correct,
        elapsed_s=elapsed,
        by_subject=by_subject,
        rows=rows,
    )


def _run_standard_mmlu(args: argparse.Namespace, api_key: str) -> int:
    cases = _load_mmlu_cases(
        Path(args.mmlu_cases),
        args.mmlu_subjects,
        args.mmlu_limit,
        args.mmlu_seed,
    )
    if not cases:
        raise ValueError("No MMLU cases matched the requested filters.")

    client = groq.Groq(api_key=api_key)
    model_ids = _iter_models(api_key, args.models, args.all_visible_models)
    outcomes: list[MmluOutcome] = []

    print(f"Running MMLU standardized eval: {len(cases)} questions")
    for model_id in model_ids:
        print(f"\nModel: {model_id}")
        outcomes.append(_run_mmlu(client, model_id, cases))

    leaderboard = sorted(
        outcomes,
        key=lambda o: (o.correct / max(1, o.total), o.correct),
        reverse=True,
    )
    print("\nMMLU leaderboard:")
    for outcome in leaderboard:
        acc = outcome.correct / max(1, outcome.total)
        print(f"- {outcome.model_id}: {outcome.correct}/{outcome.total} ({acc:.1%})  {outcome.elapsed_s:.1f}s")

    if args.mmlu_out:
        out_path = Path(args.mmlu_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for outcome in outcomes:
                for row in outcome.rows:
                    f.write(
                        json.dumps(
                            {
                                "model_id": outcome.model_id,
                                **row,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

    if args.apply_best_text_model and leaderboard:
        best = leaderboard[0].model_id
        update_model_constant(ROOT / "config.py", "TEXT_MODEL", best)
        print(f"\nUpdated config.py: TEXT_MODEL = {best!r}")

    return 0


def _run_meal_case(client: groq.Groq, case: dict[str, Any]) -> list[dict[str, str]]:
    location = case.get("location", "")
    if not location:
        raise ValueError(f"case {case.get('id')} missing location")
    system_prompt = config.MEAL_PLAN_PROMPT.format(location=location)

    # For evals we keep per-case “memory” inside the conversation only.
    conversation: list[dict[str, str]] = []
    notes = case.get("active_notes", "") or ""

    for turn in case.get("turns", []) or []:
        # `turn` can be a string, or a dict:
        #   {"text": "...", "image_path": "/abs/path/to.jpg"}
        image_path = None
        if isinstance(turn, dict):
            user_message = str(turn.get("text", "") or "")
            image_path = turn.get("image_path")
        else:
            user_message = str(turn)
        full_message = f"{notes}\n\n{user_message}" if notes else user_message
        conversation.append({"role": "user", "content": user_message})

        # Pass prior conversation as history (excluding the current user turn).
        history = conversation[:-1]
        if image_path:
            from services import vision

            raw = Path(str(image_path)).read_bytes()
            suffix = str(image_path).lower()
            mime = "image/jpeg"
            if suffix.endswith(".png"):
                mime = "image/png"
            elif suffix.endswith(".webp"):
                mime = "image/webp"
            import base64

            b64 = base64.b64encode(raw).decode("utf-8")
            ingredients = vision.analyze_ingredients(client, b64, mime)
            full_message = f"Ingredients from image: {ingredients}\n\n{full_message}"
        final = ""
        for event_type, payload in meal_agent.run(client, history, system_prompt, full_message):
            if event_type == "done":
                final = payload
        conversation.append({"role": "assistant", "content": final})

    return conversation


def _run_shop_case(
    client: groq.Groq,
    case: dict[str, Any],
    kroger_id: str,
    kroger_secret: str,
) -> list[dict[str, str]]:
    location = case.get("location", "")
    if not location:
        raise ValueError(f"case {case.get('id')} missing location")

    conversation: list[dict[str, str]] = []
    notes = case.get("active_notes", "") or ""

    for turn in case.get("turns", []) or []:
        user_message = str(turn)
        full_message = f"{notes}\n\n{user_message}" if notes else user_message
        conversation.append({"role": "user", "content": user_message})

        final = ""
        for event_type, payload in shopping_agent.run(client, location, full_message, kroger_id, kroger_secret):
            if event_type == "done":
                final = payload
        conversation.append({"role": "assistant", "content": final})

    return conversation


def _summarize(outcomes: list[EvalOutcome]) -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        m = by_model.setdefault(
            o.model_id,
            {"total": 0, "passed": 0, "cases": [], "judge_scores": []},
        )
        m["total"] += 1
        if o.ok:
            m["passed"] += 1
        row = {
            "id": o.case_id,
            "ok": o.ok,
            "elapsed_s": round(o.elapsed_s, 3),
            "score": o.score,
        }
        if o.judge is not None:
            row["judge"] = o.judge
            scores = (o.judge.get("scores") or {}) if isinstance(o.judge, dict) else {}
            if isinstance(scores, dict):
                # average of known rubric keys when present
                vals = []
                for k in ("helpfulness", "faithfulness", "formatting", "specificity"):
                    v = scores.get(k)
                    if isinstance(v, (int, float)):
                        vals.append(float(v))
                if vals:
                    m["judge_scores"].append(sum(vals) / len(vals))
        m["cases"].append(row)

    leaderboard = []
    for mid, stats in by_model.items():
        judge_avg = (sum(stats["judge_scores"]) / len(stats["judge_scores"])) if stats["judge_scores"] else None
        leaderboard.append({
            "model": mid,
            "passed": stats["passed"],
            "total": stats["total"],
            "pass_rate": stats["passed"] / max(1, stats["total"]),
            "judge_avg": judge_avg,
        })
    leaderboard.sort(
        key=lambda r: (
            r["pass_rate"],
            (r["judge_avg"] if r["judge_avg"] is not None else -1.0),
            r["passed"],
        ),
        reverse=True,
    )

    return {"leaderboard": leaderboard, "by_model": by_model}


def _iter_models(
    api_key: str,
    models_csv: str | None,
    use_all_visible: bool,
) -> list[str]:
    if models_csv:
        return [m.strip() for m in models_csv.split(",") if m.strip()]

    if use_all_visible:
        visible = [m.id for m in list_groq_models(api_key)]
        return pick_default_text_candidates(visible)

    return [config.TEXT_MODEL]


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Maple eval runner + Groq model sweeps")
    p.add_argument("--cases", default="evals/cases_smoke.jsonl", help="Path to jsonl cases")
    p.add_argument("--agent", choices=["meal", "shop", "both"], default="both", help="Which agent to eval")
    p.add_argument("--standard-eval", choices=["none", "mmlu", "vision"], default="none", help="Run a standardized or direct service eval instead of Maple cases")
    p.add_argument("--mmlu-cases", default="mmlu.csv", help="Path to MMLU-style CSV")
    p.add_argument("--mmlu-limit", type=int, default=100, help="Number of MMLU questions to sample; 0 means all")
    p.add_argument("--mmlu-seed", type=int, default=7, help="Random seed for MMLU sampling")
    p.add_argument("--mmlu-subjects", default=None, help="Comma-separated MMLU subjects to include")
    p.add_argument("--mmlu-out", default=None, help="Write MMLU per-question jsonl results to this path")
    p.add_argument("--vision-cases", default="evals/cases_vision.jsonl", help="Path to vision jsonl cases")
    p.add_argument("--models", default=None, help="Comma-separated model IDs to sweep for TEXT_MODEL")
    p.add_argument("--all-visible-models", action="store_true", help="Sweep all visible (heuristically filtered) models")
    p.add_argument("--vision-model", default=None, help="Override VISION_MODEL (for image ingredient extraction)")
    p.add_argument("--list-models", action="store_true", help="Print model IDs visible to your Groq key and exit")
    p.add_argument("--mock-shop", action="store_true", help="Mock external APIs for shopping_agent")
    p.add_argument("--judge", action="store_true", help="Run LLM-as-judge scoring (adds cost/latency)")
    p.add_argument("--judge-model", default=None, help="Model ID to use for judging (defaults to current TEXT_MODEL)")
    p.add_argument("--apply-best-text-model", action="store_true", help="After sweep, write best TEXT_MODEL to config.py")
    p.add_argument("--out", default=None, help="Write jsonl results to this path")
    args = p.parse_args(list(argv) if argv is not None else None)

    secrets = _load_streamlit_secrets()
    api_key = secrets["GROQ_API_KEY"]

    if args.list_models:
        for m in list_groq_models(api_key):
            print(m.id)
        return 0

    if args.standard_eval == "mmlu":
        return _run_standard_mmlu(args, api_key)

    if args.standard_eval == "vision":
        return _run_vision_evals(args, api_key)

    cases_path = Path(args.cases)
    cases = _load_cases(cases_path)

    client = groq.Groq(api_key=api_key)
    kroger_id = secrets.get("KROGER_CLIENT_ID", "")
    kroger_secret = secrets.get("KROGER_CLIENT_SECRET", "")

    undo_mock = None
    if args.mock_shop:
        undo_mock = apply_mock_shop()

    if args.vision_model:
        _apply_vision_model_override(args.vision_model)

    model_ids = _iter_models(api_key, args.models, args.all_visible_models)
    outcomes: list[EvalOutcome] = []
    judge_model = args.judge_model or config.TEXT_MODEL

    try:
        for model_id in model_ids:
            _apply_text_model_override(model_id)

            for case in cases:
                agent = case.get("agent")
                if args.agent != "both" and agent != args.agent:
                    continue

                if args.agent == "both" and agent not in ("meal", "shop"):
                    continue

                start = time.perf_counter()
                if agent == "meal":
                    conversation = _run_meal_case(client, case)
                else:
                    conversation = _run_shop_case(client, case, kroger_id, kroger_secret)
                elapsed = time.perf_counter() - start

                score = score_case(case, conversation)
                judge = None
                if args.judge:
                    judge = judge_conversation(client, judge_model, case, conversation)
                outcomes.append(
                    EvalOutcome(
                        case_id=str(case.get("id", "")),
                        model_id=model_id,
                        ok=bool(score["ok"]),
                        score=score,
                        elapsed_s=elapsed,
                        conversation=conversation,
                        judge=judge,
                    )
                )

                status = "PASS" if score["ok"] else "FAIL"
                print(f"[{status}] {case.get('id')}  model={model_id}  {elapsed:.2f}s")
                if not score["ok"]:
                    print("  " + dumps_json(score))

    finally:
        if undo_mock:
            undo_mock()

    summary = _summarize(outcomes)
    print("\nLeaderboard:")
    for row in summary["leaderboard"][:15]:
        extra = ""
        if row.get("judge_avg") is not None:
            extra = f"  judge≈{row['judge_avg']:.2f}/5"
        print(f"- {row['model']}: {row['passed']}/{row['total']} ({row['pass_rate']:.0%}){extra}")

    if args.apply_best_text_model and summary["leaderboard"]:
        best = summary["leaderboard"][0]["model"]
        update_model_constant(ROOT / "config.py", "TEXT_MODEL", best)
        print(f"\nUpdated config.py: TEXT_MODEL = {best!r}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for o in outcomes:
                f.write(
                    json.dumps(
                        {
                            "case_id": o.case_id,
                            "model_id": o.model_id,
                            "ok": o.ok,
                            "elapsed_s": o.elapsed_s,
                            "score": o.score,
                            "conversation": o.conversation,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
