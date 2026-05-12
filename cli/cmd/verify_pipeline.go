package cmd

import (
	"context"
	"fmt"
	"io"
	"maps"
	"strings"

	"github.com/Aureliolo/synthorg/cli/internal/config"
	"github.com/Aureliolo/synthorg/cli/internal/docker"
	"github.com/Aureliolo/synthorg/cli/internal/ui"
	"github.com/Aureliolo/synthorg/cli/internal/verify"
)

// imagesVerifyResult carries the outcome of running cache-aware verification
// over both SynthOrg and DHI image groups. Pins is the merged pin map
// containing bare-name SynthOrg keys ("backend", "web", ...) plus
// "dhi:<image>" / "dhi:<image>:platform|attestation|signature" DHI keys.
//
// The *Reverified flags tell callers which group was actually verified
// (cache miss). start writes compose only when SynthOrg was reverified;
// update always writes; callers also use the flags to skip an unnecessary
// state-save round-trip when nothing changed.
type imagesVerifyResult struct {
	Pins               map[string]string
	SynthOrgReverified bool
	DHIReverified      bool
}

// verifyImagesWithCache runs cache-aware verification of both image groups
// for the given state and renders the matching UI box per group:
//   - cache hit: "Verify <Group> Images (cached)" rendered, existing pins
//     for the group preserved into the returned merged map.
//   - cache miss: live verification box rendered, fresh pins folded in.
//
// Does NOT write compose or persist state. Callers own those side effects
// because their atomicity rules differ (start persists immediately, update
// defers persistence until after a successful pull). Callers MUST set
// state.VerifiedImageTag = state.ImageTag whenever they persist a result
// where SynthOrgReverified is true, otherwise hasSynthOrgDigests will
// reject the cache on the next invocation.
func verifyImagesWithCache(
	ctx context.Context,
	info docker.Info,
	state config.State,
	out, errOut *ui.UI,
) (imagesVerifyResult, error) {
	merged := make(map[string]string, len(state.VerifiedDigests))
	maps.Copy(merged, state.VerifiedDigests)

	res := imagesVerifyResult{Pins: merged}

	if hasSynthOrgDigests(state) {
		renderCachedSynthOrgBox(out, state)
	} else {
		pins, err := verifySynthOrgGroup(ctx, state, out, errOut)
		if err != nil {
			return imagesVerifyResult{}, err
		}
		// A miss replaces every prior SynthOrg pin (the bare-name keys),
		// because the new tag's images have new digests and the OLD pin
		// values are no longer trusted for the new refs.
		for k := range merged {
			if !strings.HasPrefix(k, "dhi:") {
				delete(merged, k)
			}
		}
		maps.Copy(merged, pins)
		res.SynthOrgReverified = true
	}

	if hasDHIDigests(state) {
		renderCachedDHIBox(out, state)
	} else {
		dhiResults, err := verifyDHIImages(ctx, info, state, out, errOut)
		if err != nil {
			return imagesVerifyResult{}, fmt.Errorf("DHI image verification failed: %w", err)
		}
		// A miss replaces every prior DHI pin: the binary-pinned index
		// moved (Renovate bump) or this is the first verification on
		// this install. Either way OLD dhi:* values do not describe the
		// images we just verified.
		for k := range merged {
			if strings.HasPrefix(k, "dhi:") {
				delete(merged, k)
			}
		}
		for _, r := range dhiResults {
			if indexDigest, ok := verify.DHIPinnedIndexDigest(r.Image); ok {
				merged["dhi:"+r.Image] = indexDigest
			}
			if r.Digest != "" {
				merged["dhi:"+r.Image+":platform"] = r.Digest
			}
			if r.AttDigest != "" {
				merged["dhi:"+r.Image+":attestation"] = r.AttDigest
			}
			if r.SigDigest != "" {
				merged["dhi:"+r.Image+":signature"] = r.SigDigest
			}
		}
		res.DHIReverified = true
	}

	res.Pins = merged
	return res, nil
}

// verifySynthOrgGroup runs SynthOrg cosign + SLSA verification for every
// SynthOrg image enabled by the given state and returns a bare-name pin
// map ready to be merged into state.VerifiedDigests. Renders the live
// "Verify SynthOrg Images" box.
//
// The provided ctx governs the verification deadline. Callers are
// responsible for applying any operator-specific timeout (the start path
// uses Tunables.ImageVerifyTimeout, the update path honours its
// --timeout flag).
func verifySynthOrgGroup(ctx context.Context, state config.State, out, errOut *ui.UI) (map[string]string, error) {
	imageRefs := verify.BuildImageRefs(state.ImageTag, state.Sandbox, state.FineTuning, state.FineTuneVariantOrDefault())
	labels := make([]string, len(imageRefs))
	for i, ref := range imageRefs {
		labels[i] = ref.Name()
	}
	lb := out.NewLiveBox("Verify SynthOrg Images", labels)

	results, err := verify.VerifyImages(ctx, verify.VerifyOptions{
		Images: imageRefs,
		Output: io.Discard,
		OnResult: func(i int, r verify.VerifyResult) {
			slsaIcon := ui.IconSuccess
			if !r.ProvenanceVerified {
				slsaIcon = ui.IconWarning
			}
			lb.UpdateLine(i, fmt.Sprintf("sig %s  slsa %s", ui.IconSuccess, slsaIcon))
		},
	})
	lb.Finish()

	if err != nil {
		if isTransportError(err) {
			errOut.HintError("Use --skip-verify for air-gapped environments")
		}
		return nil, fmt.Errorf("image verification failed: %w", err)
	}

	pins, err := digestPinMap(results)
	if err != nil {
		return nil, fmt.Errorf("digest pin map: %w", err)
	}
	return pins, nil
}

// synthOrgPins returns the bare-name subset of pins (the keys the compose
// template actually looks up). DHI keys ("dhi:*") are excluded because the
// compose template reads DHI digests via verify.DHIPinnedIndexDigest from
// the binary, not from this map. Dropping them here keeps the compose
// generator's input shape identical to the pre-merge contract.
func synthOrgPins(allPins map[string]string) map[string]string {
	out := make(map[string]string, len(allPins))
	for k, v := range allPins {
		if strings.HasPrefix(k, "dhi:") {
			continue
		}
		out[k] = v
	}
	return out
}
