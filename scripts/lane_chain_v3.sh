#!/bin/bash
# Lane order v3 (after S4's rehearsal): (a) m2a GATE x1 on brief-b, f3, f4 (tag m2ag1); (b) the A/B: m2a f3 rebuilt WITHOUT f18_opening_fallback
# (same policy), GATE x1 (tag m2anofb_g1). The m2b slot (S3's Tool failure fix: brief-a, f1, f4, f5) is started by hand when that fix is merged.
cd "$(dirname "$0")/.." || exit 1
# the A/B first (f3 m2ag1 = 4/10 landed 16:33): the fallback-off f3, then the rest of m2a (f4 may already be running from the first gate)
GATE_END=${GATE_END:-1830} GATE_BRIEFS="f3-kestrel" scripts/gate_set.sh m2anofb m2anofb_g
GATE_END=${GATE_END:-1830} GATE_BRIEFS="f4-brightwell brief-b" scripts/gate_set.sh m2a m2ag
