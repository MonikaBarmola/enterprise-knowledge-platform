"""
Run:
    python evaluation.py

CSV columns: question,role,test_type,expected_source,ground_truth_answer
allowed_departments comes from role (via ROLE_PERMISSIONS).

test_type is one of:
    findable   - expected_source is the real document that should be found
    blocked    - expected_source is a real document that EXISTS but this
                 role must not see it (access-control test)
    no_answer  - no document answers this question for anyone; expected_source
                 is left blank (hallucination test, checked via Faithfulness,
                 not access control)
"""

import csv
import json
from pathlib import Path

from openai import OpenAI

from rag import retrieve, answer_question, TOP_K
from auth import ROLE_PERMISSIONS

TEST_FILE = Path("test_questions.csv")
JUDGE_MODEL = "gpt-4o-mini"


def recall_at_k(results):
    findable = [r for r in results if r["test_type"] == "findable"]

    if not findable:
        return 0

    hits = sum(
        r["expected_source"] in r["sources"]
        for r in findable
    )

    return hits / len(findable)


def access_control_accuracy(results):
    # Only "blocked" rows count here - expected_source is a real document
    # that exists but this role must not see. Correct = it never shows up
    # in the results, regardless of other (allowed) content coming back too.
    blocked = [r for r in results if r["test_type"] == "blocked"]

    if not blocked:
        return None

    correct = sum(
        r["expected_source"] not in r["sources"]
        for r in blocked
    )

    return correct / len(blocked)


# ---------------------------------------------------------------------------
# FAITHFULNESS (LLM-as-judge)
# ---------------------------------------------------------------------------
# Skipping ragas here - it currently needs langchain-core<0.3, which
# conflicts with the langchain-chroma version this project uses. This does
# the same LLM-as-judge check with a plain OpenAI call instead.

FAITHFULNESS_PROMPT = """You are evaluating an AI assistant's answer for FAITHFULNESS -
whether every factual claim in the answer is actually supported by the given context.

Context:
{context}

Answer to evaluate:
{answer}

Break the answer into its individual factual claims. For each claim, decide if it is
directly supported by the context. Then compute a faithfulness score from 0.0 to 1.0:
(number of supported claims) / (total number of claims). If the answer makes no
factual claims at all (e.g. it says information is unavailable), score it 1.0.

Respond with ONLY a JSON object, no other text:
{{"score": <float 0.0-1.0>, "reasoning": "<one sentence explaining the score>"}}"""


def score_faithfulness(context, answer, client):
    prompt = FAITHFULNESS_PROMPT.format(context=context, answer=answer)
    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def average_faithfulness(results, client):
    # Skip NO_ACCESS rows - no answer was generated, so there's nothing to judge.
    scored_rows = [r for r in results if r["answer"] != "NO_ACCESS"]

    if not scored_rows:
        return None, []

    scores = []
    details = []
    for r in scored_rows:
        judged = score_faithfulness(r["context"], r["answer"], client)
        scores.append(judged["score"])
        details.append({
            "question": r["question"],
            "faithfulness": judged["score"],
            "reasoning": judged["reasoning"],
        })

    return sum(scores) / len(scores), details


def run():
    with open(TEST_FILE, encoding="utf-8") as f:
        questions = list(csv.DictReader(f))

    results = []

    for q in questions:
        role = q["role"]
        test_type = q["test_type"]
        expected_source = q["expected_source"]

        allowed = ROLE_PERMISSIONS.get(role, [])
        chunks = retrieve(q["question"], allowed)

        if chunks:
            answer, sources = answer_question(
                q["question"], chunks
            )
            context = "\n\n".join(
                f"[Source: {d.metadata.get('source')}]\n{d.page_content}"
                for d in chunks
            )
        else:
            answer = "NO_ACCESS"
            sources = []
            context = ""

        results.append({
            "question": q["question"],
            "role": role,
            "test_type": test_type,
            "expected_source": expected_source,
            "chunks": len(chunks),
            "sources": sources,
            "context": context,
            "answer": answer,
        })

    client = OpenAI()
    avg_faithfulness, faithfulness_details = average_faithfulness(results, client)

    metrics = {
        "Recall@K": recall_at_k(results),
        "Access-Control Accuracy":
            access_control_accuracy(results),
        "Faithfulness": avg_faithfulness,
        "K": TOP_K,
    }

    Path("results.json").write_text(
        json.dumps({
            "metrics": metrics,
            "results": results,
            "faithfulness_details": faithfulness_details,
        }, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    run()

