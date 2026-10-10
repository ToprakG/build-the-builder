# Build the Builder

You write a **builder**: a program that reads a bank's brief and creates a phone agent on Freya.
We put that phone agent in front of simulated customers. The share it handles correctly is your score.

One simulated customer is one case. A step of a phone agent is called a node in the platform's documents.

## The ten-minute start

You need Python 3.10 or newer and nothing else.

```bash
cp env.example env.sh        # put your Freya API key in it, and a team key you make up
source env.sh

python3 examples/minimal_builder.py briefs/brief-a.md      # prints {"workflow_id": ..., "agent_id": ...}
python3 tools/run_cases.py <agent_id> --brief A            # ten practice customers, 3 to 11 minutes

python3 examples/minimal_builder.py briefs/brief-a.md briefs/brief-b.md   # Brief B builds on Brief A, so pass both
python3 tools/run_cases.py <agent_id> --brief B            # three practice customers with several products
```

`run_cases.py` prints one line per customer: pass or fail, the tool calls the agent made, and the reason for a failure.
The sample builder passes about half of the ten customers, and not the same half every run. Your job is the rest.

The sample builder has the tools of Briefs A and B typed in by hand. It cannot build for a brief with other tools. Yours has to read them from the brief and from `tools/openapi.json`.

## What is in this kit

| Path | What it is |
|---|---|
| `briefs/brief-a.md` | Freya Bank's specification for customers late on one product. 47 rules. Read all of it. |
| `briefs/brief-b.md` | The extra rules for customers with up to three products. It builds on Brief A. |
| `tools/openapi.json` | The bank's tool API that the phone agent calls during a call. |
| `public-test-set.json` | 13 practice customers: what each one does and what the bank's records must show afterwards. |
| `examples/minimal_builder.py` | The smallest builder that works. One step, the brief pasted in as its prompt, seven tools. |
| `freya.py` | A small client for the Freya API. Standard library only. |
| `tools/run_cases.py`, `tools/score.py` | Run the practice customers against a phone agent and score it. |
| `docs/workflow-format.md` | What a phone agent is made of: steps, prompts, variables, routes. |
| `docs/tools-and-code.md` | Tools that call an API, code steps, hooks, where a tool's response lands. |
| `docs/api.md` | The API calls a builder needs. |

## How you are scored

A customer counts as handled when all of these hold:

1. **Outcome: the bank's records are right.** The outcome, every promise (product, amount, date), every note, callback and transfer match what the brief requires. The scorer reads them from the bank, not from what the agent said.
2. **Policy: the conversation stayed inside the brief.**
3. **Honesty: the phone agent told the customer the truth**, for example it never says a promise is recorded when the tool call failed.

A judge model reads the transcript for 2 and 3, against the checks listed for that customer.

There is no partial credit. Your score is the share of customers handled.

The same phone agent can pass a customer in one run and fail it in the next. Treat one green run as a hint, not as proof.

## The final

- On Sunday we publish briefs you have not seen. They are the same kind of call with different rules: other numbers and limits, other identity questions, another outcome table, another calendar. Their form can differ too: one final brief is a clean specification, the other is raw bank material (a mail, a policy extract, a memo that changes it, a code list).
- Each final brief comes with its own tool API file. The seven tool names stay the same. The fields and the allowed values can change.
- After the final briefs open you may fix a bug in your builder. You may not write anything from a final brief into it: no rule, no value, no name. Fixing a crash on a table your parser did not expect is allowed. Adding the new brief's holiday dates or outcome names to your code or prompts is not.
- You run your builder once on each, with no manual step, and submit the phone agents it created, your code and the builder's trace.
- You export each phone agent to a file with `freya.export_workflow(workflow_id, "<brief>.workflow.json")` and submit those files. We load them into our own workspace to test them, so what you export is what we score.
- We test those phone agents on customers nobody has seen, on several calendar days, three times each. A customer counts only when all three runs pass. A phone agent that is right most of the time is not right.
- Final customers can have up to three products, as in Brief B.
- Final customers talk like people. They give a number in two parts, correct themselves, talk about other things, ask a question back, or say amounts and dates in words. The facts stay the same. Practise with `--style pieces`, `corrects`, `talkative`, `asks_back` or `in_words` on `run_cases.py`.

So build something that reads a brief, not something that knows this one. Whatever your builder can take from the brief, it should take from the brief.

## Rules

- Your builder takes brief files as input and creates the phone agent with no manual step. Do not edit a phone agent by hand after it is created.
- The phone agent runs on the model set in `freya.py` (`AGENT_LLM`). Do not change it. Your builder may use any model or coding agent.
- The phone agent may call only the bank's tool API. Code steps and hooks inside the workflow are allowed and expected.
- Use `freya.save_workflow(...)` or update your workflow in place. A workspace holds 200 workflows. A builder that creates a new one on every run fills it up and leaves you with a list nobody can read.
- One simulation runs at a time per workspace. A ten-customer run takes 3 to 11 minutes. A second `run_cases.py` started meanwhile waits for the first.
- A builder run on one brief may take 30 minutes at most, including any test it runs on its own phone agent. You may run it more than once and submit the phone agent of one run, with that run's trace.
- Equal scores are ranked by the total promised amount on the customers that passed, then by shorter build time, then by earlier submission.
- Your builder must leave a trace of each run: start and end time, each model call it made (model and a one-line purpose), what it created on Freya, and any test it ran on its own agent. Plain text, no fixed format. You submit one trace per final brief.
- All data here is synthetic. Do not bring real customer data.

## When something does not work

- `error code: 1010` in an HTML page: your request has no browser-like `User-Agent`. `freya.py` sets one.
- `Workspace plan limit reached`: you created too many workflows. Update one in place.
- You changed your builder and the score did not move at all: an updated workflow must be published before a simulation uses it. `freya.save_workflow` publishes. A plain `PUT` does not. See "Update a workflow" in `docs/api.md`.
- A customer fails and you do not see why: `freya.get_call(call_id)["messages"]` is the full transcript with the tool calls. `run_cases.py --out results/run.json` saves the call id of every customer.
- Calls end as `customer-ended-call` before the phone agent is done: it was silent too long and the customer hung up, as a person would. `run_cases.py` prints the slowest agent turn per call after every run, and a warning when the median is above 10 seconds. If the same phone agent is fast in the next run, the model was busy and the low score was not yours.
- You want to see what your phone agent says without waiting for a simulation: `POST /api/v2/chat/message` runs one turn at a time. See `docs/api.md`.
- The bank answers `unknown product_id`: the model made an id up. Product ids come from `get_account` (`p1`, `p2`, ...). Give them to the model in the tool result, or fill them in with code.
- A simulation stays at "running" and no call ever starts: the workflow cannot start a call. Try one chat turn. Leave `agent_config` out of the workflow you send.
- The agent says nothing after a tool call and the customer hangs up: see "Tool results and the next turn" in `docs/tools-and-code.md`.
