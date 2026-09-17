# CodeScene parser fixtures

These byte-exact Cobertura reports came from the original Slipcover matrix at
`/tmp/concordat-pr173-cs-latest-matrix-20260916-01/`. Do not reformat or
regenerate them.

| Fixture                          | Slipcover | SHA-256                                                            | Prior result                             |
| -------------------------------- | --------- | ------------------------------------------------------------------ | ---------------------------------------- |
| `slipcover-1.0.18-cobertura.xml` | 1.0.18    | `7d3891891f5496f0ffe09b79da09c5798e8d06f85be7f204f7caf8e178848259` | cs-coverage 1.0.101: PASS; 1.0.103: FAIL |
| `slipcover-1.1.0-cobertura.xml`  | 1.1.0     | `48c664cdcae746904d75c142b5a6209410f2e3200aefa361147fa0c4caa139fa` | cs-coverage 1.0.101: PASS; 1.0.103: FAIL |

The 1.0.103 error,
`No matching field found: close for class java.io.InputStreamReader`, names a
Graal runtime class reached while parsing. It is not an XML class string, so
the fixtures intentionally do not contain a literal `InputStreamReader` element
or attribute.
