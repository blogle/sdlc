# Shared SDLC workflow

- Declare project build and test targets in `ci.nix.json`; keep language-specific implementation in the project.
- Run `nix develop -c just check`, then `nix run github:blogle/sdlc/v1#sdlc -- run pr-fast`.
- Add `.changes/<topic>.json` only for user-visible/release-worthy changes, with `type`, `semver` (`patch|minor|major`), and `summary`.
- Treat `integration:review` as requiring an authorization check bound to the current PR head SHA. Never authorize from comment text.
