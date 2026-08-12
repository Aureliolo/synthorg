// Package proxy implements the transparent TCP proxy with DNAT support.
package proxy

import (
	"context"
	"fmt"
	"net"
	"os/exec"
	"strconv"
	"strings"
)

const (
	// The nft front ends reach netfilter over netlink and need CAP_NET_ADMIN
	// alone. The legacy ones drive it through a raw socket and additionally
	// need CAP_NET_RAW, which this container is deliberately not granted: as
	// root with NET_ADMIN only, a legacy invocation cannot even initialise
	// the nat table.
	iptablesBinary  = "iptables-nft"
	ip6tablesBinary = "ip6tables-nft"

	loopbackCIDR = "127.0.0.0/8"
	dnsPort      = "53"

	// Bounded wait for the xtables lock. Without it the front end exits the
	// moment the lock is held rather than waiting, so transient host
	// contention fails startup outright; the setup timeout above bounds the
	// whole plan, so the per-command wait stays well inside it.
	lockWaitSeconds = "5"
)

// PlanRules returns the argv of every command InstallRules runs, in order.
//
// ownerUID is the account the relay serves under, whose traffic is exempted
// so its own upstream dials are not redirected back into it. Order is
// load-bearing throughout: netfilter takes the first matching rule, so an
// exemption appended after the rule it exempts from never runs.
func PlanRules(proxyPort uint16, dnsAllowed bool, ownerUID int) [][]string {
	owner := strconv.Itoa(ownerUID)
	plan := [][]string{
		{
			iptablesBinary, "-t", "nat", "-A", "OUTPUT", "-p", "tcp",
			"-m", "owner", "--uid-owner", owner, "-j", "RETURN",
		},
	}

	if !dnsAllowed {
		// Ahead of the redirect below, because nat OUTPUT runs before filter
		// OUTPUT for a locally generated packet: once the destination has been
		// rewritten to the relay port, the filter rule matching --dport 53
		// sees the new port and can never fire. Left in the redirect, a TCP
		// resolver dial would reach the relay and be judged by the allowlist,
		// which is a different policy from the one dns_allowed=false states.
		plan = append(plan, []string{
			iptablesBinary, "-t", "nat", "-A", "OUTPUT", "-p", "tcp",
			"--dport", dnsPort, "-j", "RETURN",
		})
	}

	plan = append(plan, []string{
		iptablesBinary, "-t", "nat", "-A", "OUTPUT", "-p", "tcp",
		"!", "-d", loopbackCIDR, "-j", "DNAT",
		"--to-destination", fmt.Sprintf("127.0.0.1:%d", proxyPort),
	})

	if !dnsAllowed {
		for _, proto := range []string{"udp", "tcp"} {
			plan = append(plan,
				[]string{
					iptablesBinary, "-A", "OUTPUT", "-p", proto,
					"--dport", dnsPort, "-m", "owner", "--uid-owner", owner,
					"-j", "ACCEPT",
				},
				[]string{
					iptablesBinary, "-A", "OUTPUT", "-p", proto,
					"--dport", dnsPort, "-j", "DROP",
				},
			)
		}
	}

	// Every rule above filters IPv4, so a reachable v6 stack would be a path
	// around the allowlist rather than a feature nobody implemented.
	plan = append(plan, []string{ip6tablesBinary, "-P", "OUTPUT", "DROP"})
	return withLockWait(plan)
}

// withLockWait returns *plan* with a bounded xtables-lock wait on every command.
func withLockWait(plan [][]string) [][]string {
	waited := make([][]string, 0, len(plan))
	for _, argv := range plan {
		with := make([]string, 0, len(argv)+2)
		with = append(with, argv[0], "-w", lockWaitSeconds)
		with = append(with, argv[1:]...)
		waited = append(waited, with)
	}
	return waited
}

// InstallRules runs a plan, stopping at the first command that fails.
//
// The rules live in the container's own network namespace and are reclaimed
// with it, so there is no inverse to run at shutdown, and by then the process
// has given up the capability that installed them.
func InstallRules(ctx context.Context, plan [][]string) error {
	for _, argv := range plan {
		out, err := exec.CommandContext(ctx, argv[0], argv[1:]...).CombinedOutput()
		if err != nil {
			// The tool's own diagnosis is the only thing separating a missing
			// capability from a missing kernel module or an unwritable lock
			// file; an exit status names none of the three.
			return fmt.Errorf(
				"%s: %w: %s",
				strings.Join(argv, " "), err, strings.TrimSpace(string(out)),
			)
		}
	}
	return nil
}

// GetOriginalDst extracts the original destination address from a
// DNAT-redirected TCP connection using SO_ORIGINAL_DST.
// This is Linux-specific and requires the connection to have been
// redirected by an iptables DNAT rule.
func GetOriginalDst(conn net.Conn) (ip string, port uint16, err error) {
	return lookupOriginalDst(conn)
}
