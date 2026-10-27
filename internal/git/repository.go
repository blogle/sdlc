package git

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/blogle/sdlc/internal/changelog"
	"github.com/blogle/sdlc/internal/release"
)

type Repository struct{ Root string }

func Open(root string) Repository { return Repository{Root: root} }

func (repository Repository) Snapshot(sourceSHA, date string) (*release.Manifest, error) {
	if !release.ValidSHA(sourceSHA) {
		return nil, fmt.Errorf("--source-sha must be a full commit SHA")
	}
	if _, err := repository.Text("rev-parse", sourceSHA); err != nil {
		return nil, fmt.Errorf("source SHA %s is not available", sourceSHA)
	}
	head, err := repository.Text("rev-parse", "HEAD")
	if err != nil {
		return nil, err
	}
	if head != sourceSHA {
		return nil, fmt.Errorf("snapshot must be generated from the source SHA checkout")
	}
	entries, err := changelog.ReadFragments(repository.Root, nil)
	if err != nil {
		return nil, err
	}
	if len(entries) == 0 {
		return nil, nil
	}
	previous, err := repository.previousManifest(sourceSHA)
	if err != nil {
		return nil, err
	}
	input := release.SnapshotInput{SourceSHA: sourceSHA, Date: date, Fragments: make([]release.SourceFragment, len(entries))}
	if previous != nil {
		input.PreviousVersion = previous.Version
		if previous.SourceMainSHA != "" {
			input.PriorBoundary = previous.SourceMainSHA
		}
	}
	if input.PreviousVersion == "" {
		tags, err := repository.Text("tag", "--list", "v[0-9]*")
		if err == nil {
			input.Tags = strings.Fields(tags)
		}
	}
	if input.Date == "" {
		input.Date, err = repository.Text("show", "-s", "--format=%cs", sourceSHA)
		if err != nil {
			return nil, err
		}
	}
	for index, entry := range entries {
		blob, err := repository.Text("rev-parse", sourceSHA+":"+entry.Path)
		if err != nil {
			return nil, err
		}
		input.Fragments[index] = release.SourceFragment{Entry: entry, BlobSHA: blob}
	}
	input.OldChangelog, err = os.ReadFile(filepath.Join(repository.Root, "CHANGELOG.md"))
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	plan, err := release.PlanSnapshot(input)
	if err != nil {
		return nil, err
	}
	manifestBytes, err := plan.Manifest.JSON()
	if err != nil {
		return nil, err
	}
	if err := os.WriteFile(filepath.Join(repository.Root, "CHANGELOG.md"), plan.Changelog, 0o644); err != nil {
		return nil, err
	}
	manifestPath := filepath.Join(repository.Root, ".sdlc", "release.json")
	if err := os.MkdirAll(filepath.Dir(manifestPath), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(manifestPath, manifestBytes, 0o644); err != nil {
		return nil, err
	}
	for _, path := range plan.Consumed {
		if err := os.Remove(filepath.Join(repository.Root, path)); err != nil {
			return nil, err
		}
	}
	return &plan.Manifest, nil
}

func (repository Repository) ValidateRelease(commit string) (release.Identity, error) {
	if !release.ValidSHA(commit) {
		return release.Identity{}, fmt.Errorf("merged commit must be a 40-character lowercase SHA")
	}
	parents, err := repository.Text("rev-list", "--parents", "-n1", commit)
	if err != nil {
		return release.Identity{}, err
	}
	fields := strings.Fields(parents)
	if len(fields) != 2 {
		return release.Identity{}, fmt.Errorf("merged release commit must have exactly one parent")
	}
	parent := fields[1]
	sourceBlobs, err := repository.tree(parent)
	if err != nil {
		return release.Identity{}, err
	}
	mergedBlobs, err := repository.tree(commit)
	if err != nil {
		return release.Identity{}, err
	}
	changed, err := repository.Bytes("diff", "--name-only", "-z", parent, commit)
	if err != nil {
		return release.Identity{}, err
	}
	changedPaths := map[string]bool{}
	for _, path := range strings.Split(string(changed), "\x00") {
		if path != "" {
			changedPaths[path] = true
		}
	}
	treeSHA, err := repository.Text("rev-parse", commit+"^{tree}")
	if err != nil {
		return release.Identity{}, err
	}
	manifestBytes, err := repository.Bytes("show", commit+":.sdlc/release.json")
	if err != nil {
		return release.Identity{}, err
	}
	var manifest release.Manifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		return release.Identity{}, fmt.Errorf("release manifest is invalid JSON: %w", err)
	}
	changelogBytes, err := repository.Bytes("show", commit+":CHANGELOG.md")
	if err != nil {
		return release.Identity{}, err
	}
	return release.ValidateIdentity(manifest, commit, release.Tree{
		Parent: parent, TreeSHA: treeSHA, SourceBlobs: sourceBlobs, MergedBlobs: mergedBlobs,
		ChangedPaths: changedPaths, Changelog: changelogBytes,
	})
}

