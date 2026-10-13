# Release publisher

The release workflow runs in the consumer repository with its locked SDLC
revision. The release App creates and refreshes one bot-owned release PR, owns
the release-gate check, and asks GitHub to squash-merge without bypassing branch
protection. It receives no blanket ruleset bypass.

The release check is attached to GitHub's test-merge commit, whose parents bind
the exact current default-branch tip and release PR head. The merger verifies
those parents and checks immediately before requesting merge. The canonical
ruleset requires both `sdlc / check` and the App-owned `sdlc / release-gate`.
GitHub evaluates required checks for the current test-merge commit, so a base
advance cannot reuse a gate result from an earlier candidate. Status freshness
stays non-strict; Mergify continues to own feature-candidate freshness.

After merge, the publisher checks out the actual squash SHA and confirms its
parent matches the manifest source. It creates or verifies the version tag and
source archive, then calls `apps.<system>.sdlc-publish` from that same checkout.
Consumers own only application artifact publication. Immutable image tags must
match their existing registry digest on retry. Mutable aliases may advance
only after immutable artifacts are verified. The GitHub Release is finalized
after the publication hook succeeds.

The fleet controller distributes the release App credentials. Release
activation remains a consumer repository variable so operators can verify the
workflows, protection, and App installation before enabling unattended
publication.
