# Roadmap

## 2. Correlation ID lifecycle

Implement ID retrieval, generation, validation, and contextual storage.

### 2.2. UUIDv7 generation

- [x] 2.2.1. Implement default UUIDv7 generator. See design-doc §3.2.3.
  - [x] Select and add UUIDv7 library dependency (prefer `uuid-utils` or standard library for Python 3.13+).
  - [x] Create `default_uuid7_generator()` function returning hex string.
  - [x] Ensure RFC 4122 compliance with millisecond precision.
  - [x] Test default generator produces valid UUIDv7 format.
  - [x] Test generated IDs are unique across calls.
