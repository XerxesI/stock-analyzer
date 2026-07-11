# Experiment Template

**Status:** Template  
**Purpose:** standard structure for recording concrete experiment runs.

The Research Registry tracks ideas and hypotheses. Experiment records track specific
runs: dataset, configuration, commit, artifacts, and result.

Copy this template for each material experiment.

---

## Experiment ID

```text
EXP-000
```

## Title

Short descriptive title.

## Date

```text
YYYY-MM-DD
```

## Owner

Researcher or agent that ran the experiment.

## Related Research Question

Link or reference to Research Registry item.

## Related ADRs

- ADR reference, if applicable.

## Commit

```text
commit_sha:
branch:
```

## Dataset

```text
dataset_name:
dataset_version:
date_range:
universe:
label_version:
feature_set_version:
```

## Configuration

```yaml
# Paste frozen config or link to config artifact.
```

## Hypothesis

What was expected before running the experiment?

## Metrics

Primary:

- metric 1

Secondary:

- metric 2
- metric 3

## Results

Summarize key results. Include tables or link artifacts.

## Artifacts

- report:
- JSON:
- plots:
- logs:

## Decision

Choose one:

```text
ACCEPT
REJECT
INCONCLUSIVE
DEFER
```

## Conclusion

What did we learn?

## Follow-Up

What should happen next, if anything?

## Notes

Any limitations, caveats, or suspected data issues.

