#!/bin/bash
# Lane order v4: m2a0 (= m2a recompiled with -f18_opening_fallback, same policies) GATE f3, f4 (tag m2a0g1); then the A/B repeat of the ORIGINAL m2a f3
# (fallback ON, tag m2a_fb_g1). The m2b slot (S3's Tool failure fix: brief-a, f1, f4, f5) and f6 on m2a0 are started by hand.
cd "$(dirname "$0")/.." || exit 1
GATE_END=${GATE_END:-1840} GATE_BRIEFS="f3-kestrel f4-brightwell" scripts/gate_set.sh m2a0 m2a0g
GATE_END=${GATE_END:-1840} GATE_BRIEFS="f3-kestrel" scripts/gate_set.sh m2a m2a_fb_g
