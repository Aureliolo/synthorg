"""Does gating every merge hold off aggregation collapse as recursion deepens?

Nobody has published an answer. ARIES measured a real system collapsing at the
merge and never added a gate; the Six Sigma model argues gating converts the
compounding into an arbitrary-depth budget and was never run on a real
decomposition benchmark. No paper connects them.

This harness connects them. It sweeps the decomposition depth cap over one
externally defined specification, runs the completion-oracle gate at every merge
in one arm and at none in the other, and reports the fraction of leaf work that
survives to a correct merged result.

Every dependency of that measurement is deliberate and is documented where it
lives: the oracle is held out of every workspace (:mod:`.oracle`), the curve is
plotted against the depth a tree ACHIEVED rather than the cap it was allowed
(:mod:`.score`), the reviewer is bound to a different model from the executor
(:mod:`.manifest`), and both arms get the same number of merge attempts so the
gated arm cannot win by spending more (:mod:`.merge`).
"""
