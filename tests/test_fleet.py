import importlib.util
import json
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("fleet", Path(__file__).parents[1] / "src/fleet.py")
fleet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet)


HEAD = "a" * 40
WORKFLOW_SHA = "b" * 40


def manifest():
    return {
        "schema": 1,
        "owner": {"login": "blogle", "id": 1},
        "repositories": [{"name": "anvil", "id": 2, "enabled": True}],
    }


def policy():
    return {
        "schema": 1,
        "provenance": {"source": "blogle/sdlc", "revision": HEAD},
        "security_floor": {
            "required_checks": ["sdlc-check", "sdlc-policy"],
            "protected_default_branch": True,
            "protected_release_tags": True,
            "no_force_push": True,
            "no_destructive_changes": True,
            "require_pull_requests": True,
            "squash_only": True,
            "dismiss_stale_reviews": True,
            "integration_path": "merge_queue",
            "trusted_publishers": {
                "sdlc-check": "blogle/sdlc",
                "sdlc-policy": "blogle/sdlc",
                "sdlc-verify": "blogle/sdlc",
            },
        },
        "bypass": {"kind": "personal_user", "user_id": 7, "actors": [7], "count": 1},
    }


def bound_policy():
    result = policy()
    result["security_floor"]["trusted_publishers"] = {
        name: {"app_id": 4181, "source": "github-actions", "workflow_path": ".github/workflows/sdlc.yml", "workflow_sha": WORKFLOW_SHA}
        for name in fleet.PR_REQUIRED_CHECKS + (fleet.QUEUE_CHECK,)
    }
    result["security_floor"]["workflow_attestation"] = {"protected_review": True, "workflow_path": ".github/workflows/sdlc.yml", "workflow_sha": WORKFLOW_SHA, "head_sha": HEAD}
    return result


