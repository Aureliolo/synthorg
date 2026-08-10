package proxy_test

import (
	"slices"
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/proxy"
)

const (
	testProxyPort = 15001
	testOwnerUID  = 10002
)

// indexOfRule returns the position of the first planned command containing
// every one of want, or -1.
func indexOfRule(plan [][]string, want ...string) int {
	for i, argv := range plan {
		joined := strings.Join(argv, " ")
		if slices.ContainsFunc(want, func(w string) bool {
			return !strings.Contains(joined, w)
		}) {
			continue
		}
		return i
	}
	return -1
}

func TestPlanRulesDrivesTheNftFrontEnd(t *testing.T) {
	// The legacy front end reaches the kernel through a raw socket and so
	// needs CAP_NET_RAW on top of CAP_NET_ADMIN; the container is granted
	// CAP_NET_ADMIN alone, so a legacy invocation cannot initialise a table.
	for _, argv := range proxy.PlanRules(testProxyPort, false, testOwnerUID) {
		if !strings.HasSuffix(argv[0], "-nft") {
			t.Errorf("command %q runs %q, not an nft front end", argv, argv[0])
		}
	}
}

func TestPlanRulesExemptsTheServingAccount(t *testing.T) {
	// Setup runs as uid 0 and gives that up afterwards, so the skip rule has
	// to name the account the relay will dial out from. Reading the current
	// euid instead would exempt root and redirect the proxy into itself.
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	skip := indexOfRule(plan, "--uid-owner 10002", "-j RETURN")
	if skip < 0 {
		t.Fatalf("no owner-skip rule in plan %q", plan)
	}
}

func TestPlanRulesSkipsBeforeItRedirects(t *testing.T) {
	// Netfilter takes the first match, so a skip appended after the redirect
	// is never reached and the relay's own upstream dials come back to it.
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	skip := indexOfRule(plan, "-j RETURN")
	redirect := indexOfRule(plan, "-j DNAT")
	if skip < 0 || redirect < 0 {
		t.Fatalf("plan %q lacks a skip or a redirect", plan)
	}
	if skip > redirect {
		t.Errorf("skip is at %d, after the redirect at %d", skip, redirect)
	}
}

func TestPlanRulesRedirectsToTheConfiguredPort(t *testing.T) {
	plan := proxy.PlanRules(9999, true, testOwnerUID)
	if indexOfRule(plan, "--to-destination 127.0.0.1:9999") < 0 {
		t.Errorf("plan %q does not redirect to the configured port", plan)
	}
}

func TestPlanRulesLeavesLoopbackAlone(t *testing.T) {
	// The relay dials its own upstream through loopback, and the health and
	// admin listeners are loopback-only.
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	if indexOfRule(plan, "-j DNAT", "! -d 127.0.0.0/8") < 0 {
		t.Errorf("plan %q redirects loopback traffic", plan)
	}
}

func TestPlanRulesLeavesDNSAloneWhenAllowed(t *testing.T) {
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	if indexOfRule(plan, "--dport 53") >= 0 {
		t.Errorf("plan %q filters DNS although it is allowed", plan)
	}
}

func TestPlanRulesAcceptsItsOwnDNSBeforeDroppingTheRest(t *testing.T) {
	// Blocking DNS outright would also block the allowlist's own resolution,
	// so the sidecar exempts itself; first-match again makes order the whole
	// difference between exempting and not.
	plan := proxy.PlanRules(testProxyPort, false, testOwnerUID)
	for _, proto := range []string{"udp", "tcp"} {
		accept := indexOfRule(plan, "-p "+proto, "--dport 53", "-j ACCEPT")
		drop := indexOfRule(plan, "-p "+proto, "--dport 53", "-j DROP")
		if accept < 0 || drop < 0 {
			t.Fatalf("plan %q lacks %s DNS rules", plan, proto)
		}
		if accept > drop {
			t.Errorf("%s accept is at %d, after the drop at %d", proto, accept, drop)
		}
	}
}

func TestPlanRulesClosesIPv6(t *testing.T) {
	// Every other rule filters v4 only, so a reachable v6 stack would be a
	// way around the allowlist rather than a missing feature.
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	if indexOfRule(plan, "-P OUTPUT DROP") < 0 {
		t.Errorf("plan %q leaves IPv6 output open", plan)
	}
}
