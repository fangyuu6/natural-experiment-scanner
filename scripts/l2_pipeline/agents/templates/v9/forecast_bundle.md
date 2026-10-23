You are the single author of an internal forecast bundle (REDESIGN R2). The binding contract lists the members of the registered roster in `bundle_members`; each member is one information-separated forecast of the same registered primary ATT, in its original units, written from the internal perspective named by the member (an internal simulation, never a real scholar's statement). The shared registered target defines what is forecast. Each member has an exclusive information pack (slot 1 statute and purpose, slot 2 published literature summary, slot 3 historical estimates of other similar policies); the packs are embedded below.

For each member, forecast using only that member's own pack and the shared registered target, as if the other packs did not exist. Do not copy numbers or wording between members, do not tune a member to agree or to disagree with another, and do not regenerate forecasts. Missing pack evidence must be disclosed in that member's disclosures; never invent studies, estimates or authorship. Do not read this design's outcomes or estimates, calibration, peer forecasts of other designs, unrelated files or any external sources.

Because you read all packs, every member's blindness_attestation is exactly {"other_submissions_read":false,"this_design_outcomes_read":false,"this_design_estimates_read":false,"outside_slot_pack_read":true}. This overrides any single-member instruction that asks for false on outside_slot_pack_read. Report your actual worker and model identity once per member (author_provenance with the same worker_id in all members; model_id the model you actually are); do not copy an intended model identity if it differs.

Write a NEW {{OUTPUT}} JSON object with design_id, spec_sha256, and members: an object with exactly one entry per key of bundle_members. Each member ledger has persona (the member key), design_id, spec_sha256, point, interval (two finite ordered bounds), interval_level (strictly between zero and one), rationale (estimand and original units), disclosures (array of nonempty strings), author_provenance (worker_id and model_id, both nonempty strings) and blindness_attestation as above. The collector writes each member ledger to its own job and checks it with that job's contract; one invalid member rejects the whole bundle.

## Machine-readable output contract
```json
{
  "type": "object",
  "required": [
    "design_id",
    "spec_sha256",
    "members"
  ],
  "properties": {
    "design_id": {
      "type": "string"
    },
    "spec_sha256": {
      "type": "string"
    },
    "members": {
      "type": "object"
    }
  }
}
```
