# Changelog

## [1.2.4] - 2026-10-11

- **fix**: stabilize release gate check-run retries without weakening authorization

## [1.2.3] - 2026-10-10

- **fix**: package the portable release coordinator and repair hosted publication workflow setup

## [1.2.2] - 2026-10-10

- **fix**: Reduce redundant Hestia CI rebuilding through shared cache warming, upstream filtering, and cache maintenance

## [1.2.1] - 2026-10-10

- **test**: Verify successor changelog history and multi-fragment output
- **docs**: Explain changelog fragment authoring and consumption
- **docs**: Document the live rolling-release publisher and exact-SHA recovery procedure

## [1.2.0] - 2026-10-10

- **feature**: Automatically enqueue Mergify-eligible pull requests while retaining GitHub ruleset and candidate validation gates
