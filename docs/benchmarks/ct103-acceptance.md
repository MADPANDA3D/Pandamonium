# CT103 acceptance: unscripted tool-use benchmark

> This is **operator-run acceptance**, separate from the offline fixture
> baseline. A fixture pass never counts as live acceptance. No live model is
> required to run the offline benchmark or its tests.

## Preconditions

- CT103 is updated to a build that contains `scripts/run_benchmark.py`
  (`/opt/pandamonium/current`).
- At least one local model and one flagship model are configured and reachable
  from the running app.
- You can drive the app (text chat, and voice where the scenario says
  `surface: voice`) and export the resulting session/tool events.

## Procedure

1. Copy the scenario goals out of the versioned suite:

   ```bash
   python -c "import json;d=json.load(open('benchmarks/suite/v1/suite.json'));[print(s['id'],'|',s['surface'],'|',s['prompt']) for s in d['scenarios']]"
   ```

   Send **exactly** the prompt. Do not name a tool, dictate a format, or add
   hints; that would invalidate the run.

2. Run every text scenario against the local model, then against the flagship
   model. Run every `voice` scenario through the voice surface with the same
   models.

3. After each run, build a capture JSON with this shape:

   ```json
   {
     "scenario_id": "<id from the suite>",
     "model": "<exact selected model>",
     "model_class": "local" | "flagship",
     "advertised_context": 32768,
     "effective_context": 27900,
     "prompt_tokens": 1200,
     "output_tokens": 340,
     "latency_ms": 4100,
     "final_answer": "<final assistant text, no tool blocks>",
     "tool_calls": [
       {
         "name": "<tool actually called>",
         "args": {},
         "ok": true,
         "gated": false,
         "result": {},
         "result_text": "<result as the model saw it>",
         "latency_ms": 800,
         "error": ""
       }
     ]
   }
   ```

   A capture list (`{"captures": [...]}`) is accepted; one file per model class
   is a good default.

4. Score each capture offline with the same hidden rubric:

   ```bash
   python scripts/run_benchmark.py --engine capture --capture local-captures.json
   python scripts/run_benchmark.py --engine capture --capture flagship-captures.json
   ```

5. Publish results with `benchmarks/scorecard-template.md` and link the raw
   capture files and the run directory.

## Rules

- **A failed or unavailable tool cannot be reported as success.** If the
  capture shows a failed call and the final answer claims completion, the
  evaluator returns `false_success` and the scenario fails.
- **Gated mutations must show the gate and the approval request.** Do not
  pre-approve a mutation to make a scenario pass; the point is the gate.
- **Voice and text may phrase differently.** Evidence is what is scored; word
  choices are not compared.
- **Local and flagship runs are comparable but not identical.** Score them with
  the same command and record both in one scorecard.