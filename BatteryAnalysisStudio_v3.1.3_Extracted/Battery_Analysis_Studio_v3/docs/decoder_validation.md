# PNE decoder validation scope

The `72-byte header + 176-byte little-endian float32 record` profile is a
reverse-engineered hypothesis inherited from Go v2.6, not a vendor format
specification. A structurally plausible import is not instrument validation.

## Source-level port comparison

The Python decoder uses the same Go `main.go` field offsets:

| Offset | Field |
|---:|---|
| 0 / 4 / 8 | Index / StepTime / CVTime |
| 16 / 20 / 24 | Voltage mV / Current mA / Capacity mAh |
| 32 / 36 / 40 | Charge capacity / Discharge capacity / Energy mWh |
| 64 / 68 / 72 | Cell temperature / 10s impedance / Power mW |
| 112 / 116 / 120 / 124 / 128 | 0.1s / 1s / 5s / 30s / 60s impedance |
| 140 | Unverified float candidate, never converted into a date |

- Step reset uses `current_time + max(0.02, previous_time*0.00001) < previous_time`
  with `previous_time > 0.05`, matching Go.
- Matching uses the same time/voltage/current/capacity cost components, skip
  penalty 4, float32 DP accumulation and MATCHED cost cutoff 2.4.
- Time, voltage, current and capacity residual limits preserve the Go thresholds.
- Above 5,000,000 segment/StepEnd combinations it uses the same six-row forward
  matching window and `<7.5` acceptance threshold; this fallback is reported.
- Record length mismatch is rejected rather than truncating partial records.
- Clearly invalid initial singletons remain in the frame as `INITIAL` and are
  excluded from default quantitative analysis. Other implausible core values
  reject the profile. Raw fields remain intact in accepted imports.
- `REVIEW` and `UNMATCHED` are retained; default analyses accept MATCHED/CSV only.
- StepNo is copied from StepEnd and is never assigned by sequential position.
- Internal Cycle and physical Step identities distinguish repeated source IDs;
  original CurCycle/TotalCycle values remain available.

## Executed synthetic validation

`python -B -m unittest discover -s tests -p test_ingestion.py -v`

The independent byte writer in `tests/test_ingestion.py` packs individual fields
using `struct.pack_into`, without relying on the decoder's NumPy dtype. The
tests cover nested ZIPs, Korean/CP949 text, exact numerical fields, skipped
StepEnd records, REVIEW exclusion, initial-record preservation, repeated Cycle
numbers, unsupported layouts, source-preserving missing-StepEnd imports,
cancellation, safe cache loading and content-based cache invalidation.

`studio.demo.generate_demo(destination)` creates clearly labelled SYNTHETIC
multi-channel PNE and complete CSV examples for runtime and frozen-EXE smoke
tests. Both include long charge/discharge curves plus 2 DCR blocks and 4 pulses
per channel, with known 1s/10s/30s resistance targets.

## Independent Go runtime comparison

The preserved Go program was subsequently built using official portable
go1.27.4 (archive SHA256 verified). Its 18 existing tests passed. Both decoders
were executed on three synthetic cases totaling 6 channels, 979 raw records
and 72 Step segments. All 18 numeric fields and source Step/Cycle/status
metadata matched within the Go CSV six-decimal serialization bound. Python
values additionally matched an independent struct.unpack float32 parser.
See `go_comparison.md` and `validation/go_comparison.json` for exact scope.

## Not yet verified

- No actual PNE instrument data, manufacturer reference output, alternate CYC
  firmware profile or company PC has been verified in these tests.
- Source imports currently load the complete selected channel frame. Cache and
  background/cancellable work do not constitute fully lazy Raw loading.
- Device validation must compare raw field values and StepEnd alignment on
  representative charge/discharge, restart, CC/CV and DCR experiments.

Keep these limits separate from source tests, GUI smoke tests and frozen-EXE
smoke tests in the final validation report.
