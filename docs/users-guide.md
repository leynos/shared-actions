# Users guide

## Correlation ID utilities

### Default UUIDv7 generator

Use `default_uuid7_generator()` to create a new correlation ID when one is not
provided by the caller. The generator returns a lowercase, 32-character hex
string representing an RFC 4122 UUIDv7 value with millisecond precision.

Example:

    from correlation_id import default_uuid7_generator

    correlation_id = default_uuid7_generator()

The returned value is suitable for request headers, structured logs, and other
contexts that require a compact, unique identifier.
