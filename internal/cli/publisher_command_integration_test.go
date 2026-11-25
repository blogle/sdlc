package cli

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	gitrepo "github.com/blogle/sdlc/internal/git"
	"github.com/blogle/sdlc/internal/release"
)

func TestPublisherCommandsResumeReceiptsAndRefuseIdentityConflicts(t *testing.T) {
	repo := makeReleaseRepo(t)
	identity, err := gitrepo.Open(repo.root).ValidateRelease(repo.candidate)
	if err != nil {
		t.Fatal(err)
	}
	ledgerPath := filepath.Join(t.TempDir(), "release-ledger.json")
	digest := strings.Repeat("a", 64)
	artifactArgs := []string{"release", "ledger", "artifact", "--ledger", ledgerPath, "--commit", repo.candidate, "--name", "source.tar.gz", "--digest", digest}
	if err := executeArgs(artifactArgs...); err != nil {
		t.Fatal(err)
	}
	if err := executeArgs(artifactArgs...); err != nil {
		t.Fatalf("idempotent artifact retry failed: %v", err)
	}
	conflictArgs := append([]string(nil), artifactArgs...)
	conflictArgs[len(conflictArgs)-1] = strings.Repeat("b", 64)
	if err := executeArgs(conflictArgs...); err == nil {
		t.Fatal("conflicting artifact digest was accepted")
	}

	receiptPath := filepath.Join(t.TempDir(), "receipts.json")
	writeReceipts(t, receiptPath, identity, "consumer.nar", "sha256:"+strings.Repeat("c", 64))
	if err := executeArgs("release", "receipts", "--ledger", ledgerPath, "--receipts", receiptPath, "--version", identity.Version, "--merged-sha", identity.Commit, "--source-sha", identity.SourceMain); err != nil {
		t.Fatal(err)
	}
	if err := executeArgs("release", "ledger", "published", "--ledger", ledgerPath); err != nil {
		t.Fatal(err)
	}
	ledger, err := release.LoadLedger(ledgerPath, identity)
	if err != nil {
		t.Fatal(err)
	}
	if !ledger.Tagged || !ledger.ConsumerReceiptsRecorded || !ledger.Published {
		t.Fatalf("publication lifecycle did not persist: %#v", ledger)
	}

	writeReceipts(t, receiptPath, identity, "consumer.nar", "sha256:"+strings.Repeat("d", 64))
	if err := executeArgs("release", "receipts", "--ledger", ledgerPath, "--receipts", receiptPath, "--version", identity.Version, "--merged-sha", identity.Commit, "--source-sha", identity.SourceMain); err == nil {
		t.Fatal("conflicting consumer receipt was accepted")
	}
	ledger, err = release.LoadLedger(ledgerPath, identity)
	if err != nil || ledger.Artifacts["consumer.nar"] != strings.Repeat("c", 64) || !ledger.Published {
		t.Fatalf("failed retry mutated ledger: %#v, %v", ledger, err)
	}
}

func TestInvokeCommandValidatesIdentityBeforeCallingNixAndRunsPublishApp(t *testing.T) {
	repo := makeReleaseRepo(t)
	identity, err := gitrepo.Open(repo.root).ValidateRelease(repo.candidate)
	if err != nil {
		t.Fatal(err)
	}
	bin, store := t.TempDir(), t.TempDir()
	if err := os.MkdirAll(filepath.Join(store, "bin"), 0o755); err != nil {
		t.Fatal(err)
	}
	called := filepath.Join(bin, "publish-called")
	program := filepath.Join(store, "bin", "publish")
	writeRepoFile(t, store, "bin/publish", "#!/bin/sh\nprintf called > \"$PUBLISH_CALLED\"\n")
	if err := os.Chmod(program, 0o755); err != nil {
		t.Fatal(err)
	}
	writeRepoFile(t, bin, "nix", `#!/bin/sh
printf '%s\n' "$*" >> "$NIX_LOG"
case "$*" in
  *"--apply"*) printf '%s' "$FAKE_STORE/publish.drv" ;;
  *"builtins.storeDir"*) printf '%s' "$FAKE_STORE" ;;
  *) printf '%s' "$FAKE_STORE/bin/publish" ;;
esac
`)
	writeRepoFile(t, bin, "nix-store", "#!/bin/sh\nexit 0\n")
	if err := os.Chmod(filepath.Join(bin, "nix"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(bin, "nix-store"), 0o755); err != nil {
		t.Fatal(err)
	}
	logPath := filepath.Join(bin, "nix.log")
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("FAKE_STORE", store)
	t.Setenv("NIX_LOG", logPath)
	t.Setenv("PUBLISH_CALLED", called)
	t.Setenv("SDLC_RELEASE_VERSION", identity.Version)
	t.Setenv("SDLC_RELEASE_MERGED_SHA", identity.Commit)
	t.Setenv("SDLC_RELEASE_SOURCE_SHA", identity.SourceMain)
	t.Setenv("SDLC_RELEASE_RECEIPTS", filepath.Join(bin, "receipts.json"))
	if err := executeArgs("release", "invoke", "--consumer-flake", ".", "--system", "x86_64-linux"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(called); err != nil {
		t.Fatalf("publish Nix app did not run: %v", err)
	}

	if err := os.Remove(called); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(logPath); err != nil {
		t.Fatal(err)
	}
	t.Setenv("SDLC_RELEASE_SOURCE_SHA", strings.Repeat("f", 40))
	if err := executeArgs("release", "invoke"); err == nil {
		t.Fatal("publisher identity mismatch was accepted")
	}
	if _, err := os.Stat(logPath); !os.IsNotExist(err) {
		t.Fatal("Nix was invoked before release identity validation")
	}
	if _, err := os.Stat(called); !os.IsNotExist(err) {
		t.Fatal("publish app ran for a mismatched release identity")
	}
}

func executeArgs(arguments ...string) error {
	command := New()
	command.SetArgs(arguments)
	command.SilenceErrors = true
	command.SilenceUsage = true
	return command.Execute()
}

func writeReceipts(t *testing.T, path string, identity release.Identity, name, digest string) {
	t.Helper()
	data, err := json.Marshal(release.Receipts{Schema: 1, Artifacts: []release.ReceiptArtifact{{
		Name: name, Digest: digest, Version: identity.Version, MergedSHA: identity.Commit, SourceMainSHA: identity.SourceMain,
	}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
