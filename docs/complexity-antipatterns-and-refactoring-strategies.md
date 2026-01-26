# Complexity antipatterns and refactoring strategies

## Antipatterns to avoid

- Deeply nested control flow that obscures the happy path.
- Functions that mix parsing, validation, and side effects in a single block.
- Hidden global state or implicit environment dependencies.
- Duplicated validation rules that diverge over time.

## Refactoring strategies

- Extract pure helper functions for parsing and validation.
- Isolate side effects (I/O, environment reads) at module boundaries.
- Prefer small, well-named functions over long procedural blocks.
- Add focused tests before and after refactors to preserve behaviour.
