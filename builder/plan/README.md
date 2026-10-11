# Procedure plan

A phone call as data: ordered steps, compiled into a workflow. The extractor fills the plan from the documents.
Tool names, spoken lines, branch values and currency words stay in the plan.

```
python3 builder/build.py --variant plan <case folder> <mock url>
python3 -m builder.plan.run <documents> <tools.openapi.json> <out dir>
```

A step is a tool call, a spoken line, a question, a handoff, or the end of the call.

- A tool step has inputs, outputs, and branches on the tool result (`@failure`, `@status`, or a mapped variable).
- `before` / `after` derive ops count a list, pick a row, copy a variable, or format an amount.
- An ask step branches on the customer's answer.
- A say step speaks a `{{variable}}` template.
- `money` is the amount template, the zero phrase, and a currency table (`code`, `name`, `minor_digits`).

The compiler does not contain product words. A new procedure is a new plan, not a new branch in the compiler.
