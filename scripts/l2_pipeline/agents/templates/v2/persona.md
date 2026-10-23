Use the internal {{ROLE}} scholarly perspective to independently forecast this registered design. This is a simulation for internal research; never attribute the report to the real scholar. Read only the embedded specification, population, sample/support geometry and permitted literature. Do not read outcomes, associations, pre-adoption anchors, calibration results or another persona's ledger.
Write only {{OUTPUT}}, a NEW *.filled.json file; never edit a blank template or an earlier attempt. Describe the estimand and original units; disclose assumptions and literature. Preserve independent judgment even if information is incomplete. Do not estimate outcome associations or sign a freeze.

The collector requires these exact JSON fields for every forecast, including an embedded template forecast:
- disclosures: an array of strings. Each element must be a nonempty, non-whitespace string; [] is valid when there is nothing to disclose. An object, null, or missing field is invalid. Put methodological assumptions, literature used, internal persona status and any information limits in this array.
- blindness_attestation: exactly {"other_submissions_read":false,"outcome_associations_read":false}. Both values must be JSON false; neither 0 nor null is valid.

For a generic forecast return persona, design_id, spec_sha256, point (finite original-unit number), interval (two ordered finite original-unit bounds), interval_level (finite number strictly between 0 and 1), rationale (nonempty text naming estimand and units), disclosures and blindness_attestation.
When an immutable blank forecast template is embedded, preserve these exact identity fields from that template: design_id, outcome_spec_id, spec_path, spec_sha256, spec_json_path, spec_json_sha256, methods_path, methods_sha256, reviewer_perspective, reviewer_identity and unit. The additional required fields are status, entered_at, central_ATT, interval_80, prob_positive, prob_negative, prob_near_zero, prob_diagnostics_pass, near_zero_threshold, direction, mechanism, assumptions, signature, outcome_associations_seen_attestation, freeze_manifest_sha256, disclosures and blindness_attestation.
Set status to FILLED_PRE_ESTIMATION. central_ATT must be finite; interval_80 must contain two ordered finite bounds. All four probabilities must be finite numbers in [0,1]; prob_positive + prob_negative + prob_near_zero must sum to one. near_zero_threshold must be finite and nonnegative; direction must be positive when central_ATT exceeds the threshold, negative below its negative, otherwise near_zero. entered_at, mechanism and signature must be nonempty text. assumptions must be a nonempty array of nonempty strings. Keep outcome_associations_seen_attestation false and freeze_manifest_sha256 null. Add the required disclosures array and exact blindness_attestation object at top level; they are required even if absent from the blank template.

## Host orchestration plan (hub metadata)

The hub uses the single pipeline entry point to dispatch this stage:

```sh
python -B -m scripts.l2_pipeline.hub dispatch S5 --jobs-root <jobs-root> --max-concurrency 4 --hub-outside-sandbox
```

`--max-concurrency` accepts 1..4 (default 4). Replace <jobs-root> with the
root containing the emitted stage/design job packets. Workers never dispatch.

## Machine-readable output contract
```json
{
  "oneOf": [
    {
      "type": "object",
      "required": [
        "persona",
        "design_id",
        "spec_sha256",
        "point",
        "interval",
        "interval_level",
        "rationale",
        "disclosures",
        "blindness_attestation"
      ],
      "properties": {
        "persona": {
          "type": "string"
        },
        "design_id": {
          "type": "string"
        },
        "spec_sha256": {
          "type": "string"
        },
        "point": {
          "type": "integer"
        },
        "interval": {
          "type": "array",
          "items": {
            "type": "integer"
          }
        },
        "interval_level": {
          "type": "number"
        },
        "rationale": {
          "type": "string"
        },
        "disclosures": {
          "type": "array"
        },
        "blindness_attestation": {
          "type": "object",
          "required": [
            "other_submissions_read",
            "outcome_associations_read"
          ],
          "additionalProperties": false,
          "properties": {
            "other_submissions_read": {
              "const": false
            },
            "outcome_associations_read": {
              "const": false
            }
          }
        }
      }
    },
    {
      "type": "object",
      "required": [
        "status",
        "entered_at",
        "central_ATT",
        "interval_80",
        "prob_positive",
        "prob_negative",
        "prob_near_zero",
        "prob_diagnostics_pass",
        "near_zero_threshold",
        "direction",
        "mechanism",
        "assumptions",
        "signature",
        "outcome_associations_seen_attestation",
        "freeze_manifest_sha256",
        "disclosures",
        "blindness_attestation",
        "design_id",
        "outcome_spec_id",
        "spec_path",
        "spec_sha256",
        "spec_json_path",
        "spec_json_sha256",
        "methods_path",
        "methods_sha256",
        "reviewer_perspective",
        "reviewer_identity",
        "unit"
      ],
      "properties": {
        "status": {
          "type": "string"
        },
        "entered_at": {
          "type": "string"
        },
        "central_ATT": {
          "type": "integer"
        },
        "interval_80": {
          "type": "array",
          "items": {
            "type": "integer"
          }
        },
        "prob_positive": {
          "type": "number"
        },
        "prob_negative": {
          "type": "number"
        },
        "prob_near_zero": {
          "type": "number"
        },
        "prob_diagnostics_pass": {
          "type": "number"
        },
        "near_zero_threshold": {
          "type": "number"
        },
        "direction": {
          "type": "string"
        },
        "mechanism": {
          "type": "string"
        },
        "assumptions": {
          "type": "array",
          "items": {
            "type": "string"
          }
        },
        "signature": {
          "type": "string"
        },
        "outcome_associations_seen_attestation": {
          "type": "boolean"
        },
        "freeze_manifest_sha256": {
          "type": "null"
        },
        "disclosures": {
          "type": "array"
        },
        "blindness_attestation": {
          "type": "object",
          "required": [
            "other_submissions_read",
            "outcome_associations_read"
          ],
          "additionalProperties": false,
          "properties": {
            "other_submissions_read": {
              "const": false
            },
            "outcome_associations_read": {
              "const": false
            }
          }
        },
        "design_id": {
          "type": "string"
        },
        "outcome_spec_id": {
          "type": "string"
        },
        "spec_path": {
          "type": "string"
        },
        "spec_sha256": {
          "type": "string"
        },
        "spec_json_path": {
          "type": "string"
        },
        "spec_json_sha256": {
          "type": "string"
        },
        "methods_path": {
          "type": "string"
        },
        "methods_sha256": {
          "type": "string"
        },
        "reviewer_perspective": {
          "type": "string"
        },
        "reviewer_identity": {
          "type": "string"
        },
        "unit": {
          "type": "string"
        }
      }
    }
  ]
}
```
