# Contributor guidance

SDLC changes must preserve the public v2 delivery and consumer contract. Treat
workflow names, status contexts, permissions, triggers, release identity formats,
manifest schema 1, and Nix `apiVersion = 2` as compatibility boundaries.

## Design and implementation

- **One concept, one identity/home.** Keep vocabulary and representations
  consistent; place behavior with its domain owner and avoid competing copies.
- **Structure communicates architecture.** Use cohesive modules, small
  intentional interfaces, predictable dependency direction, and private details.
- **Minimize conceptual machinery.** Prefer direct code and existing primitives;
  do not add speculative abstractions to optimize line count or uniformity.
- **Boundaries remove knowledge.** Hide meaningful implementation details or
  establish an invariant; avoid forwarding layers that reveal internals.
- **Composition over coordination.** Combine primitives directly where natural;
  avoid registries, factories, and orchestrators without a distinct job.
- **Encode truth in structure.** Model mutually exclusive outcomes explicitly,
  exhaustively handle variants, validate inputs, and fail closed on uncertainty.
- **Give complexity an address.** Isolate necessary complexity beside the
  invariant that governs it, and explain non-obvious correctness arguments.
- **Execution is easy to follow.** Make data flow, effects, and control flow
  explicit; avoid clever indirection and unrelated bundled operations.
- **Correctness is demonstrable.** Preserve observable output, exit status,
  identity bytes, digests, generated files, and negative security invariants in
  deterministic tests. Claim only checks actually executed successfully.
- **Remove what no longer earns its existence.** Delete obsolete production
  code and dependencies after parity; reject dead wrappers and configurability.

Separate observation of external state, pure decisions, and side effects. Domain
packages must not depend on Cobra or invoke git, GitHub, or Nix implicitly.
Review the final diff for duplicated validation, leaked boundary knowledge,
unclear effects, dead code, and claims not supported by executed checks.

## Release constraints

- The SDLC repository's own changelog fragments are **patch-only**. Never add a
  major or minor fragment or otherwise authorize a major/minor self-release.
- Preserve patch/minor/major bump semantics for consumer repositories.
- Keep the `v2` compatibility channel, Nix API version 2, and existing release
  identity formats. Do not cut over or publish during implementation.
- Do not modify the dotfiles fleet or weaken authorization, publication, or
  reconciliation invariants to simplify a port.
