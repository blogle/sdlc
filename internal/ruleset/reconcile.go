package ruleset

import (
	"fmt"
	"regexp"
)

type Remote interface {
	ReadRuleset(repository, name string, readOnly bool) (map[string]any, error)
	WriteRuleset(repository string, current, desired map[string]any) error
}

type Action string

const (
	PlanAction  Action = "plan"
	ApplyAction Action = "apply"
	CheckAction Action = "check"
)

type Request struct {
	Action      Action
	Repository  string
	Filename    string
	DryRun      bool
	Desired     map[string]any
	Automated   map[string]any
	Attestation map[string]any
}

type Plan struct {
	Repository      string         `json:"repository"`
	Action          string         `json:"action"`
	Ruleset         map[string]any `json:"ruleset"`
	AutomatedMerges map[string]any `json:"automated_merges,omitempty"`
}

type Result struct {
	Plan    *Plan
	Checked []string
}

var repositoryPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

func Reconcile(remote Remote, request Request) (Result, error) {
	if !repositoryPattern.MatchString(request.Repository) {
		return Result{}, fmt.Errorf("--repo must be OWNER/NAME")
	}
	if request.Desired == nil {
		return Result{}, fmt.Errorf("canonical ruleset policy is required")
	}
	if request.Action != PlanAction && request.Action != ApplyAction && request.Action != CheckAction {
		return Result{}, fmt.Errorf("unknown ruleset operation %s", request.Action)
	}
	return reconcileOne(remote, request, request.Action)
}

func reconcileOne(remote Remote, request Request, action Action) (Result, error) {
	current, err := remote.ReadRuleset(request.Repository, request.DesiredName(), action == CheckAction || request.DryRun)
	if err != nil {
		if request.DryRun {
			return Result{Plan: &Plan{Repository: request.Repository, Action: "read-unavailable", Ruleset: request.Desired}}, nil
		}
		return Result{}, err
	}
	equal := current != nil && Matches(current, request.Desired, false)
	if action == CheckAction && current != nil && request.Attestation != nil {
		equal = MatchesWithAttestation(current, request.Desired, request.Repository, request.Attestation, request.Filename != "default-branch.json")
	}
	operation := "create"
	if current != nil {
		operation = "update"
		if equal {
			operation = "no-op"
		}
	}
	if action == PlanAction || request.DryRun {
		plan := &Plan{Repository: request.Repository, Action: operation, Ruleset: request.Desired}
		if request.Filename == "default-branch.json" {
			plan.AutomatedMerges = request.Automated
		}
		return Result{Plan: plan}, nil
	}
	if action == CheckAction {
		if !equal {
			return Result{}, fmt.Errorf("canonical SDLC ruleset is %s on %s; run `sdlc ruleset apply --repo %s`", operation, request.Repository, request.Repository)
		}
		result := Result{Checked: []string{request.Repository}}
		if request.Filename == "default-branch.json" {
			request.Filename, request.Desired = "main-updates.json", request.Automated
			child, err := reconcileOne(remote, request, CheckAction)
			result.Checked = append(result.Checked, child.Checked...)
			return result, err
		}
		return result, nil
	}
	if !equal {
		if err := remote.WriteRuleset(request.Repository, current, request.Desired); err != nil {
			return Result{}, err
		}
	}
	result := Result{}
	if request.Filename == "default-branch.json" {
		childRequest := request
		childRequest.Filename, childRequest.Desired = "main-updates.json", request.Automated
		child, err := reconcileOne(remote, childRequest, ApplyAction)
		result.Checked = append(result.Checked, child.Checked...)
		if err != nil {
			return result, err
		}
	}
	request.Attestation = nil
	child, err := reconcileOne(remote, request, CheckAction)
	result.Checked = append(result.Checked, child.Checked...)
	return result, err
}

func (request Request) DesiredName() string { return fmt.Sprint(request.Desired["name"]) }