func (repository Repository) ParentSHA(commit string) (string, error) {
	return repository.Text("rev-parse", commit+"^")
}

func (repository Repository) ReplaySnapshot(sourceSHA string) (*release.Manifest, error) {
	worktree, err := os.MkdirTemp("", "release-gate-")
	if err != nil {
		return nil, err
	}
	if err := os.Remove(worktree); err != nil {
		return nil, err
	}
	if _, err := repository.Text("worktree", "add", "--detach", worktree, sourceSHA); err != nil {
		return nil, err
	}
	defer func() { _, _ = repository.Text("worktree", "remove", "--force", worktree) }()
	manifest, err := Open(worktree).Snapshot(sourceSHA, "")
	if err != nil {
		return nil, fmt.Errorf("coordinator snapshot replay failed: %w", err)
	}
	if manifest == nil {
		return nil, fmt.Errorf("coordinator snapshot produced no release manifest")
	}
	return manifest, nil
}

func (repository Repository) FetchReleaseBranches(defaultBranch, releaseBranch string) error {
	_, err := repository.Text("fetch", "--prune", "--tags", "origin", defaultBranch, releaseBranch)
	return err
}

func (repository Repository) AddWorktree(path, commit string) error {
	_, err := repository.Text("worktree", "add", "--detach", path, commit)
	return err
}

func (repository Repository) RemoveWorktree(path string) error {
	_, err := repository.Text("worktree", "remove", "--force", path)
	return err
}

// CommitAndPushReleaseCandidate creates the deterministic candidate and updates
// its replaceable branch only while the observed remote branch lease holds.
func (repository Repository) CommitAndPushReleaseCandidate(branch, commitDate string) (string, bool, error) {
	if _, err := repository.Text("config", "user.name", "sdlc-release[bot]"); err != nil {
		return "", false, err
	}
	if _, err := repository.Text("config", "user.email", "sdlc-release[bot]@users.noreply.github.com"); err != nil {
		return "", false, err
	}
	if _, err := repository.Text("add", "CHANGELOG.md", ".sdlc/release.json", ".changes"); err != nil {
		return "", false, err
	}
	command := exec.Command("git", "commit", "-m", "docs: prepare rolling release candidate")
	command.Dir = repository.Root
	command.Env = append(os.Environ(), "GIT_AUTHOR_DATE="+commitDate, "GIT_COMMITTER_DATE="+commitDate)
	if output, err := command.CombinedOutput(); err != nil {
		return "", false, fmt.Errorf("create deterministic release candidate: %w: %s", err, strings.TrimSpace(string(output)))
	}
	candidate, err := repository.Text("rev-parse", "HEAD")
	if err != nil {
		return "", false, err
	}
	lease := "--force-with-lease=refs/heads/" + branch + ":"
	if old, err := repository.Text("rev-parse", "--verify", "refs/remotes/origin/"+branch); err == nil {
		lease += old
	}
	push := exec.Command("git", "-C", repository.Root, "push", lease, "origin", "HEAD:refs/heads/"+branch)
	push.Stdout, push.Stderr = io.Discard, io.Discard
	if err := push.Run(); err != nil {
		return candidate, false, nil
	}
	return candidate, true, nil
}

func (repository Repository) previousManifest(sourceSHA string) (*previousRelease, error) {
	data, err := repository.Bytes("show", sourceSHA+":.sdlc/release.json")
	if err != nil {
		return nil, nil
	}
	var previous previousRelease
	if err := json.Unmarshal(data, &previous); err != nil {
		return nil, fmt.Errorf(".sdlc/release.json at %s: invalid JSON: %w", sourceSHA, err)
	}
	if !changelog.ValidVersion(previous.Version) {
		return nil, fmt.Errorf(".sdlc/release.json at %s: missing valid version", sourceSHA)
	}
	return &previous, nil
}

type previousRelease struct {
	Version       string `json:"version"`
	SourceMainSHA string `json:"source_main_sha"`
}

func (repository Repository) tree(commit string) (map[string]string, error) {
	data, err := repository.Bytes("ls-tree", "-r", "-z", commit)
	if err != nil {
		return nil, err
	}
	blobs := map[string]string{}
	for _, record := range strings.Split(string(data), "\x00") {
		if record == "" {
			continue
		}
		metadata, path, ok := strings.Cut(record, "\t")
		if !ok {
			return nil, fmt.Errorf("ambiguous git tree record")
		}
		fields := strings.Fields(metadata)
		if len(fields) != 3 {
			return nil, fmt.Errorf("ambiguous git tree metadata")
		}
		if fields[1] == "blob" {
			blobs[path] = fields[2]
		}
	}
	return blobs, nil
}

func (repository Repository) Text(args ...string) (string, error) {
	data, err := repository.Bytes(args...)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

func (repository Repository) Bytes(args ...string) ([]byte, error) {
	command := exec.Command("git", args...)
	command.Dir = repository.Root
	data, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("git cannot prove release invariant: git %s", strings.Join(args, " "))
	}
	return data, nil
}
