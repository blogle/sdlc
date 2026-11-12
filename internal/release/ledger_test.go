package release

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testIdentity() Identity {
	return Identity{Version: "1.2.3", Commit: strings.Repeat("a", 40), SourceMain: strings.Repeat("b", 40), Tree: strings.Repeat("c", 40), ChangelogDigest: strings.Repeat("d", 64), Fragments: []FragmentBinding{{Path: ".changes/fix.json", BlobSHA: strings.Repeat("e", 40)}}}
}

func TestLedgerRetryAndPublicationProofs(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state", "ledger.json")
	identity := testIdentity()
	ledger, err := LoadLedger(path, identity)
	if err != nil {
		t.Fatal(err)
	}
	if err := ledger.MarkPublished(); err == nil {
		t.Fatal("published before proof requirements")
	}
	digest := strings.Repeat("f", 64)
	if err := ledger.RecordArtifact("source.tar.gz", digest); err != nil {
		t.Fatal(err)
	}
	if err := ledger.RecordArtifact("source.tar.gz", digest); err != nil {
		t.Fatalf("identical retry failed: %v", err)
	}
	if err := ledger.RecordArtifact("source.tar.gz", strings.Repeat("0", 64)); err == nil {
		t.Fatal("conflicting artifact digest was accepted")
	}
	ledger.MarkTagged()
	ledger.ConsumerReceiptsRecorded = true
	if err := ledger.MarkPublished(); err != nil {
		t.Fatal(err)
	}
	if err := ledger.Save(path); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadLedger(path, identity)
	if err != nil || !loaded.Published {
		t.Fatalf("ledger did not resume: %#v, %v", loaded, err)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatal(err)
	}
}

func TestReceiptsRequireExactIdentityAndUniqueArtifacts(t *testing.T) {
	identity := testIdentity()
	ledger := NewLedger(identity)
	receipts := Receipts{Schema: 1, Artifacts: []ReceiptArtifact{{Name: "consumer.nar", Digest: "sha256:" + strings.Repeat("a", 64), Version: identity.Version, MergedSHA: identity.Commit, SourceMainSHA: identity.SourceMain}}}
	if err := ApplyReceipts(ledger, receipts, identity); err != nil {
		t.Fatal(err)
	}
	if !ledger.ConsumerReceiptsRecorded {
		t.Fatal("receipts were not recorded")
	}
	receipts.Artifacts = append(receipts.Artifacts, receipts.Artifacts[0])
	if err := ApplyReceipts(NewLedger(identity), receipts, identity); err == nil {
		t.Fatal("duplicate receipt name was accepted")
	}
	receipts.Artifacts = receipts.Artifacts[:1]
	receipts.Artifacts[0].SourceMainSHA = strings.Repeat("0", 40)
	if err := ApplyReceipts(NewLedger(identity), receipts, identity); err == nil {
		t.Fatal("receipt for another source was accepted")
	}
}

func TestLoadLedgerRejectsReorderedIdentityFragments(t *testing.T) {
	identity := testIdentity()
	identity.Fragments = []FragmentBinding{
		{Path: ".changes/a.json", BlobSHA: strings.Repeat("a", 40)},
		{Path: ".changes/b.json", BlobSHA: strings.Repeat("b", 40)},
	}
	path := filepath.Join(t.TempDir(), "ledger.json")
	ledger := NewLedger(identity)
	ledger.Fragments[0], ledger.Fragments[1] = ledger.Fragments[1], ledger.Fragments[0]
	if err := ledger.Save(path); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadLedger(path, identity); err == nil {
		t.Fatal("reordered fragment identity was accepted")
	}
}
