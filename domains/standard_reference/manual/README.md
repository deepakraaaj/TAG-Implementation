# Manual Overrides

Use this folder for reviewed, human-authored meaning that the schema does not express clearly on its own.

Recommended manual files:

- `glossary.json`: business words and their schema targets
- `semantics.json`: join hints, derived logic, and interpretation notes
- `few_shot_examples.json`: realistic user request to schema-intent examples

Keep manual files small and explicit. Put inferred structure in `generated/`, and put reviewed meaning here.
