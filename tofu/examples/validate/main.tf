terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }
}

module "repository_policy" {
  source = "../../modules/github-repository-policy"

  repository                   = "example"
  default_branch               = "main"
  extra_required_status_checks = ["security / scan"]
}
