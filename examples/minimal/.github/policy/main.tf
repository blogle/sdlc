terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.0"
    }
  }

  # Choose and configure the backend here. Credentials and backend-specific
  # values are supplied by the repository's workflow environment/secrets.
  backend "s3" {}
}

provider "github" {
  owner = var.owner
}

variable "owner" {
  type = string
}

variable "repository" {
  type = string
}

variable "default_branch" {
  type = string
}

module "repository_policy" {
  source = "git::https://github.com/blogle/sdlc.git//tofu/modules/github-repository-policy?ref=v1.0.0"

  repository                   = var.repository
  default_branch               = var.default_branch
  extra_required_status_checks = []
}
