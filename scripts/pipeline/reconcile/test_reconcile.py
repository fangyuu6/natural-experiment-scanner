import copy
import unittest

from reconcile import adjudicate, align, canonical, compare, kappa, metrics, validate_submission, value_token


def row(value='{"date":"2000-01-01"}', status="known", ref="a:2"):
    return {"unit_id":"AA","field_id":"effective_date","record_id":"r",
            "version_id":"v","row_ref":ref,"value":value,"value_status":status,
            "scope_role":"state_roster","evidence_json":
            '[{"url":"https://official.example/law","locator":"sec1",'
            '"accessed_date":"2026-10-06","official_citation":"law1"}]'}


class ReconcileTests(unittest.TestCase):
    def test_set_and_number_canonicalization(self):
        self.assertEqual(canonical({"x":[1.0,2,1]}), {"x":[1,2]})
        self.assertNotEqual(canonical(True), canonical("true"))

    def test_legal_differences_preserved(self):
        self.assertNotEqual(value_token(row()), value_token(row('{"date":"2000"}')))
        a,b=row(),row(); b["coverage_end"]="2024-12-31"
        self.assertNotEqual(value_token(a),value_token(b))

    def test_confidence_and_quote_do_not_weight_agreement(self):
        a,b=row(),row(); b.update(confidence="low",quote="different")
        self.assertEqual(value_token(a),value_token(b))

    def test_unknown_never_positive_or_zero(self):
        a=row("unknown","unknown"); b=row("no verified history","unknown")
        self.assertEqual(compare(a,b)[0],"both-unknown")
        self.assertEqual(compare(a,row("0"))[0],"disputed")
        self.assertEqual(compare(a,None)[0],"disputed")

    def test_identical_needs_reviewed_evidence(self):
        self.assertEqual(compare(row(),row())[0],"disputed")
        self.assertEqual(compare(row(),row(),True)[0],"accepted_identical")
        a=row();a["evidence_json"]="[]"
        self.assertEqual(compare(a,row(),True)[0],"disputed")

    def test_silence_is_not_automatic_evidence_acceptance(self):
        self.assertEqual(compare(row("silent","not_stated"),row(),True)[0],"disputed")
        self.assertEqual(compare(row("silent","not_stated"),row("silent","not_stated"),True)[0],"disputed")

    def test_union_keeps_missing_discoveries(self):
        a=[row(ref="A:2"),row(ref="A:3")]; b=[row(ref="B:2")]
        groups=align(a,b,[row()])
        self.assertEqual(len(groups),2)
        self.assertIsNone(groups[1][1])
        self.assertEqual(groups[1][2],"unmatched_discovered_record")

    def test_crosswalk_cannot_reuse_original(self):
        with self.assertRaises(ValueError):
            align([row(ref="A:2")],[row(ref="B:2")],[row()],
                  [{"A_row_ref":"A:2","B_row_ref":"B:2","identity_basis":"same law"}])

    def test_kappa_formula_and_constant(self):
        self.assertAlmostEqual(kappa([("yes","yes"),("yes","no"),("no","no"),("no","yes")]),0)
        self.assertEqual(kappa([("yes","yes"),("no","no")]),1)
        self.assertIsNone(kappa([("yes","yes")]))

    def test_missing_and_unknown_union_denominator_and_strict_pause(self):
        rows=[{"A":row(),"B":row(),"initial_status":"accepted_identical", "final_status":"accepted_identical"} for _ in range(4)]
        rows.append({"A":row(),"B":None,"initial_status":"disputed","final_status":"disputed"})
        m=metrics(rows)
        self.assertEqual(m["raw_agreement_union"],.8)
        self.assertFalse(m["production_paused"])  # exactly 20% does not pause
        rows[0]["final_status"]="both-unknown"
        self.assertTrue(metrics(rows)["production_paused"])

    def test_rechecked_never_inflates_initial_agreement(self):
        r={"A":row('"A"'),"B":row('"B"'),"initial_status":"disputed","final_status":"accepted_rechecked"}
        m=metrics([r]);self.assertEqual(m["raw_agreements"],0)
        self.assertEqual(m["known_supported_agreements"],0)

    def test_component_ruling_cannot_resolve_whole_field(self):
        a,b=row('"A"'),row('"B"')
        review={"ruling_value":"A","evidence":["official:source"],"whole_field_resolved":False}
        self.assertEqual(adjudicate(a,b,review)[1],"disputed")
        review["whole_field_resolved"]=True
        initial,final,_,value=adjudicate(a,b,review)
        self.assertEqual((initial,final,value),("disputed","accepted_rechecked","A"))

    def test_evidence_acceptance_needs_quote_version_and_components(self):
        review={"ruling_value":"A","evidence":["official:source"],"whole_field_resolved":True,
                "exact_quote_matched":True,"matched_source_version_verified":True,
                "all_material_components_reviewed":True}
        a,b=row('"A"'),row('"silent"',"not_stated")
        self.assertEqual(adjudicate(a,b,review)[1],"accepted_evidence")
        del review["matched_source_version_verified"]
        self.assertEqual(adjudicate(a,b,review)[1],"accepted_rechecked")

    def test_recheck_requires_evidence_allows_sourced_zero(self):
        with self.assertRaises(ValueError):
            adjudicate(row(),None,{"whole_field_resolved":True,"ruling_value":0})
        self.assertEqual(adjudicate(row(),None,{"whole_field_resolved":True,
                         "ruling_value":0,"evidence":["official:coverage"]})[3],0)

    def test_status_kappa_uninformative_with_constant_margin(self):
        rows=[{"A":row("unknown","unknown"),"B":row(),"initial_status":"disputed","final_status":"disputed"},
              {"A":row("unknown","unknown"),"B":row("unknown","unknown"),"initial_status":"both-unknown","final_status":"both-unknown"}]
        self.assertIsNone(metrics(rows)["status_kappa_supplemental"])

    def test_invalid_clock_and_malformed_evidence_are_reported(self):
        a=row();a.update(evidence_json="broken",search_log_json="[]",minutes_spent="1",
                         date_lower="2000-02-30",date_upper="2000-03-01")
        issues=validate_submission([a],[a])
        self.assertTrue(any(i["issue"]=="invalid_declared_date" for i in issues))
        self.assertTrue(any(i["issue"]=="known_evidence_not_addressable" for i in issues))

    def test_partial_never_certificate_is_not_full_window(self):
        a=row();a.update(field_id="never_treated_certification",coverage_end="2024-12-31",
                         search_log_json="[]",minutes_spent="1")
        issues=validate_submission([a],[a])
        self.assertTrue(any(i["issue"]=="partial_negative_certificate_not_full_pilot_window" for i in issues))


if __name__ == "__main__":
    unittest.main()
