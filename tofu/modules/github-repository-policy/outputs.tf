output "ruleset_id" {
  description = "ID of the canonical default-branch ruleset."
  value       = github_repository_ruleset.canonical.id
}