class FleetContractTests(unittest.TestCase):
    def test_policy_requires_trusted_single_user(self):
        valid = policy()
        valid["security_floor"]["required_checks"] = ["sdlc-check", "sdlc-policy"]
        fleet.validate_policy(valid, trusted_bypass_user_id=7)
        with self.assertRaisesRegex(ValueError, "trusted fleet"):
            fleet.validate_policy(policy(), trusted_bypass_user_id=8)

    def test_manifest_rejects_missing_immutable_id(self):
        invalid = manifest()
        invalid["repositories"][0].pop("id")
        with self.assertRaisesRegex(ValueError, "immutable repository"):
            fleet.validate_manifest(invalid)

    def test_alias_migration_requires_new_checks_before_requirement(self):
        result = fleet.migration_plan([], [], phase="bootstrap")
        self.assertEqual(result["operations"][0]["kind"], "publish_wrappers")
        result = fleet.migration_plan(["sdlc / pr-fast"], fleet.ALL_CHECK_NAMES, phase="dual", exact_head=True)
        self.assertEqual(result["required_checks"], ["sdlc-check", "sdlc-policy"])
        self.assertTrue(result["retain_aliases"])
        with self.assertRaisesRegex(ValueError, "before they exist"):
            fleet.migration_plan([], ["sdlc-check"], phase="dual", exact_head=True)
        with self.assertRaisesRegex(ValueError, "in flight"):
            fleet.migration_plan([], [], repository="anvil", migration_state={"repository": "anvil", "generation": 2, "phase": "bootstrap", "in_flight": True})

    def test_plan_is_prepare_only_and_checks_head(self):
        result = fleet.plan(
            manifest(), policy(), trusted_bypass_user_id=7,
            verifier=type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "protected_repository_id": 1, "protected_ref": "refs/heads/main", "commit_sha": revision, "reachable_from_protected": True, "ancestry_verified": True, "immutable": True}})(),
            consumer_pin=HEAD, expected_head=HEAD, phase="bootstrap",
            current_checks=["sdlc / pr-fast"], available_checks=fleet.ALL_CHECK_NAMES,
        )
        self.assertTrue(result["prepare_only"])
        self.assertEqual(result["mutations"], [])
        with self.assertRaisesRegex(ValueError, "does not match"):
            fleet.plan(manifest(), policy(), trusted_bypass_user_id=7, verifier=type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "protected_repository_id": 1, "protected_ref": "refs/heads/main", "commit_sha": revision, "reachable_from_protected": True, "ancestry_verified": True, "immutable": True}})(), consumer_pin=HEAD,
                       expected_head="b" * 40, available_checks=fleet.ALL_CHECK_NAMES)

    def test_release_identity_and_policy_version_fail_closed(self):
        identity = fleet.verify_release_identity(
            expected_head=HEAD, actual_head=HEAD, expected_version="1.2.3",
            actual_version="1.2.3", source_main_sha=HEAD,
        )
        self.assertEqual(identity["version"], "1.2.3")
        with self.assertRaisesRegex(ValueError, "unsupported policy version"):
            fleet.check_policy_compatibility(2)
        with self.assertRaisesRegex(ValueError, "version changed"):
            fleet.verify_release_identity(
                expected_head=HEAD, actual_head=HEAD, expected_version="1.2.3",
                actual_version="1.2.4", source_main_sha=HEAD,
            )

    def test_generated_callers_are_central_and_pinned(self):
        callers = fleet.render_caller_workflows("v2.0.0")[".github/workflows/sdlc.yml"]
        self.assertIn("name: sdlc-check", callers)
        self.assertIn("name: sdlc / candidate", callers)
        self.assertIn("if: always()", callers)
        self.assertIn("stage: verify", callers)

    def test_provenance_rejects_forged_fork_and_toctou(self):
        verifier = type("Verifier", (), {"verify": lambda self, source, revision: {"source": "evil/fork", "repository_id": 1, "commit_sha": revision, "reachable_from_protected": True, "ancestry_verified": True, "protected_ref": "refs/heads/main", "protected_repository_id": 1, "immutable": True}})()
        with self.assertRaisesRegex(ValueError, "incomplete source identity"):
            fleet.verify_provenance(policy()["provenance"], verifier=verifier, consumer_pin=HEAD)
        verifier = type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "commit_sha": "b" * 40, "reachable_from_protected": True, "ancestry_verified": True, "protected_ref": "refs/heads/main", "protected_repository_id": 1, "immutable": True}})()
        with self.assertRaisesRegex(ValueError, "differs"):
            fleet.verify_provenance(policy()["provenance"], verifier=verifier, consumer_pin=HEAD)
        verifier = type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "commit_sha": revision, "reachable_from_protected": True, "ancestry_verified": False, "protected_ref": "refs/heads/main", "protected_repository_id": 1, "immutable": True}})()
        with self.assertRaisesRegex(ValueError, "not proven"):
            fleet.verify_provenance(policy()["provenance"], verifier=verifier, consumer_pin=HEAD)

    def test_floor_rejects_queue_check_as_normal_required_context(self):
        invalid = policy()
        invalid["security_floor"]["required_checks"].append("sdlc-verify")
        invalid["security_floor"]["required_checks"].sort()
        with self.assertRaisesRegex(ValueError, "never require sdlc-verify"):
            fleet.validate_policy(invalid, trusted_bypass_user_id=7)

    def test_renderer_and_reconciler_are_injected_and_prepare_only(self):
        rendered = fleet.render_canonical_policy("main", bound_policy(), trusted_bypass_user_id=7)
        self.assertEqual(rendered["branch"]["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])
        self.assertEqual(rendered["branch"]["bypass_actors"], [{"actor_type": "User", "actor_id": 7, "bypass_mode": "always"}])
        self.assertEqual(rendered["release_tags"]["conditions"]["ref_name"]["include"], ["refs/tags/v*"])
        statuses = next(rule for rule in rendered["branch"]["rules"] if rule["type"] == "required_status_checks")["parameters"]["required_status_checks"]
        self.assertEqual(statuses[0]["integration_id"], 4181)
        self.assertNotIn("app_id", statuses[0])
        fixture = json.loads((Path(__file__).parent / "fixtures/fleet-ruleset.json").read_text())
        self.assertEqual(fixture["rules"][2]["parameters"]["required_status_checks"][0]["integration_id"], 4181)

        class Client:
            def read(self, observed):
                return {"rulesets": [], "workflow_digest": "old"}

        result = fleet.reconcile_repository(
            {"rulesets": rendered, "workflow_digest": "new"},
            {"state": "ready"}, client=Client(),
        )
        self.assertEqual(result["state"], "unsafe")
        self.assertEqual(result["mutations"], [])
        with self.assertRaisesRegex(ValueError, "disabled"):
            fleet.reconcile_repository({}, {}, client=Client(), apply=True)

    def test_ruleset_readback_rejects_app_or_role_bypass(self):
        desired = fleet.render_canonical_policy("main", bound_policy(), trusted_bypass_user_id=7)["branch"]
        self.assertTrue(fleet.verify_ruleset_readback(desired, desired, trusted_bypass_user_id=7))
        forged = dict(desired)
        forged["bypass_actors"] = [{"actor_type": "OrganizationAdmin", "actor_id": 1, "bypass_mode": "always"}]
        with self.assertRaisesRegex(ValueError, "bypass"):
            fleet.verify_ruleset_readback(forged, desired, trusted_bypass_user_id=7)

    def test_codeowner_policy_is_sensitive_only_for_dotfiles(self):
        ordinary = bound_policy()
        fleet.validate_policy(ordinary, trusted_bypass_user_id=7)
        dotfiles = bound_policy()
        dotfiles.update({"repository_kind": "dotfiles", "codeowner_protection": {"required": True, "sensitive_paths": ["/.github/workflows/fleet.yml", "/fleet/policy.json"]}})
        rendered = fleet.render_canonical_policy("main", dotfiles, trusted_bypass_user_id=7)
        pull_request = next(rule for rule in rendered["branch"]["rules"] if rule["type"] == "pull_request")
        self.assertFalse(next(rule for rule in fleet.render_canonical_policy("main", ordinary, trusted_bypass_user_id=7)["branch"]["rules"] if rule["type"] == "pull_request")["parameters"]["require_code_owner_review"])
        self.assertTrue(pull_request["parameters"]["require_code_owner_review"])

    def test_publishers_require_observed_actions_app_and_reject_spoof(self):
        checks = [{"name": name, "app": {"id": 4181, "slug": "github-actions"}, "workflow_path": ".github/workflows/sdlc.yml", "workflow_sha": WORKFLOW_SHA, "head_sha": HEAD, "run_id": 1} for name in fleet.PR_REQUIRED_CHECKS + (fleet.QUEUE_CHECK,)]
        self.assertEqual(len(fleet.verify_check_publishers(checks, bound_policy()["security_floor"])), 3)
        checks[0]["app"] = {"id": 999, "slug": "github-actions"}
        with self.assertRaisesRegex(ValueError, "spoofed"):
            fleet.verify_check_publishers(checks, bound_policy()["security_floor"])
        with self.assertRaisesRegex(ValueError, "blocked"):
            fleet.verify_check_publishers(checks, policy()["security_floor"])
        checks[0]["app"] = {"id": 4181, "slug": "github-actions"}
        checks[0]["workflow_path"] = ".github/workflows/other.yml"
        with self.assertRaisesRegex(ValueError, "workflow source"):
            fleet.verify_check_publishers(checks, bound_policy()["security_floor"])

    def test_release_app_environment_contract_is_separate_from_fleet(self):
        release = {"app_kind": "sdlc-release", "installation_verified": True, "permissions": {"contents": "write", "pull_requests": "write", "checks": "write", "actions": "write", "environments": "write"}}
        self.assertTrue(fleet.release_credential_readiness(release)["ready"])
        with self.assertRaisesRegex(ValueError, "Environments:write"):
            fleet.release_credential_readiness({**release, "permissions": {**release["permissions"], "environments": "read"}})
        self.assertTrue(fleet.verify_release_workflow_binding({"jobs": {"release": {"environment": "release", "secrets": "inherit"}}}))
        with self.assertRaisesRegex(ValueError, "bind its protected environment"):
            fleet.verify_release_workflow_binding({"jobs": {"release": {"secrets": "inherit"}}})

    def test_identity_credentials_and_state_machine_fail_closed(self):
        self.assertEqual(fleet.verify_identity(1, 2, {"owner_id": 1, "repository_id": 2, "default_branch": "main"})["default_branch"], "main")
        with self.assertRaisesRegex(ValueError, "replaced"):
            fleet.verify_identity(1, 2, {"owner_id": 1, "repository_id": 3, "default_branch": "main"})
        ready = fleet.credential_readiness({"permissions": {"administration": "write", "actions_secrets": "write", "actions_variables": "write", "contents": "read"}, "installation_verified": True, "generation": 4})
        self.assertEqual(ready["generation"], 4)
        self.assertEqual(fleet.next_state("active", "transient"), "active")
        self.assertEqual(fleet.next_state("active", "unsafe"), "unsafe")


if __name__ == "__main__":
    unittest.main()
