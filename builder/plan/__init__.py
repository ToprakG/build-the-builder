"""A procedure plan: ordered steps, compiled into a phone workflow.

The plan is data. Tool names, spoken lines, branch values and currency words come from the plan,
which the extractor fills from the documents. This package does not name a product, a bank or a case.
"""

from builder.plan.compile import compile_plan
from builder.plan.schema import complete, validate

__all__ = ["compile_plan", "complete", "validate"]
