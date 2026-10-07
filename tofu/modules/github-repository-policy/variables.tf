variable "repository" {
  description = "GitHub repository name (without owner)."
  type        = string
}

variable "default_branch" {
  description = "Default branch name managed by this policy."
  type        = string
}

variable "extra_required_status_checks" {
  description = "Additional required check contexts beyond sdlc / pr-fast."
  type        = list(string)
  default     = []
}
