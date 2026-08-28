# CRAG — rapport de scoring

- Mode : A (retrieval scopé aux pages de la question)
- Judge : qwen-code (officiel : gpt-4-0125-preview — comparer uniquement des runs jugées par le même modèle)

| Score | Accuracy | Hallucination | Missing | n |
|---|---|---|---|---|
| **3.5** | 32.0% | 28.5% | 39.5% | 200 |

Repères Task 1 (dev) : LLM seul ≈ −7, RAG naïf ≈ −7, podium KDD Cup ≈ +12 ; hallucination < 20 % = bon signal.

## Par domain

| domain | correct | miss | hallucination |
|---|---|---|---|
| finance | 4 | 30 | 14 |
| movie | 18 | 15 | 12 |
| music | 8 | 6 | 11 |
| open | 14 | 11 | 10 |
| sports | 20 | 17 | 10 |

## Par question_type

| question_type | correct | miss | hallucination |
|---|---|---|---|
| aggregation | 9 | 4 | 7 |
| comparison | 5 | 14 | 2 |
| false_premise | 1 | 9 | 7 |
| multi-hop | 4 | 5 | 3 |
| post-processing | 1 | 3 | 5 |
| set | 6 | 8 | 9 |
| simple | 18 | 21 | 14 |
| simple_w_condition | 20 | 15 | 10 |

