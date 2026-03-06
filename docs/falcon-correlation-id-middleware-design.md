# Falcon Correlation ID Middleware Design

## 1. Overview

This document describes the correlation ID lifecycle and supporting utilities
used by the Falcon middleware. The goal is to ensure every request has a
well-formed correlation identifier that can be retrieved, generated, validated,
and stored in context for downstream logging and tracing.

## 2. Correlation ID lifecycle

### 2.1. Retrieval

If a request includes a correlation ID header, the middleware reads it and
passes it through validation. If none is present, it falls back to generation.

### 2.2. Generation

When no valid correlation ID is provided, the middleware generates a default
value using UUIDv7. Generated values are RFC 4122 compliant and use millisecond
precision timestamps.

### 2.3. Validation

Incoming correlation IDs are validated for format and version/variant
constraints. Invalid values are discarded and replaced with a newly generated
ID. Validation rules should be strict enough to protect downstream systems, but
lightweight enough to avoid introducing latency in the request path.

### 2.4. Contextual storage

The middleware stores the correlation ID in request context so downstream
components can access it without re-parsing headers or regenerating values. The
context value should be considered authoritative for the lifetime of the
request.

## 3. Implementation details

### 3.2.3. Default UUIDv7 generator

The default generator returns a lowercase, 32-character hex string produced
from a UUIDv7 value. The generator must:

- Use an RFC 4122 UUIDv7 implementation with millisecond precision.
- Return lowercase hex without dashes.
- Remain compatible with the project Python requirement (>=3.12).

Recommended implementation:

    def default_uuid7_generator() -> str:
        """Return an RFC 4122 UUIDv7 hex string."""
        return uuid_utils.uuid7().hex

## 4. Design decisions

- Decision: Use `uuid-utils` for UUIDv7 generation.
  Rationale: Python 3.12 does not provide a stdlib UUIDv7 API, and `uuid-utils`
  offers RFC 4122 compliant UUIDv7 generation with millisecond precision and a
  stable ABI.
  Date/Author: 2026-01-26 (Codex)

- Decision: Return lowercase hex strings without dashes from the default
  generator.
  Rationale: Hex strings are compact, URL-safe, and consistent with existing
  UUID tooling in this repo.
  Date/Author: 2026-01-26 (Codex)
