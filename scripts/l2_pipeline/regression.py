"""Run pipeline checks with an explicit no-real-fit scope for prospective work."""
import argparse
import unittest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-archival-fits',action='store_true')
    args=parser.parse_args()
    suite=unittest.defaultTestLoader.discover('scripts/l2_pipeline/tests')
    def leaves(suite):
        for test in suite:
            if isinstance(test,unittest.TestSuite): yield from leaves(test)
            else: yield test
    excluded=[];selected=[]
    for test in leaves(suite):
        if args.no_archival_fits and '.ArchivedPrimaryEquivalence.' in test.id(): excluded.append(test.id())
        else: selected.append(test)
    print('EXCLUDED_REAL_ARCHIVAL_FITS: '+repr(excluded),flush=True)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__=='__main__': raise SystemExit(main())
