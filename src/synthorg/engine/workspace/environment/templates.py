"""Default declaration templates seeded into a fresh project workspace.

Held as module constants (not packaged data files) so they are always
importable regardless of wheel packaging, and trivially unit-testable.
Each is a minimal, valid starting point the agents then maintain as the
project gains dependencies.
"""

from typing import Final

DEFAULT_MANIFEST_YAML: Final[str] = """\
# SynthOrg per-project environment declaration.
#
# Declares how this project's dev environment is provisioned so the agent
# sandbox, CI, and a fresh clone all build identically. Edit as the project
# gains dependencies; lockfiles pin exact versions for reproducibility.
#
#   language:       primary language of the deliverable.
#   lockfiles:      version-pinning files hashed into the env cache key.
#   setup_commands: ordered shell commands that install the toolchain and
#                   dependencies into the working tree. Run on provision and
#                   emitted into bootstrap.sh for a fresh clone.
#   test_command:   how a fresh clone runs the project's tests.
#   env:            toolchain / PATH additions applied on later tool calls.
language: python
lockfiles: []
setup_commands: []
test_command: "python -m pytest"
env: {}
"""

DEFAULT_DEVCONTAINER_JSON: Final[str] = """\
{
  "name": "synthorg-project",
  "image": "mcr.microsoft.com/devcontainers/base:bookworm",
  "postCreateCommand": "true"
}
"""

DEFAULT_FLAKE_NIX: Final[str] = """\
{
  description = "SynthOrg per-project reproducible dev environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [ ];
      };
    };
}
"""
