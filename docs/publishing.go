// Package docs exposes the shared Factory publishing procedure to the installer.
package docs

import _ "embed"

// FactoryPublishing is also shipped as a Nix Factory resource.
//
//go:embed FACTORY-PUBLISHING.md
var FactoryPublishing string
