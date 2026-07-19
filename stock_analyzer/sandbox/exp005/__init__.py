"""EXP-005 -- Minimal Portfolio-Policy Feasibility Pilot.

Implements the design frozen in
docs/09_experiments/EXP-005_Portfolio_Policy_Feasibility_Pilot.md (Revision 5,
FROZEN). This package holds only what is genuinely new (portfolio admission/
reservation/execution accounting, Variant B/D orchestration, the Experiment
Manifest, and post-hoc decision-quality diagnostics) -- ranking, entry-fill, and
exit-decision logic are reused unmodified from stock_analyzer.sandbox.application/
infrastructure via the seams documented in the frozen plan's Section 11, not
duplicated here.
"""
