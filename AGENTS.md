# Contributor guidance

SDLC changes must preserve the public v2 delivery and consumer contract. Treat
workflow names, status contexts, permissions, triggers, release identity formats,
manifest schema 1, and Nix `apiVersion = 2` as compatibility boundaries.

## Design and implementation

- Give each domain concept one owner and one representation; keep handlers thin
  and dependency direction explicit.
- Separate observation of external state, pure decisions, and side effects.
  Domain packages must not depend on Cobra or invoke git, GitHub, or Nix
  implicitly.
- Prefer small cohesive interfaces and straightforward data flow. Avoid generic
  managers, registries, forwarding layers, and speculative abstractions.
- Validate untrusted inputs at boundaries, represent exclusive outcomes
  explicitly, and fail closed whenever a release invariant cannot be proved.
- Keep compatibility tests for observable output, exit status, identity bytes,
  digests, generated adapters, and negative security invariants.
- Review the final diff for duplicated validation, leaked boundary knowledge,
  unclear effects, dead code, and claims not supported by executed checks.

## Release constraints

- The SDLC repository's own changelog fragments are **patch-only**. Never add a
  major or minor fragment or otherwise authorize a major/minor self-release.
- Preserve patch/minor/major bump semantics for consumer repositories.
- Keep the `v2` compatibility channel, Nix API version 2, and existing release
  identity formats. Do not cut over or publish during implementation.
- Do not modify the dotfiles fleet or weaken authorization, publication, or
  reconciliation invariants to simplify a port.
