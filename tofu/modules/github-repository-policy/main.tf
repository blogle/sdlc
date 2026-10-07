resource "github_repository_ruleset" "canonical" {
  name        = "sdlc-default-branch"
  repository  = var.repository
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/${var.default_branch}"]
      exclude = []
    }
  }

  rules {
    deletion         = true
    non_fast_forward = true

    pull_request {
      dismiss_stale_reviews_on_push     = true
      require_code_owner_review         = false
      require_last_push_approval        = false
      required_approving_review_count   = 0
      required_review_thread_resolution = false
    }

    required_status_checks {
      strict_required_status_checks_policy = false

      dynamic "required_check" {
        for_each = toset(concat(["sdlc / pr-fast"], var.extra_required_status_checks))
        content {
          context = required_check.value
        }
      }
    }
  }
}

resource "github_issue_label" "integration_auto" {
  repository  = var.repository
  name        = "integration:auto"
  color       = "1d76db"
  description = "Authorize automatic integration"
}

resource "github_issue_label" "integration_review" {
  repository  = var.repository
  name        = "integration:review"
  color       = "fbca04"
  description = "Require human review before integration"
}
