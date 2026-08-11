# Evaluation Harness (Phase 7 / Day 5)

**Status: harness built, not yet run against real data.** This is intentionally
honest rather than fabricated — correlating pipeline scores against human
judgment requires actual human speech and actual human ratings, both of which
only you (running this locally, with a microphone) can produce. Everything
below is ready to go; what's missing is the data collection step, which is a
few hours of recording + rating, not more building.

## What this measures

Two questions, matching the roadmap's Phase 7 DoD:

1. **Does the pipeline's score agree with a human's?** — Spearman correlation
   between the headline dimensions (`app/scoring/headline.py`) and a human
   rater's score, per dimension, across 6-8 recordings of deliberately varying
   quality.
2. **Is the LLM argument-quality scorer stable?** — run it 3x on the same
   transcript and look at the variance. High variance on `temperature`-less
   models (Claude Opus 5 doesn't accept `temperature`; see the module
   docstring in `app/scoring/argument_quality.py`) would be a real finding,
   not necessarily a bug.

## How to collect the data

1. Record 6-8 sessions through the actual app (`/practice/{id}`), deliberately
   varying quality: some fluent and well-argued, some halting or off-topic,
   some short and repetitive — same spirit as Day 3's three deliberately
   different TTS samples, but real speech this time (yours, or a friend's).
2. Wait for each session to reach `scored` (`GET /sessions/{id}` — or just
   check the feedback page loads).
3. For each session, rate it yourself 0-100 on each of the 5 dimensions using
   the rubric below — and get a second rater if you can (even one other
   person gives you an inter-rater baseline, which matters more than the
   exact number of sessions).
4. Fill in `backend/scripts/ratings_template.csv` (copy it first) — one row
   per session per rater:

   ```
   session_id,rater,fluency,vocabulary,clarity,relevance,argumentation
   3f2a1c9e-...,self,70,65,,55,
   3f2a1c9e-...,friend,75,60,,60,
   ```

   Leave a cell blank if you don't feel you can judge that dimension (e.g.
   clarity/argumentation are hard to rate blind if you haven't configured
   Azure/Anthropic yet either — see below).

5. Run:

   ```bash
   cd backend
   .venv/Scripts/python.exe -m scripts.evaluate correlate path/to/your_ratings.csv
   ```

   This prints a markdown table (session count, Spearman ρ, p-value per
   dimension) — paste it into the Results section below.

6. For the variance check, pick one scored session and run:

   ```bash
   .venv/Scripts/python.exe -m scripts.evaluate llm-variance <session_id> --runs 3
   ```

## Rating rubric (0-100 per dimension)

| Score | Fluency | Vocabulary | Clarity | Relevance | Argumentation |
|---|---|---|---|---|---|
| 0-20 | Constant halting, can't follow | Extremely repetitive, few distinct words | Hard to understand most words | Mostly off-topic | No real argument, just assertions |
| 40-60 | Some hesitation, generally followable | Adequate but repetitive vocabulary | Understandable with effort | On-topic but wanders | Basic structure, weak evidence |
| 80-100 | Smooth, natural pace, minimal fillers | Rich, varied vocabulary | Every word clear | Consistently on-topic | Well-structured, evidenced, handles counterarguments |

Anchor to *your own* judgment of what "good" sounds like for a GD/debate
practice speech — the rubric is a starting point, not a strict standard.

## Results

*(Not yet populated — run `scripts/evaluate.py correlate` against your own
ratings CSV and paste the output table here, along with the honest caveats:
which dimensions correlated well, which didn't, what you'd change as a result.
Per the roadmap's own framing: "Relevance correlated at 0.71 but
persuasiveness only at 0.34, so I stopped surfacing persuasiveness as a number
and made it qualitative feedback instead" is a stronger answer than a
suspiciously perfect result — report whatever you actually get.)*

### Sample-size caveat

With fewer than ~6-8 rated sessions, Spearman correlation is noisy — a single
outlier session can swing ρ substantially. `scripts/evaluate.py` prints this
caveat automatically when `n < 6`. Treat early numbers as directional.

### LLM variance

*(Not yet populated — run `scripts/evaluate.py llm-variance <session_id>` and
paste the per-dimension variance table here.)*
