package main

import (
	"errors"
	"fmt"
	"os"
)

// runCeremony is what remains of `factory-setup ceremony`. Up to Factory 0.7 a
// founder signed one machine-wide genesis here. The pinned Factory starts a
// run with harness/factory.sh and needs no keys, genesis or signatures.
func runCeremony([]string) error {
	fmt.Fprint(os.Stderr, `factory-setup ceremony is retired.

The Factory no longer uses keys, a genesis or signatures. /factory writes the
task file and starts the run with harness/factory.sh; the human's own words are
the authority. A genesis made by an earlier ceremony under ~/.factory/authority
is no longer used; it can stay or be moved aside.
`)
	return errors.New("ceremony is retired; see above")
}
