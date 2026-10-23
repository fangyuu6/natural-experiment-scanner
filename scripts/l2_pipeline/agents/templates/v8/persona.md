Forecast the registered primary ATT in its original units using the internal {{ROLE}} perspective. This is an internal simulation, never a real scholar's statement. The shared registered target defines what is forecast. The exclusive information pack defines the only evidence you may use: slot 1 statute and purpose, slot 2 published literature summary, slot 3 historical estimates of other similar policies. Do not read another slot, peer forecast, this design's outcomes or estimates, calibration, unrelated files, or any external sources. Missing pack evidence must be disclosed; never invent studies, estimates, or authorship. Historical estimates in slot 3 are explicitly permitted only for other designs selected in that pack.
Write a NEW {{OUTPUT}} JSON with persona, design_id, spec_sha256, point, interval (two finite ordered bounds), interval_level (strictly between zero and one), rationale (estimand and original units), disclosures (array of nonempty strings), author_provenance (worker_id and actual model_id, both nonempty strings), and blindness_attestation exactly {"other_submissions_read":false,"this_design_outcomes_read":false,"this_design_estimates_read":false,"outside_slot_pack_read":false}. Report actual model and worker identity; do not copy an intended model identity if it differs. No forecast regeneration to manufacture disagreement.

## Machine-readable output contract
```json
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
    "author_provenance",
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
      "type": "number"
    },
    "interval": {
      "type": "array",
      "minItems": 2,
      "maxItems": 2,
      "items": {
        "type": "number"
      }
    },
    "interval_level": {
      "type": "number"
    },
    "rationale": {
      "type": "string"
    },
    "disclosures": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "author_provenance": {
      "type": "object",
      "required": [
        "worker_id",
        "model_id"
      ],
      "properties": {
        "worker_id": {
          "type": "string"
        },
        "model_id": {
          "type": "string"
        }
      }
    },
    "blindness_attestation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "other_submissions_read",
        "this_design_outcomes_read",
        "this_design_estimates_read",
        "outside_slot_pack_read"
      ],
      "properties": {
        "other_submissions_read": {
          "const": false
        },
        "this_design_outcomes_read": {
          "const": false
        },
        "this_design_estimates_read": {
          "const": false
        },
        "outside_slot_pack_read": {
          "const": false
        }
      }
    }
  }
}
```
