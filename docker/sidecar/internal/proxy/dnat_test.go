package proxy_test

import (
	"strings"
	"testing"

	"github.com/Aureliolo/synthorg/sidecar/internal/proxy"
)

const (
	testProxyPort = 15001
	testOwnerUID  = 10002
)

// containsAll reports whether joined carries every one of want.
func containsAll(joined string, want []string) bool {
	for _, fragment := range want {
		if !strings.Contains(joined, fragment) {
			return false
		}
	}
	return true
}

// indexOfRule returns the position of the first planned command containing
// every one of want, or -1.
func indexOfRule(plan [][]string, want ...string) int {
	for i, argv := range plan {
		if containsAll(strings.Join(argv, " "), want) {
			return i
		}
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

func TestPlanRulesCarriesEachRule(t *testing.T) {
	cases := []struct {
		name       string
		proxyPort  uint16
		dnsAllowed bool
		fragments  []string
		wantInPlan bool
		why        string
	}{
		{
			name:       "redirects to the configured port",
			proxyPort:  9999,
			dnsAllowed: true,
			fragments:  []string{"--to-destination 127.0.0.1:9999"},
			wantInPlan: true,
			why:        "a redirect to a port nothing listens on drops every dial",
		},
		{
			name:       "leaves loopback alone",
			proxyPort:  testProxyPort,
			dnsAllowed: true,
			fragments:  []string{"-j DNAT", "! -d 127.0.0.0/8"},
			wantInPlan: true,
			why: "the relay dials its upstream over loopback, and the health " +
				"and admin listeners are loopback-only",
		},
		{
			name:       "closes IPv6",
			proxyPort:  testProxyPort,
			dnsAllowed: true,
			fragments:  []string{"-P OUTPUT DROP"},
			wantInPlan: true,
			why: "every other rule filters v4 only, so a reachable v6 stack " +
				"would be a way around the allowlist",
		},
		{
			name:       "leaves DNS alone when allowed",
			proxyPort:  testProxyPort,
			dnsAllowed: true,
			fragments:  []string{"--dport 53"},
			wantInPlan: false,
			why:        "port 53 filtering belongs to the disallowed case alone",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			plan := proxy.PlanRules(tc.proxyPort, tc.dnsAllowed, testOwnerUID)
			found := indexOfRule(plan, tc.fragments...) >= 0
			if found != tc.wantInPlan {
				t.Errorf(
					"rule %q present = %v, want %v (%s); plan %q",
					tc.fragments, found, tc.wantInPlan, tc.why, plan,
				)
			}
		})
	}
}

func TestPlanRulesKeepsBlockedTCPDNSOutOfTheRedirect(t *testing.T) {
	// nat OUTPUT runs before filter OUTPUT for a locally generated packet, so
	// a TCP resolver dial that the redirect has already rewritten reaches the
	// filter rule carrying the relay port and never matches --dport 53. The
	// exemption is what leaves the drop something to match.
	plan := proxy.PlanRules(testProxyPort, false, testOwnerUID)
	exempt := indexOfRule(plan, "-t nat", "-p tcp", "--dport 53", "-j RETURN")
	redirect := indexOfRule(plan, "-j DNAT")
	if exempt < 0 {
		t.Fatalf("plan %q does not exempt TCP DNS from the redirect", plan)
	}
	if exempt > redirect {
		t.Errorf("exemption is at %d, after the redirect at %d", exempt, redirect)
	}
}

func TestPlanRulesRedirectsTCPDNSWhenDNSIsAllowed(t *testing.T) {
	// The exemption exists to let the drop fire; with DNS allowed there is no
	// drop, so exempting port 53 would carve an unfiltered hole in the
	// redirect that the allowlist never sees.
	plan := proxy.PlanRules(testProxyPort, true, testOwnerUID)
	if exempt := indexOfRule(plan, "-t nat", "--dport 53"); exempt >= 0 {
		t.Errorf("plan %q exempts port 53 from the redirect with DNS allowed", plan)
	}
}

func TestPlanRulesWaitsForTheLock(t *testing.T) {
	// Without a wait the front end exits the moment the lock is held rather
	// than waiting for it, so the setup timeout never applies and transient
	// host contention fails startup.
	for _, argv := range proxy.PlanRules(testProxyPort, false, testOwnerUID) {
		if indexOfArg(argv, "-w") != 1 {
			t.Errorf("command %q does not wait for the xtables lock", argv)
		}
	}
}

// indexOfArg returns the position of want in argv, or -1.
func indexOfArg(argv []string, want string) int {
	for i, arg := range argv {
		if arg == want {
			return i
		}
	}
	return -1
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
